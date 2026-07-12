import os

# Deterministic settings for tests; individual tests may override via monkeypatch.
os.environ.setdefault(
    "PITCHPROB_DATABASE_URL",
    "postgresql+psycopg://pitchprob:pitchprob@localhost:5433/pitchprob",
)
