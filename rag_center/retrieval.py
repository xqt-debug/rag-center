import hashlib
import math
import re

import httpx
import jieba

from .errors import DomainError, require
from .ingestion import CHUNK_PROFILE


def tokenize(text):
    words = []
    for piece in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*|[\u3400-\u9fff]+", text.lower()):
        if re.search(r"[\u3400-\u9fff]", piece):
            words.extend(word.strip() for word in jieba.cut(piece) if word.strip())
        else:
            words.append(piece)
    return words


class Embedder:
    def __init__(self, settings):
        self.settings = settings
        endpoint = hashlib.sha256(settings.embedding_base_url.rstrip("/").encode()).hexdigest()[:12]
        self.profile = (
            f"{settings.embedding_provider}:{settings.embedding_model}:"
            f"{settings.embedding_dimensions}:{endpoint}:{CHUNK_PROFILE}"
        )

    def require_configured(self) -> None:
        require(
            self.settings.embedding_provider == "openai-compatible"
            and bool(self.settings.embedding_api_key.strip()),
            "embedding_not_configured",
            "尚未配置真实向量模型，请设置向量服务和 API 密钥后重试",
            503,
        )

    def embed(self, texts):
        if self.settings.embedding_provider == "local-test":
            # 仅用于开发验证的可重复测试向量，不具备真实语义理解能力。
            result = []
            for text in texts:
                vector = [0.0] * self.settings.embedding_dimensions
                for token in tokenize(text):
                    number = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
                    vector[number % len(vector)] += 1 if number & (1 << 32) else -1
                norm = math.sqrt(sum(v * v for v in vector)) or 1
                result.append([v / norm for v in vector])
            return result
        require(
            self.settings.embedding_provider == "openai-compatible",
            "embedding_configuration",
            "向量服务类型未配置",
            503,
        )
        self.require_configured()
        vectors = []
        try:
            with httpx.Client(timeout=60) as client:
                for start in range(0, len(texts), 10):
                    batch = texts[start : start + 10]
                    response = client.post(
                        self.settings.embedding_base_url.rstrip("/") + "/embeddings",
                        headers={"Authorization": f"Bearer {self.settings.embedding_api_key}"},
                        json={
                            "model": self.settings.embedding_model,
                            "input": batch,
                            "dimensions": self.settings.embedding_dimensions,
                        },
                    )
                    response.raise_for_status()
                    records = sorted(response.json()["data"], key=lambda row: row["index"])
                    require(
                        [r["index"] for r in records] == list(range(len(batch))),
                        "embedding_response",
                        "向量服务返回数量异常",
                        503,
                    )
                    for row in records:
                        vector = row["embedding"]
                        require(
                            len(vector) == self.settings.embedding_dimensions
                            and all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector),
                            "embedding_dimensions",
                            "向量维度或数值不匹配",
                            503,
                        )
                        vectors.append(vector)
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise DomainError("embedding_unavailable", "向量服务暂时不可用，请稍后重试", 503) from None
        return vectors


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1
    )
