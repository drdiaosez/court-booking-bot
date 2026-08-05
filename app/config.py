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

    # Optional outbound proxy for every browser launch. Needed when the server's own IP is
    # a datacenter/hosting range that the facility's site (via Cloudflare or similar) blocks
    # outright -- see README "Datacenter IPs getting blocked". Leave proxy_server empty to
    # disable and connect directly, which is fine for facilities that don't block VPS IPs.
    proxy_server: str = ""
    proxy_username: str = ""
    proxy_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
