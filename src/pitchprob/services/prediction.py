"""Market-book construction: one fixture → every market probability.

This is the application service shared by the CLI and the API. It trains
Dixon-Coles and Elo on the league's full history at request time (a few
seconds — acceptable for M1; a fitted-model cache keyed by league and data
version is the designated M2 optimization) and derives all goal markets from
the score matrix per ADR 0002.

Asian handicap entries report, per line, the probability that a bet on each
side wins (fully or half) plus the push probability.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from pitchprob.betting.staking import expected_value, kelly_fraction
from pitchprob.core.errors import UnknownTeamError
from pitchprob.data.dataset import load_matches_frame
from pitchprob.markets import (
    asian_handicap,
    btts,
    double_chance,
    draw_no_bet,
    expected_goals,
    match_odds,
    totals,
)
from pitchprob.models.dixon_coles import DixonColesModel
from pitchprob.models.elo import EloModel

DISCLAIMER = (
    "Probabilities are model estimates with uncertainty; they are routinely "
    "worse than closing-line odds and nothing here guarantees profit."
)

_TOTALS_LINES = ("0.5", "1.5", "2.5", "3.5", "4.5")
_AH_LINES = ("-2", "-1.5", "-1", "-0.5", "-0.25", "0", "0.25", "0.5", "1", "1.5", "2")
_TOP_SCORES = 5

#: Default Dixon-Coles half-life; ~390 days is the common sweet spot reported
#: in the club-football literature (recent form matters, but one season of
#: signal should not evaporate).
DEFAULT_HALF_LIFE_DAYS = 390.0


def build_market_book(
    session: Session,
    league_code: str,
    home_team: str,
    away_team: str,
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    offered_1x2: tuple[float, float, float] | None = None,
) -> dict[str, Any]:
    frame = load_matches_frame(session, league_code=league_code)
    if frame.empty:
        raise ValueError(f"no ingested matches for league {league_code!r}")
    known_teams = set(frame["home_team"]) | set(frame["away_team"])
    for team in (home_team, away_team):
        if team not in known_teams:
            raise UnknownTeamError(
                f"{team!r} has no matches in league {league_code!r}; "
                "use the canonical team name"
            )

    dc = DixonColesModel(half_life_days=half_life_days).fit(frame)
    elo = EloModel().fit(frame)

    matrix = dc.score_matrix(home_team, away_team)
    mo = match_odds(matrix)
    dc_book = double_chance(matrix)
    dnb = draw_no_bet(matrix)
    both = btts(matrix)
    goals = expected_goals(matrix)
    elo_p = elo.match_probabilities(home_team, away_team)

    top_scores = sorted(
        (
            (h, a, float(matrix[h, a]))
            for h in range(matrix.shape[0])
            for a in range(matrix.shape[1])
        ),
        key=lambda item: item[2],
        reverse=True,
    )[:_TOP_SCORES]

    ah_book: dict[str, dict[str, float]] = {}
    for line_text in _AH_LINES:
        line = Decimal(line_text)
        home_side = asian_handicap(matrix, line, "home")
        away_side = asian_handicap(matrix, line, "away")
        ah_book[line_text] = {
            "home": home_side.full_win + home_side.half_win,
            "push": home_side.push,
            "away": away_side.full_win + away_side.half_win,
        }

    book: dict[str, Any] = {
        "league": league_code,
        "home_team": home_team,
        "away_team": away_team,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "trained_on_matches": len(frame),
        "train_max_date": str(frame["date"].max()),
        "expected_goals": {"home": goals.home, "away": goals.away},
        "markets": {
            "1x2": {
                "dixon_coles": {"home": mo.home, "draw": mo.draw, "away": mo.away},
                "elo": {"home": elo_p.home, "draw": elo_p.draw, "away": elo_p.away},
            },
            "double_chance": {
                "home_or_draw": dc_book.home_or_draw,
                "home_or_away": dc_book.home_or_away,
                "draw_or_away": dc_book.draw_or_away,
            },
            "draw_no_bet": {"home": dnb.home, "away": dnb.away},
            "totals": {
                line: {
                    "over": totals(matrix, Decimal(line)).over,
                    "under": totals(matrix, Decimal(line)).under,
                }
                for line in _TOTALS_LINES
            },
            "btts": {"yes": both.yes, "no": both.no},
            "asian_handicap": ah_book,
            "correct_score_top": [
                {"score": f"{h}-{a}", "probability": p} for h, a, p in top_scores
            ],
        },
        "disclaimer": DISCLAIMER,
    }

    if offered_1x2 is not None:
        probabilities = {"home": mo.home, "draw": mo.draw, "away": mo.away}
        prices = dict(zip(("home", "draw", "away"), offered_1x2, strict=True))
        book["value_analysis"] = {
            selection: {
                "offered_price": price,
                "model_probability": probabilities[selection],
                "fair_price": 1.0 / probabilities[selection],
                "expected_value": expected_value(
                    probability=probabilities[selection], price=price
                ),
                "kelly_fraction": kelly_fraction(
                    probability=probabilities[selection], price=price, fraction=0.25
                ),
            }
            for selection, price in prices.items()
        }

    return book
