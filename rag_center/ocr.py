"""本地 OCR：缓存推理模型，对动图按帧号均匀抽样并去除重复文本。"""

import threading
from functools import lru_cache

import numpy as np
from PIL import Image, ImageOps

from .errors import DomainError, require

MAX_OCR_FRAMES = 12
_engine_lock = threading.Lock()


@lru_cache(maxsize=1)
def _engine():
    # 随依赖包提供模型，首次使用时加载；不向外部 OCR 服务发送资料。
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)


def recognize_image(image: Image.Image) -> str:
    """透明背景铺白，仅缩放识别副本，保留原图的清晰度与动画。"""
    try:
        rgba = ImageOps.exif_transpose(image).convert("RGBA")
        rgb = Image.new("RGB", rgba.size, "white")
        rgb.paste(rgba, mask=rgba.getchannel("A"))
        rgb.thumbnail((2500, 2500), Image.Resampling.LANCZOS)
        with _engine_lock:
            result, _ = _engine()(np.array(rgb))
        return "\n".join(row[1].strip() for row in result or [] if row[2] >= 0.5 and row[1].strip())
    except Exception:
        # 推理库的原始异常可能含本地路径；只公开可操作的提示。
        raise DomainError("ocr_unavailable", "本地 OCR 识别失败，请检查依赖后重试", 503) from None


def frame_indices(count: int) -> list[int]:
    samples = min(count, MAX_OCR_FRAMES)
    return [round(i * (count - 1) / (samples - 1)) for i in range(samples)] if samples > 1 else [0]


def extract_image_text(body: bytes) -> list[tuple[int | None, str]]:
    import io

    pages, seen, length = [], set(), 0
    with Image.open(io.BytesIO(body)) as image:
        frames = getattr(image, "n_frames", 1)
        for index in frame_indices(frames):
            image.seek(index)
            text = recognize_image(image.copy())
            # 多帧常驻字幕只保存一次；仍保留首次出现的原始帧号供引用。
            identity = "".join(text.split())
            if not identity or identity in seen:
                continue
            seen.add(identity)
            length += len(text)
            require(length <= 2_000_000, "text_limit", "识别文字超过处理上限")
            pages.append((index + 1 if frames > 1 else None, text))
    return pages
