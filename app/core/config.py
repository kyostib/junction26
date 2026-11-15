from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _ROOT_DIR / ".env"


class Settings(BaseSettings):
    environment: str = "dev"  # "dev" | "prod"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/bsmeter"
    context_dev_api_key: str | None = None
    llm_api_key: str | None = None
    llm_provider: str = "mock"
    max_claims_to_verify: int = 5
    max_tweets_to_analyze: int = 10
    # Web origins are opt-in. The browser extension uses its service worker and
    # therefore does not need a permissive CORS policy.
    allowed_origins: list[str] = []

    @property
    def is_dev(self) -> bool:
        return self.environment.lower() == "dev"

    @property
    def is_prod(self) -> bool:
        return self.environment.lower() == "prod"

    model_config = SettingsConfigDict(
        env_file=(".env", str(_ENV_FILE)),
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
