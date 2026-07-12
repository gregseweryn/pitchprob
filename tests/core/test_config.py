from pathlib import Path

import pytest

from pitchprob.core.config import Settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.database_url.startswith("postgresql+psycopg://")
    assert s.log_level == "INFO"
    assert s.data_dir == Path("./data")


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITCHPROB_LOG_LEVEL", "DEBUG")
    s = Settings(_env_file=None)
    assert s.log_level == "DEBUG"
