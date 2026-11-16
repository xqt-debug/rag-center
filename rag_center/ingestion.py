"""独立的文档解析与分块逻辑，不依赖任务租约或数据库写入。"""

import io
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from .errors import require
from .media import is_image, validate_image
from .ocr import extract_image_text

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
CHUNK_PROFILE = "split800-120-v1"
MAX_TEXT_CHARACTERS = 2_000_000
MAX_PDF_PAGES = 500
MAX_CHUNKS = 5000

PageText = tuple[int | None, str]


@dataclass(frozen=True)
class ParsedDocument:
    pages: list[PageText]
    page_count: int

    @property
    def text(self) -> str:
        return "\n\n".join(content for _, content in self.pages)


def parse_document(path: str, filename: str) -> ParsedDocument:
    """保留 PDF 的物理页码，即使空白页未提取出文本也不重新编号。"""
    body = Path(path).read_bytes()
    if is_image(filename):
        validate_image(filename, body)
        return ParsedDocument(extract_image_text(body), page_count=0)
    if Path(filename).suffix.lower() != ".pdf":
        text = body.decode("utf-8-sig")
        require(text.strip(), "empty_document", "文件没有可检索文本")
        require(len(text) <= MAX_TEXT_CHARACTERS, "text_limit", "提取文本超过处理上限")
        return ParsedDocument([(None, text)], page_count=0)

    reader = PdfReader(io.BytesIO(body))
    require(not reader.is_encrypted, "encrypted_pdf", "暂不支持加密 PDF，请解密后上传")
    require(len(reader.pages) <= MAX_PDF_PAGES, "page_limit", "PDF 超过 500 页处理上限")
    pages, length = [], 0
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        length += len(text)
        require(length <= MAX_TEXT_CHARACTERS, "text_limit", "提取文本超过处理上限")
        if text.strip():
            pages.append((number, text))
    require(pages, "ocr_required", "PDF 没有可提取文本，扫描件需要先进行 OCR")
    return ParsedDocument(pages, page_count=len(reader.pages))


def chunk_pages(pages: list[PageText]) -> list[PageText]:
    """按页分别分块，确保一条引用不会跨越两个 PDF 物理页面。"""
    pieces = []
    for page, content in pages:
        for start in range(0, len(content), CHUNK_SIZE - CHUNK_OVERLAP):
            value = content[start : start + CHUNK_SIZE].strip()
            if value:
                pieces.append((page, value))
            if start + CHUNK_SIZE >= len(content):
                break
    require(len(pieces) <= MAX_CHUNKS, "chunk_limit", "文本块数量超过处理上限")
    return pieces
