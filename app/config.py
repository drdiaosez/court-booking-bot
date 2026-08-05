from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./app.db"
    session_secret_key: str = "dev-only-insecure-secret-change-me"
    credential_encryption_key: str = ""
    default_timezone: str = "America/Los_Angeles"
    prewarm_lead_seconds: int = 90
    playwright_headless: bool = True
    screenshot_dir: str = "data/screenshots"


@lru_cache
def get_settings() -> Settings:
    return Settings()
