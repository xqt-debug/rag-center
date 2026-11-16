"""图片格式与解码边界；所有处理都读取副本，绝不重编码用户原文件。"""

import io
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .errors import DomainError, require

IMAGE_TYPES = {
    ".png": ("PNG", "image/png"),
    ".jpg": ("JPEG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
    ".gif": ("GIF", "image/gif"),
    ".webp": ("WEBP", "image/webp"),
    ".bmp": ("BMP", "image/bmp"),
}
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_FRAMES = 300
MAX_DECODED_PIXELS = 120_000_000


def is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_TYPES


def image_media_type(filename: str) -> str:
    return IMAGE_TYPES[Path(filename).suffix.lower()][1]


def validate_image(filename: str, body: bytes) -> None:
    """检查真实格式和全部帧，限制压缩小文件展开后的内存与计算量。"""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as image:
                require(
                    image.format == IMAGE_TYPES[Path(filename).suffix.lower()][0],
                    "invalid_image",
                    "图片内容与文件扩展名不匹配",
                )
                frames = getattr(image, "n_frames", 1)
                pixels = image.width * image.height
                require(
                    pixels <= MAX_IMAGE_PIXELS
                    and frames <= MAX_IMAGE_FRAMES
                    and pixels * frames <= MAX_DECODED_PIXELS,
                    "image_limit",
                    "图片超过处理上限：单帧 2000 万像素、300 帧、累计 1.2 亿像素",
                )
                decoded = 0
                for index in range(frames):
                    image.seek(index)
                    frame_pixels = image.width * image.height
                    decoded += frame_pixels
                    require(
                        frame_pixels <= MAX_IMAGE_PIXELS and decoded <= MAX_DECODED_PIXELS,
                        "image_limit",
                        "图片解码后的像素量超过处理上限",
                    )
                    image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        EOFError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise DomainError("invalid_image", "图片损坏或无法安全解码，请更换文件") from None
