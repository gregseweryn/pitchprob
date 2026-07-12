"""Application configuration.

Settings load from environment variables with the ``PITCHPROB_`` prefix and,
in development, from a local ``.env`` file. Access via :func:`get_settings`,
which caches a single instance per process.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PITCHPROB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://pitchprob:pitchprob@localhost:5433/pitchprob"
    log_level: str = "INFO"
    data_dir: Path = Path("./data")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
