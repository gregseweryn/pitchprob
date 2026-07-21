"""Documentation-claim tests (audit 2026-07, finding A2/A9).

The project's brand is that published claims match what was measured.
football-data.co.uk's non-closing quotes are a Friday/Tuesday pre-match
snapshot, not the market open — so every surface that says "open" must
carry the early-snapshot disclaimer. These tests pin that contract the
same way payload disclaimers are pinned: as content, not decoration.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    """Read a doc with markdown line wrapping and blockquote markers
    collapsed, so phrase assertions survive re-wrapping."""
    raw = (ROOT / relative).read_text(encoding="utf-8")
    return " ".join(raw.replace("\n>", "\n").split())


def test_readme_carries_early_snapshot_caveat() -> None:
    text = _read("README.md")
    assert "early snapshot" in text
    assert "not the market open" in text
    # The disclaimer must state the source's actual capture timing.
    assert "Friday" in text and "Tuesday" in text


def test_readme_no_unqualified_opening_line_claim() -> None:
    """The load-bearing benchmark claim must not be phrased as the market
    open anywhere in the README prose."""
    text = _read("README.md")
    assert "opening line" not in text
    assert "opening price" not in text


def test_adr_0010_carries_terminology_correction() -> None:
    text = _read("docs/adr/0010-inference-engine-true-clv.md")
    assert "Terminology correction" in text
    assert "early snapshot" in text
    assert "not the market open" in text


def test_polish_readme_carries_early_snapshot_caveat() -> None:
    text = _read("README.pl.md")
    assert "wczesny snapshot" in text
    assert "nie jest otwarciem rynku" in text


def test_readme_adr_count_matches_docs() -> None:
    """Audit finding A9(c): the claimed ADR count must equal the files."""
    text = _read("README.md")
    match = re.search(r"(\d+) architecture decision records", text)
    assert match is not None, "README no longer states an ADR count"
    assert int(match.group(1)) == len(list((ROOT / "docs" / "adr").glob("*.md")))


def test_audit_latency_claim_carries_its_correction() -> None:
    """ADR 0014: the audit claimed the line-latency map was measurable from
    the existing tape. Measurement said otherwise, and the correction is
    payload — an audit whose refuted claims stay unmarked is a worse
    document than one that never made them."""
    text = _read("docs/AUDYT-2026-07.pl.md")
    assert "Sprostowanie (2026-07-21, ADR 0014)" in text
    assert "nieprawdziwe" in text
    # The three reasons must all survive re-editing, not just the verdict.
    assert "jeden** snapshot" in text
    assert "nie niesie żadnego polskiego bukmachera" in text
    assert "kwantuje opóźnienie do 24 h" in text


def test_adr_0014_states_the_corners_budget_and_the_feed_bar() -> None:
    """The two numbers that gate spending and trust must be in the ADR, not
    only in code: a credit budget nobody wrote down gets exceeded, and a
    validation bar chosen after seeing the data is not a bar."""
    text = _read("docs/adr/0014-corners-latency-map-and-pl-feed.md")
    assert "43 of the 500 monthly credits" in text
    assert "fixed in advance" in text
    assert "UNVERIFIED" in text


def test_adr_0015_thresholds_match_the_code() -> None:
    """The risk limits are only "fixed in advance" if the document and the
    code cannot drift apart. This test is the hinge between them."""
    from decimal import Decimal

    from pitchprob.betting.risk import DEFAULT_LIMITS

    text = _read("docs/adr/0015-risk-layer-and-weekly-report.md")
    assert DEFAULT_LIMITS.max_drawdown_pln == Decimal("75")
    assert DEFAULT_LIMITS.max_daily_stake_pln == Decimal("25")
    assert DEFAULT_LIMITS.max_match_stake_pln == DEFAULT_LIMITS.max_stake_pln
    for claim in ("2-5 PLN", "25 PLN", "75 PLN", "15 "):
        assert claim in text, f"ADR 0015 no longer states {claim!r}"
    # the reasoning that picked these numbers, not just the numbers
    assert "one bet per fixture" in text
    assert "Ruin is not a live risk" in text


def test_readme_test_counter_is_honest_and_current() -> None:
    """Audit finding A9(c): the test counter drifted 343→395 unnoticed.
    The README may never overstate the suite, and staleness is capped so
    the published number stays meaningful."""
    text = _read("README.md")
    claims = {int(n) for n in re.findall(r"(\d+) tests", text)}
    assert len(claims) == 1, f"README counter sites disagree: {sorted(claims)}"
    claimed = claims.pop()
    actual = 0
    for path in (ROOT / "tests").rglob("test_*.py"):
        actual += len(
            re.findall(r"^\s*def test_", path.read_text(encoding="utf-8"), re.MULTILINE)
        )
    assert claimed <= actual, f"README overstates the suite: {claimed} > {actual}"
    assert actual - claimed <= 50, (
        f"README test counter is stale: claims {claimed}, actual {actual}"
    )
