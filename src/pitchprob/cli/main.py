"""pitchprob CLI: ingest → train → predict → backtest.

Every command opens its own transactional session; ``_make_downloader`` is a
seam that tests replace with an offline fake.
"""

import json as json_lib
from datetime import UTC, date, datetime
from typing import Annotated, Any, cast

import httpx
import numpy as np
import typer

from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.betting.selection import select_value_bets
from pitchprob.core.config import get_settings
from pitchprob.core.db import session_scope
from pitchprob.core.errors import PitchprobError
from pitchprob.core.logging import configure_logging
from pitchprob.data.dataset import load_closing_odds_frame, load_matches_frame
from pitchprob.data.orm import Backtest, ModelRun
from pitchprob.data.service import LEAGUES, Downloader, HttpDownloader, IngestionService
from pitchprob.evaluation.backtest import run_backtest
from pitchprob.evaluation.metrics import (
    brier_score,
    expected_calibration_error,
    log_loss,
    ranked_probability_score,
)
from pitchprob.evaluation.staking import simulate_staking
from pitchprob.models.dixon_coles import DixonColesModel, IndependentPoissonModel
from pitchprob.models.elo import EloModel
from pitchprob.models.ensemble import EnsembleModel
from pitchprob.models.gbm import GbmModel
from pitchprob.services.prediction import DEFAULT_HALF_LIFE_DAYS, build_market_book

app = typer.Typer(
    name="pitchprob",
    help="Football probability engine: honest, backtested market estimates.",
    no_args_is_help=True,
)


def _make_downloader() -> Downloader:
    settings = get_settings()
    return HttpDownloader(cache_dir=settings.data_dir / "raw")


def _pct(value: float) -> str:
    return f"{100 * value:5.1f}%"


@app.command()
def ingest(
    league: Annotated[list[str] | None, typer.Option(help="League code, repeatable")] = None,
    all_leagues: Annotated[bool, typer.Option("--all", help="All configured leagues")] = False,
    from_year: Annotated[int, typer.Option(help="First season start year")] = 2014,
    to_year: Annotated[int, typer.Option(help="Last season start year")] = 2025,
) -> None:
    """Download and store seasons from football-data.co.uk (idempotent)."""
    configure_logging(get_settings().log_level)
    codes = list(LEAGUES) if all_leagues or not league else league
    downloader = _make_downloader()
    total_matches = 0
    with session_scope() as session:
        service = IngestionService(session=session, downloader=downloader)
        for code in codes:
            if code not in LEAGUES:
                typer.echo(f"unknown league code {code!r}; known: {', '.join(LEAGUES)}")
                raise typer.Exit(code=1)
            for year in range(from_year, to_year + 1):
                try:
                    report = service.ingest_season(code, year)
                except httpx.HTTPError as exc:
                    typer.echo(f"{code} {year}: download failed ({exc}); skipping")
                    continue
                total_matches += report.matches_inserted + report.matches_updated
                typer.echo(
                    f"{code} {year}: {report.matches_inserted} new matches, "
                    f"{report.matches_updated} updated, {report.odds_quotes} odds quotes, "
                    f"{report.quarantined} quarantined"
                )
    typer.echo(f"done: {total_matches} matches ingested/refreshed")


@app.command()
def xg(
    league: Annotated[list[str] | None, typer.Option(help="League code, repeatable")] = None,
    all_leagues: Annotated[bool, typer.Option("--all", help="All configured leagues")] = False,
    from_year: Annotated[int, typer.Option(help="First season start year")] = 2014,
    to_year: Annotated[int, typer.Option(help="Last season start year")] = 2025,
) -> None:
    """Attach Understat xG to already-ingested matches (idempotent)."""
    from pitchprob.data.adapters.understat import REQUIRED_HEADERS
    from pitchprob.data.xg_service import XgUpdateService

    configure_logging(get_settings().log_level)
    codes = list(LEAGUES) if all_leagues or not league else league
    downloader = HttpDownloader(
        cache_dir=get_settings().data_dir / "raw", headers=REQUIRED_HEADERS
    )
    with session_scope() as session:
        service = XgUpdateService(session=session, downloader=downloader)
        for code in codes:
            for year in range(from_year, to_year + 1):
                try:
                    report = service.update_league_season(code, year)
                except httpx.HTTPError as exc:
                    typer.echo(f"{code} {year}: download failed ({exc}); skipping")
                    continue
                unknown = ", ".join(sorted(report.unmatched_teams)) or "-"
                typer.echo(
                    f"{code} {year}: {report.matched}/{report.parsed} matches got xG, "
                    f"{report.unmatched_matches} unmatched (unknown teams: {unknown})"
                )


