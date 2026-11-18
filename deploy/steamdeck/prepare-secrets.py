"""将 GitHub Environment secrets 写入部署配置，不输出任何秘密值。"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


class SecretError(ValueError):
    """可安全显示的部署错误，只包含配置名称和原因。"""


def fail(message):
    raise SecretError(message)


def regular(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        fail(f"{path.name} must be a regular file")


def container_env(service):
    """只在内存中读取本项目容器的配置，确认长期凭据没有被意外轮换。"""
    name = f"rag-center-{service}-1"
    result = subprocess.run(["docker", "inspect", name], capture_output=True, text=True)
    if result.returncode:
        fail(f"Cannot verify the existing {service} container; keep its secret unchanged")
    try:
        data = json.loads(result.stdout)[0]
        labels = data["Config"]["Labels"]
        if (
            data["Name"] != f"/{name}"
            or labels.get("com.docker.compose.project") != "rag-center"
            or labels.get("com.docker.compose.service") != service
        ):
            fail(f"Unexpected {service} container identity")
        return dict(value.split("=", 1) for value in data["Config"]["Env"] if "=" in value)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        fail(f"Cannot verify the existing {service} container configuration")


def atomic_write(path, content):
    regular(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare(root):
    env_path, overrides = root / ".env", root / "app-secrets.env"
    regular(env_path)
    regular(overrides)
    # 专用 raw 文件保留 $、引号、反斜杠等字符，不经过 Compose 插值。
    values = {}
    if overrides.exists():
        for line in overrides.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or key not in {"RAG_SECRET", "RAG_EMBEDDING_API_KEY"}:
                fail("Unexpected entry in app-secrets.env")
            values[key] = value

    supplied = {
        key: os.environ.get(name, "")
        for key, name in (
            ("POSTGRES_PASSWORD", "DEPLOY_POSTGRES_PASSWORD"),
            ("RAG_SECRET", "DEPLOY_RAG_SECRET"),
            ("RAG_EMBEDDING_API_KEY", "DEPLOY_EMBEDDING_API_KEY"),
        )
    }
    for key, value in supplied.items():
        if any(character in value for character in "\r\n\x00"):
            fail(f"{key} must be a single-line secret")

    password = supplied["POSTGRES_PASSWORD"]
    if password:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", password):
            fail("POSTGRES_PASSWORD must use URL-safe characters")
        if password != container_env("db").get("POSTGRES_PASSWORD"):
            fail(
                "POSTGRES_PASSWORD differs from the existing database; password rotation is a separate operation"
            )

    signing = supplied["RAG_SECRET"]
    if signing:
        if len(signing) < 32 or signing.startswith("development"):
            fail("RAG_SECRET must be the existing production signing secret")
        if signing != container_env("api").get("RAG_SECRET"):
            fail(
                "RAG_SECRET differs from the existing application; preserve it to keep existing API keys valid"
            )
        values["RAG_SECRET"] = signing
    if supplied["RAG_EMBEDDING_API_KEY"]:
        values["RAG_EMBEDDING_API_KEY"] = supplied["RAG_EMBEDDING_API_KEY"]

    # 所有候选值校验通过后才写文件；未提供的 Secret 沿用原配置。
    if password:
        lines = env_path.read_text(encoding="utf-8").splitlines()
        lines = [line for line in lines if not re.match(r"^\s*(?:export\s+)?POSTGRES_PASSWORD\s*=", line)]
        lines.append(f"POSTGRES_PASSWORD={password}")
        atomic_write(env_path, "\n".join(lines) + "\n")
    atomic_write(
        overrides,
        "# Managed by GitHub Actions; raw env_file, do not quote values.\n"
        + "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
    )


if __name__ == "__main__":
    try:
        prepare(Path(sys.argv[1]))
    except SecretError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except (ValueError, OSError):
        # OS 或配置异常不回显内容，以免错误文本携带密钥。
        print(
            "Secret configuration failed; verify secret formats and existing deployment files.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
