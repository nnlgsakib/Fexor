from functools import lru_cache
from pathlib import Path

from fexor_core.crypto import load_key
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FEXOR_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://fexor:fexor@localhost:5432/fexor"
    # 32-byte base64 key for biometric/secret encryption at rest. Generate with
    # `fexor-server gen-key`. Losing it makes stored faces and 2FA secrets unreadable.
    data_key: str
    secure_cookies: bool = True
    session_hours: int = 12
    web_dir: Path | None = None  # built admin SPA (web/admin/dist)
    max_upload_mb: int = 50
    login_max_failures: int = 5
    login_lock_minutes: int = 15

    @property
    def key(self) -> bytes:
        return load_key(self.data_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
