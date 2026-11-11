import hashlib
import math
import re
from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    dimensions: int
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

class HashEmbeddingProvider(EmbeddingProvider):
    """Offline deterministic feature-hashing bag-of-words embedding."""
    def __init__(self, dimensions: int = 384):
        if dimensions <= 0:
            raise ValueError('dimensions must be positive')
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9']+", text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, 'big')
            vector[value % self.dimensions] += 1.0 if value & 1 else -1.0
        norm = math.sqrt(sum(x*x for x in vector)) or 1.0
        return [x / norm for x in vector]

class FastEmbedProvider(EmbeddingProvider):
    def __init__(self, model_name: str):
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError('Install fastembed to use EMBEDDING_BACKEND=fastembed') from exc
        self._model = TextEmbedding(model_name=model_name)
        probe = next(iter(self._model.embed(["dimension probe"])))
        self.dimensions = len(probe)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [item.tolist() for item in self._model.embed(texts)]