@app.command()
def train(
    league: Annotated[str, typer.Option(help="League code")] = "E0",
    half_life: Annotated[float, typer.Option(help="Time-decay half-life, days")] = (
        DEFAULT_HALF_LIFE_DAYS
    ),
) -> None:
    """Fit Dixon-Coles and Elo on a league; persist both as model runs."""
    configure_logging(get_settings().log_level)
    with session_scope() as session:
        frame = load_matches_frame(session, league_code=league)
        if frame.empty:
            typer.echo(f"no matches ingested for {league!r}; run `pitchprob ingest` first")
            raise typer.Exit(code=1)

        dc = DixonColesModel(half_life_days=half_life).fit(frame)
        elo = EloModel().fit(frame)
        cutoff = frame["date"].max()
        for name, params in (("dixon_coles", dc.get_params()), ("elo", elo.get_params())):
            session.add(
                ModelRun(
                    model_name=name,
                    params=params,
                    trained_at=datetime.now(tz=UTC),
                    train_cutoff=cutoff,
                )
            )

        typer.echo(f"trained dixon_coles + elo on {len(frame)} {league} matches")
        typer.echo(
            f"dixon_coles: home_adv={dc.params.home_adv:.3f} rho={dc.params.rho:.3f}"
        )
        top = sorted(dc.params.attack.items(), key=lambda kv: kv[1], reverse=True)[:6]
        typer.echo("top attack ratings:")
        for team, rating in top:
            typer.echo(f"  {team:<28} {rating:+.3f}")


@app.command()
def predict(
    home: Annotated[str, typer.Option(help="Home team (canonical name)")],
    away: Annotated[str, typer.Option(help="Away team (canonical name)")],
    league: Annotated[str, typer.Option(help="League code")] = "E0",
    odds: Annotated[
        str | None, typer.Option(help="Offered 1X2 prices, e.g. 2.05,3.4,3.6")
    ] = None,
    half_life: Annotated[float, typer.Option(help="Time-decay half-life, days")] = (
        DEFAULT_HALF_LIFE_DAYS
    ),
    as_json: Annotated[bool, typer.Option("--json", help="Raw JSON output")] = False,
) -> None:
    """Print the full market probability book for a fixture."""
    configure_logging(get_settings().log_level)
    offered = None
    if odds is not None:
        parts = [float(x) for x in odds.split(",")]
        if len(parts) != 3:
            typer.echo("--odds expects three prices: home,draw,away")
            raise typer.Exit(code=1)
        offered = (parts[0], parts[1], parts[2])

    try:
        with session_scope() as session:
            book = build_market_book(
                session, league, home, away,
                half_life_days=half_life, offered_1x2=offered,
            )
    except (PitchprobError, ValueError) as exc:
        typer.echo(f"error: {exc}")
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json_lib.dumps(book, indent=2))
        return

    markets: dict[str, Any] = book["markets"]
    eg = book["expected_goals"]
    typer.echo(f"\n{home} vs {away}  ({league}, trained on {book['trained_on_matches']} "
               f"matches to {book['train_max_date']})")
    typer.echo(f"expected goals: {eg['home']:.2f} - {eg['away']:.2f}\n")

    typer.echo("1X2                home   draw   away")
    for model_name in ("ensemble", "dixon_coles", "elo", "gbm"):
        p = markets["1x2"][model_name]
        typer.echo(
            f"  {model_name:<16} {_pct(p['home'])} {_pct(p['draw'])} {_pct(p['away'])}"
        )
    weights = ", ".join(f"{k}={v:.2f}" for k, v in book["ensemble_weights"].items())
    typer.echo(f"  stack weights: {weights}")

    dc_ = markets["double_chance"]
    typer.echo(f"\nDouble chance      1X {_pct(dc_['home_or_draw'])}   "
               f"12 {_pct(dc_['home_or_away'])}   X2 {_pct(dc_['draw_or_away'])}")
    dnb = markets["draw_no_bet"]
    typer.echo(f"Draw no bet        home {_pct(dnb['home'])}   away {_pct(dnb['away'])}")

    typer.echo("\nTotals             over   under")
    for line, ou in markets["totals"].items():
        typer.echo(f"  Over/Under {line:<4}  {_pct(ou['over'])} {_pct(ou['under'])}")

    btts = markets["btts"]
    typer.echo(f"\nBTTS               yes {_pct(btts['yes'])}   no {_pct(btts['no'])}")

    typer.echo("\nAsian handicap     home-wins  push   away-wins")
    for line, ah in markets["asian_handicap"].items():
        typer.echo(f"  AH {line:<6}       {_pct(ah['home'])}  {_pct(ah['push'])}  "
                   f"{_pct(ah['away'])}")

    typer.echo("\nMost likely scores:")
    for entry in markets["correct_score_top"]:
        typer.echo(f"  {entry['score']:<5} {_pct(entry['probability'])}")

    if "value_analysis" in book:
        typer.echo("\nValue vs offered odds (quarter-Kelly):")
        for selection, v in book["value_analysis"].items():
            typer.echo(
                f"  {selection:<5} price {v['offered_price']:.2f}  fair "
                f"{v['fair_price']:.2f}  Expected value {v['expected_value']:+.3f}  "
                f"kelly {v['kelly_fraction']:.3f}"
            )

    typer.echo(f"\n{book['disclaimer']}")


