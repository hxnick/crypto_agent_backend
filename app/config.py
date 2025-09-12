import os

class Settings:
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    DEFAULT_EXCHANGE: str = os.getenv("DEFAULT_EXCHANGE", "okx")
    HTTP_PROXY: str | None = os.getenv("HTTP_PROXY") or None
    HTTPS_PROXY: str | None = os.getenv("HTTPS_PROXY") or None

settings = Settings()
