"""显式声明的共享依赖，不保存请求状态，也不负责业务事务提交。"""

from dataclasses import dataclass

from itsdangerous import URLSafeTimedSerializer

from ..config import Settings
from ..db import Database
from ..retrieval import Embedder
from .access import AccessPolicy
from .quota import QuotaPolicy
from .storage import FileStorage


@dataclass(frozen=True)
class Resources:
    db: Database
    settings: Settings
    embedder: Embedder
    signer: URLSafeTimedSerializer
    access: AccessPolicy
    quota: QuotaPolicy
    storage: FileStorage


class Operations:
    """仅提供公共依赖，领域类之间不通过隐式方式相互调用。"""

    def __init__(self, resources: Resources):
        self.db = resources.db
        self.settings = resources.settings
        self.embedder = resources.embedder
        self.signer = resources.signer
        self.access = resources.access
        self.quota = resources.quota
        self.storage = resources.storage
