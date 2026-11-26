from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingBackend(str, Enum):
    HASH = "hash"
    FASTEMBED = "fastembed"


class AnswerProviderName(str, Enum):
    CONTEXT = "context"
    OPENAI = "openai"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    qdrant_url: str | None = None
    qdrant_path: str = "./data/qdrant"
    qdrant_collection: str = "support_documents"
    qdrant_startup_attempts: int = Field(default=5, ge=1, le=30)
    qdrant_retry_seconds: float = Field(default=0.5, ge=0, le=30)
    database_path: str = "./data/supportops.db"
    embedding_backend: EmbeddingBackend = EmbeddingBackend.HASH
    embedding_dimensions: int = Field(default=384, ge=1, le=65536)
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    answer_provider: AnswerProviderName = AnswerProviderName.CONTEXT
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = Field(default=30, gt=0, le=300)
    openai_max_retries: int = Field(default=1, ge=0, le=5)
    openai_max_output_tokens: int = Field(default=500, ge=1, le=16_384)
    api_key: str | None = None
    webhook_secret: str | None = None
    allow_unsigned_webhooks_dev: bool = False
    app_environment: str = Field(default="development", pattern="^(development|production)$")
    enable_api_docs: bool = True
    max_request_body_bytes: int = Field(default=262_144, ge=1024, le=10_485_760)
    webhook_lease_seconds: float = Field(default=60, ge=0.01, le=3600)
    low_confidence_threshold: float = Field(default=0.20, ge=0, le=1)
    chunk_size: int = Field(default=120, ge=1, le=10000)
    chunk_overlap: int = Field(default=20, ge=0, le=9999)

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if self.app_environment == "production" and self.allow_unsigned_webhooks_dev:
            raise ValueError(
                "ALLOW_UNSIGNED_WEBHOOKS_DEV cannot be enabled in production"
            )
        if self.answer_provider == AnswerProviderName.OPENAI and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when ANSWER_PROVIDER=openai")
        return self

    def ensure_data_dir(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        if not self.qdrant_url:
            Path(self.qdrant_path).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
