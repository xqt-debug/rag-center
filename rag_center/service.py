"""HTTP 与 MCP 共用的应用入口。

只有此入口负责提交业务事务。领域操作接收已有数据库会话，不能自行提交，
确保业务变更、审计记录和幂等回执要么一起保存，要么一起回滚。
"""

import hashlib
import json

from itsdangerous import URLSafeTimedSerializer
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .actions import ACTIONS, ActionSpec
from .auth import Principal, digest, rate_limit
from .config import Settings
from .db import Database
from .errors import DomainError, require
from .models import ApiKey, Audit, Job, Operation, User
from .retrieval import Embedder
from .schemas import Input
from .services.access import AccessPolicy
from .services.accounts import AccountOperations
from .services.documents import DocumentOperations
from .services.knowledge_bases import KnowledgeBaseOperations
from .services.quota import QuotaPolicy
from .services.resources import Resources
from .services.search import SearchOperations
from .services.storage import FileStorage
from .services.transfers import TransferOperations


class Service:
    """执行顺序：参数校验 → 认证与授权 → 重放回执或执行操作 → 审计 → 提交。"""

    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings
        self.embedder = Embedder(settings)
        self.access = AccessPolicy()
        resources = Resources(
            database,
            settings,
            self.embedder,
            URLSafeTimedSerializer(settings.secret, salt="rag-file-transfers-v1"),
            self.access,
            QuotaPolicy(settings),
            FileStorage(settings),
        )
        self.transfers = TransferOperations(resources)
        groups = {
            "knowledge_bases": KnowledgeBaseOperations(resources),
            "documents": DocumentOperations(resources),
            "transfers": self.transfers,
            "search": SearchOperations(resources),
            "accounts": AccountOperations(resources),
        }
        # 启动时解析并绑定处理方法，不对请求中的任意操作名直接执行 getattr。
        # 若注册项缺少实现，应在启动阶段立即暴露问题。
        self.handlers = {name: getattr(groups[spec.group], name) for name, spec in ACTIONS.items()}

    def run(
        self, principal: Principal, action: str, payload: dict | None = None, request_id: str | None = None
    ) -> dict:
        spec = ACTIONS.get(action)
        require(spec is not None, "unknown_action", "未知操作", 404)
        args = self._validate(spec, payload)
        with self.db.sessions() as session:
            # 限流器会在业务事务开始前单独提交计数。
            # 不能移到资源变更之后，否则可能提前提交部分业务修改。
            rate_limit(session, f"actions:{principal.actor_id}", 180, 60)
            if spec.write:
                # 后台发布同样按照“用户 → 文件 → 任务”的顺序加锁。
                # 用户行锁也会串行处理该用户所有密钥发起的配额预留。
                session.scalar(select(User).where(User.id == principal.user_id).with_for_update())
            key = self.access.resolve_key(session, principal)
            operation_id, fingerprint, cached = self._receipt(
                session, principal, action, spec, args, request_id
            )
            self._authorize(session, principal, key, action, spec, args, replay=cached is not None)
            if cached is not None:
                self._check_replay_target(session, principal, key, action, cached.result)
                return cached.result

            result = self.handlers[action](session, principal, key, args)
            if spec.write:
                session.add(
                    Audit(
                        user_id=principal.user_id,
                        actor_id=principal.actor_id,
                        action=action,
                        target_id=result.get("id", result.get("document_id", "")),
                    )
                )
                if operation_id is not None:
                    session.add(Operation(id=operation_id, fingerprint=fingerprint, result=result))
            session.commit()
            return result

    @staticmethod
    def _validate(spec: ActionSpec, payload: dict | None) -> Input:
        try:
            return spec.model.model_validate(payload or {})
        except ValidationError as exc:
            # 校验错误可能包含完整输入，因此这里只返回字段名。
            # 避免文档正文和供应商凭证进入接口层错误日志。
            fields = sorted({".".join(map(str, error["loc"])) or "request" for error in exc.errors()})
            raise DomainError("invalid_input", "参数不符合要求：" + ", ".join(fields), 422) from None

    def _receipt(
        self,
        session: Session,
        principal: Principal,
        action: str,
        spec: ActionSpec,
        args: Input,
        request_id: str | None,
    ) -> tuple[str | None, str | None, Operation | None]:
        if not request_id or not spec.cacheable:
            return None, None, None
        require(len(request_id) <= 160, "invalid_input", "请求标识过长")
        operation_id = hashlib.sha256(f"{principal.actor_id}:{request_id}".encode()).hexdigest()
        fingerprint = digest(
            json.dumps([action, args.model_dump()], sort_keys=True, ensure_ascii=False), self.settings.secret
        )
        cached = session.get(Operation, operation_id)
        require(
            cached is None or cached.fingerprint == fingerprint,
            "idempotency_conflict",
            "请求标识已用于其他操作",
            409,
        )
        return operation_id, fingerprint, cached

    def _authorize(
        self,
        session: Session,
        principal: Principal,
        key: ApiKey | None,
        action: str,
        spec: ActionSpec,
        args: Input,
        replay: bool,
    ) -> None:
        if spec.web_only:
            require(key is None, "forbidden", "此操作需要网页登录", 403)
            return
        scope = spec.scope
        if action == "prepare_document_upload" and args.document_id:
            scope = "update"
        self.access.require_scope(key, scope)
        # 删除回执不包含文档正文；即使删除操作已经移除了知识库授权，
        # 仍可重复返回回执，但必须继续校验凭证与操作权限。
        if replay and action.startswith("delete_"):
            return
        knowledge_base_id = getattr(args, "knowledge_base_id", None)
        if knowledge_base_id:
            self.access.knowledge_base(session, principal, key, knowledge_base_id)
        document_id = getattr(args, "document_id", None)
        if document_id:
            document = self.access.document(session, principal, key, document_id)
            if action == "prepare_document_upload":
                require(document.kb_id == knowledge_base_id, "invalid_input", "上传目标知识库不匹配")
        if action == "get_ingestion_job":
            job = session.get(Job, args.job_id)
            require(job is not None, "not_found", "处理任务不存在或不可访问", 404)
            self.access.document(session, principal, key, job.document_id)

    def _check_replay_target(
        self, session: Session, principal: Principal, key: ApiKey | None, action: str, result: dict
    ) -> None:
        # 重放创建请求时，不能恢复对后来已删除资源的访问。
        if action == "create_knowledge_base":
            self.access.knowledge_base(session, principal, key, result["id"])
        elif action == "create_document":
            self.access.document(session, principal, key, result["id"])

    def receive_upload(self, token: str, body: bytes) -> dict:
        return self.transfers.receive_upload(token, body)

    def resolve_download(self, token: str) -> tuple[str, str]:
        return self.transfers.resolve_download(token)
