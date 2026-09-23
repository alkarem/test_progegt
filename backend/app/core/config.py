"""إعدادات التطبيق من متغيرات البيئة (البادئة GBCFMS_)."""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-change-me-dev-only-secret-change-me"  # noqa: S105 (مرفوض في الإنتاج)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GBCFMS_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+psycopg://gbcfms:gbcfms@localhost:5432/gbcfms"
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_hours: int = 8
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    storage_dir: str = "storage"
    max_upload_mb: int = 20
    backup_key: str | None = None   # base64 لمفتاح AES-256 (32 بايت)؛ بدونه تُحفظ النسخ غير مشفرة مع تحذير

    @model_validator(mode="after")
    def _production_guard(self):
        if self.env == "prod" and (self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32):
            raise ValueError("GBCFMS_JWT_SECRET يجب ضبطه بقيمة عشوائية (32 حرفًا على الأقل) في الإنتاج")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
