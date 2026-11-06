import os
from functools import cached_property, lru_cache
from pathlib import Path

from fexor_core.crypto import load_key, new_key_b64
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FEXOR_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://fexor:fexor@localhost:5432/fexor"
    # 32-byte base64 key for biometric/secret encryption at rest. If unset, one is created in
    # data_dir/data.key on first start. Losing it makes stored faces and 2FA secrets unreadable.
    data_key: str | None = None
    data_dir: Path = Path("data")
    # Address kiosks use to reach this server, e.g. http://192.168.1.20 (shown in pairing QR).
    public_url: str | None = None
    secure_cookies: bool = True
    session_hours: int = 12
    web_dir: Path | None = None  # built admin SPA (web/admin/dist)
    max_upload_mb: int = 50
    login_max_failures: int = 5
    login_lock_minutes: int = 15

    @cached_property
    def key(self) -> bytes:
        if self.data_key:
            return load_key(self.data_key)
        path = self.data_dir / "data.key"
        if not path.exists():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(new_key_b64())
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return load_key(path.read_text().strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
