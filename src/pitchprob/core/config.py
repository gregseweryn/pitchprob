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
    #: api-sports.io key (free tier = research instrument; see ADR 0008)
    api_football_key: str | None = None
    #: the-odds-api.com key (live odds tape; 500 credits/month — ADR 0012)
    odds_api_key: str | None = None
    #: odds-api.io key (Polish-book feed; free tier = 2 books, 100 req/h —
    #: ADR 0014). Optional: everything works without it, with manual entry.
    odds_api_io_key: str | None = None
    #: Telegram bot credentials for the speaking loop (ADR 0016). Both must
    #: be set for `pitchprob watch` to push; without them it prints to stdout.
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    #: healthchecks.io ping URL for the watch loop — pinged after each pass so
    #: a silently dead loop raises an alarm. Optional; no URL, no ping.
    healthchecks_watch_url: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
