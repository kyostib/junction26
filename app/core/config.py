from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./bs_meter.db"
    context_dev_api_key: str | None = None
    llm_api_key: str | None = None
    llm_provider: str = "mock"
    max_claims_to_verify: int = 5
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