@app.command()
def coupon(
    tier: Annotated[str, typer.Option(help="safe | balanced | value | high_risk")] = "balanced",
    fixture: Annotated[
        list[str] | None,
        typer.Option(help='Explicit fixture "Home,Away,LEAGUE" (canonical names), repeatable'),
    ] = None,
    league: Annotated[
        list[str] | None, typer.Option(help="Restrict fixtures.csv to these leagues")
    ] = None,
    max_legs: Annotated[int, typer.Option(help="Maximum legs per coupon")] = 4,
    top: Annotated[int, typer.Option(help="Coupons to show per tier")] = 5,
    max_fixtures: Annotated[int, typer.Option(help="Fixture cap from fixtures.csv")] = 12,
) -> None:
    """Generate risk-tiered coupons with per-leg reasoning (ADR 0006)."""
    from pitchprob.data.adapters.football_data_co_uk import FIXTURES_URL, parse_fixtures_csv
    from pitchprob.data.normalize import canonical_team_name
    from pitchprob.services.coupons import INDEPENDENCE_CAVEAT, TIERS, generate_coupons

    configure_logging(get_settings().log_level)
    if tier not in TIERS:
        raise typer.BadParameter(f"unknown tier {tier!r}; expected one of {sorted(TIERS)}")

    entries: list[tuple[str, str, str]] = []
    if fixture:
        for spec in fixture:
            parts = [p.strip() for p in spec.split(",")]
            if len(parts) != 3:
                raise typer.BadParameter(f'fixture must be "Home,Away,LEAGUE", got {spec!r}')
            entries.append((parts[0], parts[1], parts[2]))
    else:
        raw = HttpDownloader(cache_dir=None).get(FIXTURES_URL)
        wanted = set(league) if league else set(LEAGUES)
        entries = [
            (canonical_team_name(f.home_team), canonical_team_name(f.away_team), f.league_code)
            for f in parse_fixtures_csv(raw)
            if f.league_code in wanted
        ][:max_fixtures]
        if not entries:
            typer.echo("No upcoming fixtures found for the configured leagues.")
            return

    books = []
    with session_scope() as session:
        for home, away, league_code in entries:
            try:
                books.append(build_market_book(session, league_code, home, away))
            except (PitchprobError, ValueError) as exc:
                typer.echo(f"skipping {home} vs {away}: {exc}")

    coupons = generate_coupons(books, tier=tier, max_legs=max_legs, top_n=top)
    if not coupons:
        typer.echo(f"No coupons reach the {tier} band "
                   f"({TIERS[tier][0]:.0%}-{TIERS[tier][1]:.0%}) from these fixtures.")
        return

    typer.echo(f"\n=== {tier} coupons (band {TIERS[tier][0]:.0%}-{TIERS[tier][1]:.0%}) ===")
    for rank, item in enumerate(coupons, start=1):
        typer.echo(f"\n#{rank}  joint probability {item.joint_probability:.1%}")
        for leg in item.legs:
            typer.echo(f"  {leg.match_label} — {leg.market}: {leg.selection} "
                       f"({leg.probability:.0%})")
            for reason in leg.reasons:
                typer.echo(f"      + {reason}")
    typer.echo(f"\n{INDEPENDENCE_CAVEAT}")


