"""本地文件校验；存储路径由服务端生成，不能直接使用客户端文件名。"""

from pathlib import Path

from ..config import Settings
from ..errors import DomainError, require


class FileStorage:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.storage_dir.mkdir(parents=True, exist_ok=True)

    def validate_filename(self, name: str) -> str:
        require(
            name.strip() and not any(character in name for character in "\\/\x00\r\n"),
            "invalid_filename",
            "文件名称无效",
        )
        ext = Path(name).suffix.lower()
        require(ext in {".txt", ".md", ".pdf"}, "unsupported_type", "支持 TXT、Markdown 和文本 PDF")
        return ext

    def validate_content(self, name: str, body: bytes) -> None:
        ext = self.validate_filename(name)
        require(
            0 < len(body) <= self.settings.max_file_mb * 1024**2, "file_too_large", "文件为空或超过限制", 413
        )
        if ext == ".pdf":
            require(body.lstrip().startswith(b"%PDF-"), "invalid_file", "文件不是有效 PDF")
        else:
            try:
                body.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise DomainError("invalid_encoding", "文本文件请使用 UTF-8 编码") from None
            require(b"\x00" not in body, "invalid_file", "文本文件含有二进制内容")
