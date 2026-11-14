from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "dev"  # "dev" | "prod"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/bsmeter"
    context_dev_api_key: str | None = None
    llm_api_key: str | None = None
    llm_provider: str = "mock"
    max_claims_to_verify: int = 5
    allowed_origins: list[str] = ["*"]

    @property
    def is_dev(self) -> bool:
        return self.environment.lower() == "dev"

    @property
    def is_prod(self) -> bool:
        return self.environment.lower() == "prod"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