def _model_factory(name: str, half_life: float, calibration: str = "isotonic") -> Any:
    key = name.replace("_", "-").lower()
    if key == "dixon-coles":
        return lambda: DixonColesModel(half_life_days=half_life)
    if key == "poisson":
        return lambda: IndependentPoissonModel(half_life_days=half_life)
    if key == "elo":
        return lambda: EloModel()
    if key == "gbm":
        return lambda: GbmModel()
    if key == "ensemble":
        return lambda: EnsembleModel(half_life_days=half_life)
    if key == "ensemble-cal":
        from pitchprob.models.calibrated import CalibratedEnsembleModel

        if calibration not in ("isotonic", "temperature"):
            raise typer.BadParameter(f"unknown calibration {calibration!r}")
        return lambda: CalibratedEnsembleModel(
            half_life_days=half_life,
            method=cast(Any, calibration),
        )
    raise typer.BadParameter(
        f"unknown model {name!r} "
        "(dixon-coles | poisson | elo | gbm | ensemble | ensemble-cal)"
    )


@app.command()
def backtest(
    league: Annotated[str, typer.Option(help="League code")] = "E0",
    start: Annotated[str, typer.Option(help="First prediction date, ISO")] = "2021-08-01",
    model: Annotated[str, typer.Option(help="dixon-coles | poisson | elo")] = "dixon-coles",
    refit_days: Annotated[int, typer.Option(help="Refit cadence, days")] = 7,
    min_train_matches: Annotated[int, typer.Option(help="Training-history gate")] = 380,
    half_life: Annotated[float, typer.Option(help="Time-decay half-life, days")] = (
        DEFAULT_HALF_LIFE_DAYS
    ),
    ev_threshold: Annotated[float, typer.Option(help="Min EV to place a bet")] = 0.03,
    pool: Annotated[
        bool, typer.Option("--pool", help="Train on all leagues, evaluate on --league")
    ] = False,
    selector: Annotated[
        str, typer.Option(help="Bet selection: naive | blended (ADR 0006)")
    ] = "naive",
    blend_weight: Annotated[
        float, typer.Option(help="Model share in the log-linear blend")
    ] = 0.4,
    max_price: Annotated[
        float, typer.Option(help="Hard price cap for blended selection")
    ] = 8.0,
    calibration: Annotated[
        str, typer.Option(help="ensemble-cal method: isotonic | temperature")
    ] = "isotonic",
) -> None:
    """Walk-forward backtest vs the margin-removed Pinnacle closing line."""
    if selector not in ("naive", "blended"):
        raise typer.BadParameter(f"unknown selector {selector!r} (naive | blended)")
    configure_logging(get_settings().log_level)
    start_date = date.fromisoformat(start)
    factory = _model_factory(model, half_life, calibration)

    with session_scope() as session:
        frame = load_matches_frame(
            session, league_code=None if pool else league, include_stats=True
        )
        if frame.empty:
            typer.echo(f"no matches ingested for {league!r}")
            raise typer.Exit(code=1)

        typer.echo(f"walk-forward backtest: {model} on {league}"
                   f"{' (pooled training)' if pool else ''}, start {start_date}, "
                   f"refit every {refit_days}d")
        preds = run_backtest(
            frame,
            model_factory=factory,
            start=start_date,
            refit_every_days=refit_days,
            min_train_matches=min_train_matches,
            predict_only={"league": league} if pool else None,
        )
        if preds.empty:
            typer.echo("no predictions generated (check --start and training history)")
            raise typer.Exit(code=1)

        probs = preds[["p_home", "p_draw", "p_away"]].to_numpy(dtype=np.float64)
        outcomes = preds["outcome"].to_numpy(dtype=np.int64)
        per_class_ece = {
            f"ece_{name}": expected_calibration_error(
                (outcomes == index).astype(np.int64), probs[:, index]
            )
            for index, name in enumerate(("home", "draw", "away"))
        }
        metrics: dict[str, Any] = {
            "n_predictions": len(preds),
            "model": {
                "log_loss": log_loss(outcomes, probs),
                "brier": brier_score(outcomes, probs),
                "rps": ranked_probability_score(outcomes, probs),
                **per_class_ece,
            },
        }

        closing = load_closing_odds_frame(session, league_code=league, bookmaker="pinnacle")
        joined = preds.merge(closing, on=["date", "home_team", "away_team"], how="inner")
        if len(joined) > 0:
            closing_prices = joined[["price_home", "price_draw", "price_away"]].to_numpy(
                dtype=np.float64
            )
            shin = np.array([remove_overround_shin(list(p)) for p in closing_prices])
            joint_outcomes = joined["outcome"].to_numpy(dtype=np.int64)
            joint_probs = joined[["p_home", "p_draw", "p_away"]].to_numpy(dtype=np.float64)
            metrics["benchmark_subset"] = {
                "n": len(joined),
                "model_log_loss": log_loss(joint_outcomes, joint_probs),
                "model_rps": ranked_probability_score(joint_outcomes, joint_probs),
                "closing_log_loss": log_loss(joint_outcomes, shin),
                "closing_rps": ranked_probability_score(joint_outcomes, shin),
            }

            best = load_closing_odds_frame(
                session, league_code=league, bookmaker="market_max"
            )
            price_source = best if len(best) else closing
            bets = joined.merge(
                price_source,
                on=["date", "home_team", "away_team"],
                how="inner",
                suffixes=("", "_best"),
            )
            candidates = []
            price_cols = {"home": "price_home", "draw": "price_draw", "away": "price_away"}
            if "price_home_best" in bets.columns:
                price_cols = {k: f"{v}_best" for k, v in price_cols.items()}
            for i, raw_row in enumerate(bets.itertuples(index=False)):
                row = cast(Any, raw_row)  # pandas named tuples are untyped
                for sel_idx, selection in enumerate(("home", "draw", "away")):
                    candidates.append(
                        {
                            "date": row.date,
                            "probability": float(
                                getattr(row, ("p_home", "p_draw", "p_away")[sel_idx])
                            ),
                            "market_probability": float(shin[i, sel_idx]),
                            "price": float(getattr(row, price_cols[selection])),
                            "won": int(row.outcome) == sel_idx,
                            "closing_probability": float(shin[i, sel_idx]),
                        }
                    )
            import pandas as pd

            candidate_frame = pd.DataFrame(candidates)
            if selector == "blended":
                selected = select_value_bets(
                    candidate_frame,
                    blend_weight=blend_weight,
                    ev_threshold=ev_threshold,
                    max_price=max_price,
                )
                # bets are pre-qualified on blended EV; settle them all at
                # the anchored probability
                sim_frame = selected.assign(probability=selected["p_bet"])
                staking = simulate_staking(sim_frame, strategy="flat", ev_threshold=-1.0)
            else:
                staking = simulate_staking(
                    candidate_frame, strategy="flat", ev_threshold=ev_threshold
                )
            metrics["staking_flat"] = {
                "selector": selector,
                "n_bets": staking.n_bets,
                "roi": staking.roi,
                "profit_units": staking.profit,
                "hit_rate": staking.hit_rate,
                "max_drawdown": staking.max_drawdown,
                "mean_clv": staking.mean_clv,
            }

        session.add(
            Backtest(
                created_at=datetime.now(tz=UTC),
                config={
                    "league": league,
                    "model": model,
                    "start": start,
                    "refit_days": refit_days,
                    "half_life": half_life,
                    "ev_threshold": ev_threshold,
                },
                metrics=metrics,
            )
        )

    typer.echo(json_lib.dumps(metrics, indent=2))
    typer.echo(
        "\nNote: closing-line metrics are the benchmark to approach; beating them "
        "consistently is unlikely (see ADR 0004)."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
