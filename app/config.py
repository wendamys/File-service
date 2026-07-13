from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация сервиса, читается из .env и переменных окружения."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    base_url: str = "http://91.199.149.128:18001"
    candidate_id: str | None = None
    downloads_dir: Path = Path("downloads")
    db_url: str = "sqlite:///file_service.db"

    request_interval: float = 1.5
    max_interval: float = 15.0
    backoff_factor: float = 1.5
    max_retries: int = 5
    timeout: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
