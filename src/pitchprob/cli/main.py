"""pitchprob CLI: ingest → train → predict → backtest.

Every command opens its own transactional session; ``_make_downloader`` is a
seam that tests replace with an offline fake.
"""

import contextlib
import json as json_lib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any

import httpx
import typer

from pitchprob.core.config import get_settings
from pitchprob.core.db import session_scope
from pitchprob.core.errors import PitchprobError
from pitchprob.core.logging import configure_logging
from pitchprob.data.dataset import load_matches_frame
from pitchprob.data.orm import Backtest, ModelRun, OddsTick
from pitchprob.data.service import LEAGUES, Downloader, HttpDownloader, IngestionService
from pitchprob.models.dixon_coles import DixonColesModel
from pitchprob.models.elo import EloModel
from pitchprob.services.experiments import (
    apply_overrides,
    compare_experiments,
    config_hash,
    run_experiment,
)
from pitchprob.services.harness import HarnessConfig, NoDataError, run_harness
from pitchprob.services.latency_map import (
    DEFAULT_MIN_MOVE,
    DEFAULT_MIN_OBSERVED,
    run_latency_map,
)
from pitchprob.services.movement_study import run_movement_study
from pitchprob.services.prediction import DEFAULT_HALF_LIFE_DAYS, build_market_book
from pitchprob.services.quote_check import log_quote_check, quote_check_report
from pitchprob.services.recorder import (
    DEFAULT_RESERVE,
    import_tape,
    record_corners_to_csv,
    record_snapshot,
    record_snapshot_to_csv,
)

if TYPE_CHECKING:
    from pitchprob.services.alerts import Notifier

app = typer.Typer(
    name="pitchprob",
    help="Football probability engine: honest, backtested market estimates.",
    no_args_is_help=True,
)


def _make_downloader(*, refresh: bool = False) -> Downloader:
    settings = get_settings()
    return HttpDownloader(cache_dir=settings.data_dir / "raw", refresh=refresh)


def _make_odds_client(api_key: str) -> Any:
    from pitchprob.data.adapters.odds_api import OddsApiClient

    return OddsApiClient(api_key=api_key)


def _pct(value: float) -> str:
    return f"{100 * value:5.1f}%"


@app.command()
def ingest(
    league: Annotated[list[str] | None, typer.Option(help="League code, repeatable")] = None,
    all_leagues: Annotated[bool, typer.Option("--all", help="All configured leagues")] = False,
    from_year: Annotated[int, typer.Option(help="First season start year")] = 2014,
    to_year: Annotated[int, typer.Option(help="Last season start year")] = 2025,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Re-download instead of serving the on-disk cache. Needed "
                 "for the current season: its file grows every round, and "
                 "the cache would otherwise serve August forever.",
        ),
    ] = False,
) -> None:
    """Download and store seasons from football-data.co.uk (idempotent)."""
    configure_logging(get_settings().log_level)
    codes = list(LEAGUES) if all_leagues or not league else league
    downloader = _make_downloader(refresh=refresh)
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
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Re-download instead of serving the on-disk cache (needed "
                 "for the current season). Raw responses are still archived "
                 "append-only under data/understat/.",
        ),
    ] = False,
) -> None:
    """Attach Understat xG to already-ingested matches (idempotent)."""
    from pitchprob.data.adapters.understat import REQUIRED_HEADERS
    from pitchprob.data.understat_archive import SNAPSHOT_SUBDIR
    from pitchprob.data.xg_service import XgUpdateService

    configure_logging(get_settings().log_level)
    codes = list(LEAGUES) if all_leagues or not league else league
    downloader = HttpDownloader(
        cache_dir=get_settings().data_dir / "raw",
        headers=REQUIRED_HEADERS,
        refresh=refresh,
    )
    with session_scope() as session:
        service = XgUpdateService(
            session=session,
            downloader=downloader,
            snapshot_dir=get_settings().data_dir / SNAPSHOT_SUBDIR,
        )
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
def injuries(
    league: Annotated[list[str] | None, typer.Option(help="League code, repeatable")] = None,
    all_leagues: Annotated[bool, typer.Option("--all", help="All configured leagues")] = False,
    from_season: Annotated[int, typer.Option(help="First season start year")] = 2022,
    to_season: Annotated[int, typer.Option(help="Last season start year")] = 2024,
) -> None:
    """Ingest API-Football injury lists (free tier: seasons 2022-2024, ADR 0008)."""
    from pitchprob.data.adapters.api_football import ApiFootballClient, ApiFootballError
    from pitchprob.data.injury_service import InjuryUpdateService

    configure_logging(get_settings().log_level)
    settings = get_settings()
    try:
        client = ApiFootballClient(
            key=settings.api_football_key, cache_dir=settings.data_dir / "raw"
        )
    except ApiFootballError as exc:
        typer.echo(f"error: {exc}")
        raise typer.Exit(code=1) from exc

    codes = list(LEAGUES) if all_leagues or not league else league
    with session_scope() as session:
        service = InjuryUpdateService(session=session, client=client)
        for code in codes:
            for season in range(from_season, to_season + 1):
                try:
                    report = service.update_league_season(code, season)
                except (ApiFootballError, ValueError, httpx.HTTPError) as exc:
                    typer.echo(f"{code} {season}: failed ({exc}); skipping")
                    continue
                unknown = ", ".join(sorted(report.unmatched_teams)) or "-"
                typer.echo(
                    f"{code} {season}: {report.inserted} new of {report.parsed} records "
                    f"(unknown teams: {unknown})"
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
        typer.echo("\nValue vs offered odds (Dixon-Coles + Shin market blend, quarter-Kelly):")
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


@app.command()
def backtest(
    league: Annotated[str, typer.Option(help="League code, or 'all'")] = "E0",
    start: Annotated[str, typer.Option(help="First prediction date, ISO")] = "2021-08-01",
    model: Annotated[
        str, typer.Option(help="dixon-coles | poisson | elo | gbm | ensemble | ensemble-cal")
    ] = "dixon-coles",
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
        str, typer.Option(help="Bet selection: naive | blended (ADR 0006) "
                               "| meta (CLV gate, ADR 0011)")
    ] = "naive",
    blend_weight: Annotated[
        float, typer.Option(help="Model share in the log-linear blend")
    ] = 0.4,
    max_price: Annotated[
        float, typer.Option(help="Hard price cap for blended selection")
    ] = 8.0,
    calibration: Annotated[
        str, typer.Option(help="ensemble-cal method: temperature (default) | isotonic")
    ] = "temperature",
    end: Annotated[
        str | None, typer.Option(help="Last evaluation date, ISO (bounds A/B windows)")
    ] = None,
    ablate: Annotated[
        str | None, typer.Option(help="NaN a feature family for A/B runs: absences")
    ] = None,
    at: Annotated[
        str, typer.Option(help="Snapshot to bet at: close (legacy kickoff sim) "
                               "| open (true CLV vs the close, ADR 0010)")
    ] = "close",
    markets: Annotated[
        str, typer.Option(help="Comma list of markets to bet: 1x2,ou,ah "
                               "(auxiliary markets need --at open)")
    ] = "1x2",
) -> None:
    """Walk-forward backtest vs the margin-removed Pinnacle closing line."""
    configure_logging(get_settings().log_level)
    config = HarnessConfig(
        start=date.fromisoformat(start),
        league=league,
        model=model,
        refit_days=refit_days,
        min_train_matches=min_train_matches,
        half_life=half_life,
        ev_threshold=ev_threshold,
        pool=pool,
        selector=selector,
        blend_weight=blend_weight,
        max_price=max_price,
        calibration=calibration,
        end=date.fromisoformat(end) if end else None,
        ablate=ablate,
        at=at,
        markets=tuple(m.strip() for m in markets.split(",") if m.strip()),
    )
    typer.echo(f"walk-forward backtest: {model} on {league}"
               f"{' (pooled training)' if pool else ''}, start {config.start}, "
               f"refit every {refit_days}d, at {at}, markets {','.join(config.markets)}")

    with session_scope() as session:
        try:
            result = run_harness(session, config)
        except NoDataError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
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
                    "selector": selector,
                    "at": at,
                    "markets": list(config.markets),
                    "pool": pool,
                    "blend_weight": blend_weight,
                    "max_price": max_price,
                    "end": end,
                    "ablate": ablate,
                },
                metrics=result.metrics,
            )
        )

    typer.echo(json_lib.dumps(result.metrics, indent=2))
    typer.echo(
        "\nNote: closing-line metrics are the benchmark to approach; beating them "
        "consistently is unlikely (see ADR 0004)."
    )


experiment_app = typer.Typer(
    no_args_is_help=True,
    help="Run and compare backtest experiments with paired significance (ADR 0010).",
)
app.add_typer(experiment_app, name="experiment")


def _experiment_config(
    league: str, start: str, model: str, refit_days: int, min_train_matches: int,
    half_life: float, ev_threshold: float, pool: bool, selector: str,
    blend_weight: float, max_price: float, calibration: str, end: str | None,
    ablate: str | None, at: str, markets: str,
) -> HarnessConfig:
    return HarnessConfig(
        start=date.fromisoformat(start),
        league=league,
        model=model,
        refit_days=refit_days,
        min_train_matches=min_train_matches,
        half_life=half_life,
        ev_threshold=ev_threshold,
        pool=pool,
        selector=selector,
        blend_weight=blend_weight,
        max_price=max_price,
        calibration=calibration,
        end=date.fromisoformat(end) if end else None,
        ablate=ablate,
        at=at,
        markets=tuple(m.strip() for m in markets.split(",") if m.strip()),
    )


@experiment_app.command("run")
def experiment_run(
    name: Annotated[str, typer.Option(help="Experiment name for the registry")],
    league: Annotated[str, typer.Option(help="League code, or 'all'")] = "E0",
    start: Annotated[str, typer.Option(help="First prediction date, ISO")] = "2021-08-01",
    model: Annotated[str, typer.Option(help="Model name (see backtest)")] = "dixon-coles",
    refit_days: Annotated[int, typer.Option(help="Refit cadence, days")] = 7,
    min_train_matches: Annotated[int, typer.Option(help="Training-history gate")] = 380,
    half_life: Annotated[float, typer.Option(help="Time-decay half-life, days")] = (
        DEFAULT_HALF_LIFE_DAYS
    ),
    ev_threshold: Annotated[float, typer.Option(help="Min EV to place a bet")] = 0.03,
    pool: Annotated[bool, typer.Option("--pool", help="Pooled training")] = False,
    selector: Annotated[str, typer.Option(help="naive | blended | meta")] = "naive",
    blend_weight: Annotated[float, typer.Option(help="Model share in blend")] = 0.4,
    max_price: Annotated[float, typer.Option(help="Hard price cap")] = 8.0,
    calibration: Annotated[
        str, typer.Option(help="temperature (default) | isotonic")
    ] = "temperature",
    end: Annotated[str | None, typer.Option(help="Last evaluation date, ISO")] = None,
    ablate: Annotated[str | None, typer.Option(help="Feature family to NaN")] = None,
    at: Annotated[str, typer.Option(help="close | open")] = "close",
    markets: Annotated[str, typer.Option(help="Comma list: 1x2,ou,ah")] = "1x2",
) -> None:
    """Run one configuration and store it as a named, content-hashed row."""
    configure_logging(get_settings().log_level)
    config = _experiment_config(
        league, start, model, refit_days, min_train_matches, half_life,
        ev_threshold, pool, selector, blend_weight, max_price, calibration,
        end, ablate, at, markets,
    )
    with session_scope() as session:
        try:
            run_id, metrics = run_experiment(session, config, name=name)
        except NoDataError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    typer.echo(json_lib.dumps(metrics, indent=2))
    typer.echo(f"\nstored as experiment run #{run_id} "
               f"(name {name!r}, hash {config_hash(config)})")


@experiment_app.command("compare")
def experiment_compare(
    vs: Annotated[
        list[str],
        typer.Option(
            "--vs",
            help="key=value override defining the B side (repeatable), "
                 "e.g. --vs ablate=absences --vs refit-days=28",
        ),
    ],
    league: Annotated[str, typer.Option(help="League code, or 'all'")] = "E0",
    start: Annotated[str, typer.Option(help="First prediction date, ISO")] = "2021-08-01",
    model: Annotated[str, typer.Option(help="Model name (see backtest)")] = "dixon-coles",
    refit_days: Annotated[int, typer.Option(help="Refit cadence, days")] = 7,
    min_train_matches: Annotated[int, typer.Option(help="Training-history gate")] = 380,
    half_life: Annotated[float, typer.Option(help="Time-decay half-life, days")] = (
        DEFAULT_HALF_LIFE_DAYS
    ),
    ev_threshold: Annotated[float, typer.Option(help="Min EV to place a bet")] = 0.03,
    pool: Annotated[bool, typer.Option("--pool", help="Pooled training")] = False,
    selector: Annotated[str, typer.Option(help="naive | blended | meta")] = "naive",
    blend_weight: Annotated[float, typer.Option(help="Model share in blend")] = 0.4,
    max_price: Annotated[float, typer.Option(help="Hard price cap")] = 8.0,
    calibration: Annotated[
        str, typer.Option(help="temperature (default) | isotonic")
    ] = "temperature",
    end: Annotated[str | None, typer.Option(help="Last evaluation date, ISO")] = None,
    ablate: Annotated[str | None, typer.Option(help="Feature family to NaN")] = None,
    at: Annotated[str, typer.Option(help="close | open")] = "close",
    markets: Annotated[str, typer.Option(help="Comma list: 1x2,ou,ah")] = "1x2",
    n_boot: Annotated[int, typer.Option(help="Bootstrap resamples")] = 5000,
) -> None:
    """Paired A/B comparison: flags define A, --vs overrides define B."""
    configure_logging(get_settings().log_level)
    config_a = _experiment_config(
        league, start, model, refit_days, min_train_matches, half_life,
        ev_threshold, pool, selector, blend_weight, max_price, calibration,
        end, ablate, at, markets,
    )
    with session_scope() as session:
        try:
            config_b = apply_overrides(config_a, vs)
            result = compare_experiments(
                session, config_a, config_b, n_boot=n_boot, store=True
            )
        except NoDataError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    typer.echo(result.report)


@app.command("record-odds")
def record_odds(
    league: Annotated[str, typer.Option(help="League code, or 'all'")] = "all",
    markets: Annotated[
        str, typer.Option(help="Comma list of The Odds API markets: h2h,totals,spreads")
    ] = "h2h,totals,spreads",
    csv_dir: Annotated[
        str | None,
        typer.Option(help="Write a gzipped CSV snapshot here instead of the DB "
                          "(the GitHub Actions cloud-recorder sink, ADR 0012)"),
    ] = None,
) -> None:
    """Append one live odds snapshot to the odds_ticks tape (ADR 0012).

    Designed for a scheduler: each run costs ~(markets x leagues) API
    credits from the 500/month free tier — five leagues with all three
    markets is ~15 credits, i.e. about one snapshot per day.
    """
    from pathlib import Path

    from pitchprob.data.adapters.odds_api import SPORT_KEYS, scrub_http_error

    configure_logging(get_settings().log_level)
    settings = get_settings()
    if not settings.odds_api_key:
        typer.echo("PITCHPROB_ODDS_API_KEY is not set (see .env)")
        raise typer.Exit(code=1)
    leagues = list(SPORT_KEYS) if league == "all" else [league]
    market_list = [m.strip() for m in markets.split(",") if m.strip()]
    client = _make_odds_client(settings.odds_api_key)
    try:
        if csv_dir is not None:
            summary, path = record_snapshot_to_csv(
                client, leagues=leagues, markets=market_list,
                directory=Path(csv_dir),
            )
            typer.echo(f"snapshot file: {path if path else '(empty snapshot, none)'}")
        else:
            with session_scope() as session:
                summary = record_snapshot(
                    session, client, leagues=leagues, markets=market_list
                )
    except httpx.HTTPError as exc:
        typer.echo(scrub_http_error(exc))
        # `from None`: the cause chain carries the request URL with the
        # apiKey — it must not survive into any traceback.
        raise typer.Exit(code=1) from None
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"recorded {summary.ticks_inserted} ticks across "
        f"{summary.events_seen} events"
    )
    typer.echo(f"bookmakers seen: {', '.join(summary.bookmakers) or '(none)'}")
    if summary.requests_remaining is not None:
        typer.echo(f"API credits remaining this month: {summary.requests_remaining}")


@app.command("record-corners")
def record_corners(
    league: Annotated[str, typer.Option(help="League code, or 'all'")] = "E0",
    within_hours: Annotated[
        int, typer.Option(help="Only price fixtures kicking off within this many hours")
    ] = 26,
    csv_dir: Annotated[
        str, typer.Option(help="Directory for the gzipped CSV snapshot")
    ] = "data/tape",
    reserve: Annotated[
        int, typer.Option(help="Stop if fewer than this many monthly credits remain")
    ] = DEFAULT_RESERVE,
    max_credits: Annotated[
        int | None, typer.Option(help="Hard ceiling on credits spent by this run")
    ] = None,
) -> None:
    """Record corners odds for fixtures close to kickoff (ADR 0014).

    Unlike `record-odds`, this costs credits per *fixture*: corners are an
    additional market, served one event at a time. Measured 2026-07-21:
    Pinnacle prices corners about a day before kickoff and not at three
    days, and fixtures with nothing priced are billed zero — so a 26-hour
    window costs roughly one credit per fixture and nothing for looking.
    The E0 pilot is ~10 fixtures per round, ~43 credits per month.

    Both spending limits are honoured and the reason for stopping is
    printed: the main tape's monthly budget comes first.
    """
    from pathlib import Path

    from pitchprob.data.adapters.odds_api import SPORT_KEYS, scrub_http_error

    configure_logging(get_settings().log_level)
    settings = get_settings()
    if not settings.odds_api_key:
        typer.echo("PITCHPROB_ODDS_API_KEY is not set (see .env)")
        raise typer.Exit(code=1)
    leagues = list(SPORT_KEYS) if league == "all" else [league]
    try:
        summary, path = record_corners_to_csv(
            _make_odds_client(settings.odds_api_key),
            leagues=leagues,
            within_hours=within_hours,
            directory=Path(csv_dir),
            reserve=reserve,
            max_credits=max_credits,
        )
    except httpx.HTTPError as exc:
        typer.echo(scrub_http_error(exc))
        raise typer.Exit(code=1) from None
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"{summary.events_in_window} fixtures within {within_hours}h; "
        f"{summary.events_priced} had corners priced"
    )
    typer.echo(
        f"recorded {summary.ticks_inserted} ticks for {summary.credits_spent} credits"
    )
    typer.echo(f"bookmakers seen: {', '.join(summary.bookmakers) or '(none)'}")
    typer.echo(f"snapshot file: {path if path else '(empty snapshot, none)'}")
    if summary.requests_remaining is not None:
        typer.echo(f"API credits remaining this month: {summary.requests_remaining}")
    if summary.stop_reason is not None:
        typer.echo(f"stopped early: {summary.stop_reason}")


@app.command("import-tape")
def import_tape_command(
    directory: Annotated[
        str, typer.Option("--dir", help="Directory with *.csv.gz tape snapshots")
    ] = "data/tape",
) -> None:
    """Merge cloud-recorded tape snapshots into odds_ticks (idempotent)."""
    from pathlib import Path

    configure_logging(get_settings().log_level)
    with session_scope() as session:
        files, ticks = import_tape(session, Path(directory))
    typer.echo(f"imported {files} new snapshot files ({ticks} ticks)")


# --- Phase 5 part 3: the PL value scanner and the forward pick ledger -----


def _parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_book_value(spec: str, option: str) -> tuple[str, str]:
    book, sep, value = spec.partition(":")
    if not sep or not book or not value:
        raise typer.BadParameter(
            f"--{option} expects book:price, got {spec!r}"
        )
    return book, value


def _build_quotes(
    quote_specs: list[str],
    tax_free_books: list[str],
    boost_specs: list[str],
    haircut_specs: list[str],
) -> list[Any]:
    from decimal import Decimal, InvalidOperation

    from pitchprob.betting.effective import PromoTerms
    from pitchprob.services.scanner import OperatorQuote

    try:
        boosts = {
            book: Decimal(value)
            for book, value in (
                _parse_book_value(s, "boost") for s in boost_specs
            )
        }
        haircuts = {
            book: float(value)
            for book, value in (
                _parse_book_value(s, "haircut") for s in haircut_specs
            )
        }
        quotes = []
        for spec in quote_specs:
            book, value = _parse_book_value(spec, "quote")
            tax_free = book in tax_free_books
            boosted = boosts.get(book)
            haircut = haircuts.get(book, 1.0)
            promo = (
                PromoTerms(
                    tax_free=tax_free,
                    boosted_price=boosted,
                    payout_haircut=haircut,
                )
                if tax_free or boosted is not None or haircut != 1.0
                else None
            )
            quotes.append(
                OperatorQuote(bookmaker=book, price=Decimal(value), promo=promo)
            )
    except InvalidOperation as exc:
        raise typer.BadParameter(f"bad decimal price: {exc}") from exc
    return quotes


def _signed_pct(value: float | None) -> str:
    return "     —" if value is None else f"{100 * value:+6.1f}%"


def _price_str(value: Any) -> str:
    """Numeric(8,3) round-trips as '2.100'; operators read '2.10'."""
    from decimal import Decimal

    normalized = Decimal(value).normalize()
    exponent = normalized.as_tuple().exponent
    if isinstance(exponent, int) and exponent > -2:
        normalized = normalized.quantize(Decimal("0.01"))
    return f"{normalized:f}"


def _age_hours(delta_seconds: float) -> str:
    return f"{int(delta_seconds // 3600)}h ago"


def _effective_display(verdict: Any) -> str:
    from decimal import Decimal

    from pitchprob.betting.effective import effective_price

    promo = verdict.promo
    base = (
        verdict.price_quoted
        if promo is None or promo.boosted_price is None
        else promo.boosted_price
    )
    tax_free = promo is not None and promo.tax_free
    multiplier = promo.tax_multiplier if promo is not None else None
    return str(
        effective_price(base, tax_free=tax_free, multiplier=multiplier).quantize(
            Decimal("0.01")
        )
    )


@app.command("scan")
def scan_command(
    match: Annotated[
        str, typer.Argument(help="Team substring to find the fixture on the tape")
    ],
    selection: Annotated[
        str, typer.Option(help="home/draw/away or over/under")
    ],
    market: Annotated[str, typer.Option(help="1x2, ou or ah")] = "1x2",
    line: Annotated[
        str | None, typer.Option(help="Goal/handicap line (ou/ah); must match "
                                      "the tape's current Pinnacle line")
    ] = None,
    quote: Annotated[
        list[str] | None,
        typer.Option("--quote", help="Operator-seen PL price as book:price, "
                                     "repeatable (e.g. betclic:2.10)"),
    ] = None,
    tax_free: Annotated[
        list[str] | None,
        typer.Option("--tax-free", help="Book whose quote is under a tax-free "
                                        "promo (repeatable)"),
    ] = None,
    boost: Annotated[
        list[str] | None,
        typer.Option("--boost", help="Boosted price as book:price (repeatable)"),
    ] = None,
    haircut: Annotated[
        list[str] | None,
        typer.Option("--haircut", help="Payout haircut as book:factor for "
                                       "conditioned winnings (repeatable)"),
    ] = None,
    model_prob: Annotated[
        float | None,
        typer.Option(help="Model fair probability for the selection "
                          "(secondary anchor, informational)"),
    ] = None,
    min_edge: Annotated[
        float, typer.Option(help="Minimum effective edge vs the sharp anchor "
                                 "before PLAY")
    ] = 0.02,
    feed: Annotated[
        bool,
        typer.Option(help="Also pull PL quotes from the odds-api.io feed "
                          "(marked UNVERIFIED until quote-check clears it)"),
    ] = False,
) -> None:
    """Verdict PL quotes against the live Pinnacle fair from the tape.

    Primary anchor: Shin-de-margined Pinnacle from the odds tape (the sharp
    anchor IS the edge thesis; the model does not outpredict the market —
    Phase 0-2a verdicts). Expect mostly NO BET: the 12% tax sits in the
    prices; value lives in promos/boosts and slow PL lines.
    """
    from decimal import Decimal

    from pitchprob.betting.promos import parse_disabled
    from pitchprob.services.scanner import feed_quotes
    from pitchprob.services.scanner import scan as run_scan

    configure_logging(get_settings().log_level)
    if not quote and not feed:
        raise typer.BadParameter(
            "at least one --quote book:price is required (or --feed)"
        )
    # §8 kill switch: books whose promo the env has switched off price bare.
    disabled_promos = parse_disabled(get_settings().disabled_promos)
    quotes = _build_quotes(quote or [], tax_free or [], boost or [], haircut or [])
    with session_scope() as session:
        try:
            if feed:
                probe = run_scan(
                    session, query=match, market=market, selection=selection,
                    line=Decimal(line) if line is not None else None,
                    quotes=[], min_edge=min_edge,
                )
                quotes = quotes + feed_quotes(
                    session,
                    home_team=probe.home_team,
                    away_team=probe.away_team,
                    kickoff=probe.commence_time,
                    market=market,
                    selection=selection,
                    line=Decimal(line) if line is not None else None,
                    at=datetime.now(tz=UTC),
                )
                if not quotes:
                    typer.echo(
                        "the feed has no quote for this selection — nothing "
                        "to verdict (record it with `pitchprob oddsio record`)"
                    )
                    raise typer.Exit(code=1)
            result = run_scan(
                session,
                query=match,
                market=market,
                selection=selection,
                line=Decimal(line) if line is not None else None,
                quotes=quotes,
                model_probability=model_prob,
                min_edge=min_edge,
                disabled_promos=disabled_promos,
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    line_label = f" {result.line}" if result.line is not None else ""
    typer.echo(
        f"{result.home_team} vs {result.away_team} — kickoff "
        f"{result.commence_time:%Y-%m-%d %H:%M} UTC (event {result.event_id})"
    )
    if result.anchor is None or result.anchor_age is None:
        typer.echo(
            f"{market}{line_label} {selection}: no Pinnacle anchor on the "
            "tape for this market/line — verdicts below carry no edge"
        )
    else:
        anchor_price = result.anchor.prices[selection]
        fair = result.anchor.probabilities[selection]
        typer.echo(
            f"{market}{line_label} {selection} | anchor: pinnacle "
            f"{_price_str(anchor_price)} -> fair {100 * fair:.1f}% | observed "
            f"{result.anchor.observed_at:%Y-%m-%d %H:%M} UTC "
            f"({_age_hours(result.anchor_age.total_seconds())})"
        )
    for verdict in result.verdicts:
        parts = [
            f"{verdict.verdict:<9}",
            f"{verdict.bookmaker:<10}",
            f"quoted {_price_str(verdict.price_quoted)}",
            f"eff {_effective_display(verdict)}",
            f"edge {_signed_pct(verdict.edge)}",
        ]
        if verdict.edge_model is not None:
            parts.append(f"model {_signed_pct(verdict.edge_model)}")
        if verdict.evaluation is not None and verdict.promo is not None:
            parts.append(
                f"promo {100 * verdict.evaluation.promo_value:+.1f}pp"
            )
        typer.echo("  ".join(parts))


pick_app = typer.Typer(
    no_args_is_help=True,
    help="Forward real-money pick ledger (Phase 5): log bets, settle, "
         "track CLV vs the tape's Pinnacle close.",
)
app.add_typer(pick_app, name="pick")


@pick_app.command("log")
def pick_log(
    market: Annotated[str, typer.Option(help="1x2, ou or ah")],
    selection: Annotated[str, typer.Option(help="home/draw/away or over/under")],
    book: Annotated[str, typer.Option(help="Polish bookmaker, e.g. betclic")],
    stake: Annotated[str, typer.Option(help="Stake in PLN (program: 2-5)")],
    price: Annotated[str, typer.Option(help="Executed decimal price as quoted")],
    match: Annotated[
        str | None,
        typer.Option(help="Team substring to resolve the fixture from the "
                          "tape (alternative: --home/--away/--kickoff)"),
    ] = None,
    home: Annotated[str | None, typer.Option(help="Home team (tape naming)")] = None,
    away: Annotated[str | None, typer.Option(help="Away team (tape naming)")] = None,
    kickoff: Annotated[
        str | None, typer.Option(help="Kickoff, ISO (UTC assumed if naive)")
    ] = None,
    event: Annotated[
        str | None, typer.Option(help="Tape event id (enables sharp anchor "
                                      "and CLV when teams are given manually)")
    ] = None,
    line: Annotated[str | None, typer.Option(help="Line for ou/ah")] = None,
    tax_free: Annotated[
        bool, typer.Option("--tax-free", help="Force the x1.0 regime (the "
                                              "coupon showed tax-free). "
                                              "Default: the regime derives "
                                              "itself from the promo registry "
                                              "and the allowance state")
    ] = False,
    taxed: Annotated[
        bool, typer.Option("--taxed", help="Force the bare x0.88 regime (the "
                                           "coupon showed full tax)")
    ] = False,
    placed_at: Annotated[
        str | None, typer.Option(help="Bet execution time, ISO (default: now)")
    ] = None,
    notes: Annotated[str | None, typer.Option(help="Free-form note")] = None,
    override_risk: Annotated[
        bool,
        typer.Option(
            "--override-risk",
            help="Place the bet even though it breaches an exposure limit or "
                 "the drawdown breaker. The pick is marked permanently.",
        ),
    ] = False,
) -> None:
    """Log one executed real-money bet; the sharp anchor fills from the tape."""
    from decimal import Decimal

    from pitchprob.betting.promos import active_promo, parse_disabled
    from pitchprob.services.ledger import log_pick, tax_free_allowance
    from pitchprob.services.tape import find_events

    configure_logging(get_settings().log_level)
    if tax_free and taxed:
        raise typer.BadParameter("--tax-free and --taxed contradict each other")
    # None = derive the regime from the registry + allowance (ADR 0017);
    # an explicit flag records what the operator saw on the coupon.
    tax_free_arg: bool | None = True if tax_free else (False if taxed else None)
    disabled = parse_disabled(get_settings().disabled_promos)
    now = datetime.now(tz=UTC)
    event_id: str | None
    with session_scope() as session:
        if match is not None:
            events = find_events(session, match, at=now)
            if not events:
                raise typer.BadParameter(
                    f"no upcoming tape event matches {match!r}"
                )
            if len(events) > 1:
                names = "; ".join(
                    f"{e.home_team} vs {e.away_team} ({e.event_id})"
                    for e in events
                )
                raise typer.BadParameter(f"{match!r} is ambiguous: {names}")
            found = events[0]
            home_name, away_name = found.home_team, found.away_team
            kickoff_dt = found.commence_time
            event_id = event if event is not None else found.event_id
        else:
            if home is None or away is None or kickoff is None:
                raise typer.BadParameter(
                    "give --match, or all of --home/--away/--kickoff"
                )
            home_name, away_name = home, away
            kickoff_dt = _parse_utc_datetime(kickoff)
            event_id = event
        try:
            pick = log_pick(
                session,
                home_team=home_name,
                away_team=away_name,
                kickoff_utc=kickoff_dt,
                market=market,
                selection=selection,
                line=Decimal(line) if line is not None else None,
                bookmaker=book,
                stake_pln=Decimal(stake),
                price_quoted=Decimal(price),
                tax_free=tax_free_arg,
                placed_at=(
                    _parse_utc_datetime(placed_at) if placed_at else None
                ),
                event_id=event_id,
                notes=notes,
                override_risk=override_risk,
                disabled_promos=disabled,
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        line_label = (
            f" {_price_str(pick.line)}" if pick.line is not None else ""
        )
        typer.echo(
            f"logged pick #{pick.id}: {home_name} vs {away_name} | "
            f"{market}{line_label} {selection} @ {book} "
            f"{_price_str(pick.price_quoted)} "
            f"(effective {_price_str(pick.price_effective)})"
        )
        if pick.price_sharp is not None and pick.sharp_observed_at is not None:
            age = now - pick.sharp_observed_at.replace(tzinfo=UTC)
            typer.echo(
                f"sharp anchor: pinnacle {_price_str(pick.price_sharp)} (observed "
                f"{pick.sharp_observed_at:%Y-%m-%d %H:%M} UTC, "
                f"{_age_hours(age.total_seconds())})"
            )
        else:
            typer.echo(
                "no sharp anchor on the tape (CLV will need the event id)"
            )
        if pick.risk_override:
            typer.echo(f"RISK OVERRIDE recorded on this pick: {pick.risk_note}")
        if not Decimal("2") <= pick.stake_pln <= Decimal("5"):
            typer.echo(
                f"warning: stake {pick.stake_pln} PLN is outside the 2-5 PLN "
                "program range (Phase 3 parameters)"
            )
        regime = "auto" if tax_free_arg is None else "forced"
        typer.echo(
            f"tax regime: x{pick.tax_multiplier} ({regime}) — confirm it on "
            "the coupon; the book's screen decides"
        )
        if active_promo(book, disabled=disabled) is not None:
            allowance = tax_free_allowance(session, book)
            typer.echo(
                f"tax-free allowance remaining: {allowance.remaining} PLN "
                f"at {book}"
            )


@pick_app.command("settle")
def pick_settle(
    pick_id: Annotated[
        int | None, typer.Option("--id", help="Settle one pick manually")
    ] = None,
    score: Annotated[
        str | None, typer.Option("--result", help="Final score as H:A "
                                                  "(with --id)")
    ] = None,
) -> None:
    """Settle picks and attach CLV vs the tape's Pinnacle close.

    Without options: auto-settle every kicked-off pick whose result is in
    the matches table (run `ingest --refresh` first), then attach CLV.
    Unmatched picks are listed, never guessed.
    """
    from pitchprob.data.orm import Pick
    from pitchprob.services.ledger import attach_clv, auto_settle, settle_pick

    configure_logging(get_settings().log_level)
    if (pick_id is None) != (score is None):
        raise typer.BadParameter("--id and --result go together")
    now = datetime.now(tz=UTC)
    with session_scope() as session:
        if pick_id is not None and score is not None:
            pick = session.get(Pick, pick_id)
            if pick is None:
                raise typer.BadParameter(f"no pick #{pick_id}")
            try:
                ft_home_s, _, ft_away_s = score.partition(":")
                ft_home, ft_away = int(ft_home_s), int(ft_away_s)
            except ValueError as exc:
                raise typer.BadParameter(
                    f"--result expects H:A, got {score!r}"
                ) from exc
            settle_pick(pick, ft_home=ft_home, ft_away=ft_away, settled_at=now)
            typer.echo(
                f"settled pick #{pick_id}: {ft_home}:{ft_away}, gross return "
                f"{pick.gross_return_pln} PLN"
            )
        else:
            summary = auto_settle(session, now=now)
            typer.echo(
                f"auto-settled {summary.settled} picks "
                f"({summary.pending} pending)"
            )
            if summary.unmatched:
                from sqlalchemy import select as sa_select

                from pitchprob.data.normalize import (
                    canonical_team_name,
                    odds_api_canonical,
                    suggest_canonical,
                )
                from pitchprob.data.orm import Team

                canon = [
                    row[0]
                    for row in session.execute(
                        sa_select(Team.canonical_name)
                    ).all()
                ]
                known = set(canon)
            for description in summary.unmatched:
                typer.echo(
                    "  unmatched (settle with --id/--result or extend "
                    f"_ODDS_API_OVERRIDES in data/normalize.py): {description}"
                )
                # The tape stores "Home vs Away"; no club name contains
                # " vs ", so the split is safe. Suggest only for the side
                # the canonical maps fail to bridge — the resolved side is
                # not the problem.
                for side in description.split(" vs "):
                    if canonical_team_name(odds_api_canonical(side)) in known:
                        continue
                    for candidate, confidence in suggest_canonical(side, canon):
                        typer.echo(
                            f'    candidate: "{side}" -> "{candidate}" '
                            f"(score {confidence:.2f})"
                        )
        clv = attach_clv(session, now=now)
        typer.echo(
            f"CLV attached to {clv.attached} picks "
            f"(skipped: {clv.skipped_no_event} without event id, "
            f"{clv.skipped_no_closing} without tape closing)"
        )


@pick_app.command("list")
def pick_list() -> None:
    """The ledger: every pick with settlement and CLV decomposition."""
    from sqlalchemy import select as sa_select

    from pitchprob.data.orm import Pick
    from pitchprob.services.ledger import ledger_summary

    configure_logging(get_settings().log_level)
    with session_scope() as session:
        picks = (
            session.execute(sa_select(Pick).order_by(Pick.placed_at, Pick.id))
            .scalars()
            .all()
        )
        for pick in picks:
            line_label = (
                f" {_price_str(pick.line)}" if pick.line is not None else ""
            )
            returned = (
                f"{pick.gross_return_pln}"
                if pick.gross_return_pln is not None
                else "open"
            )
            typer.echo(
                f"#{pick.id} {pick.kickoff_utc:%Y-%m-%d} "
                f"{pick.home_team} vs {pick.away_team} | "
                f"{pick.market}{line_label} {pick.selection} @ "
                f"{pick.bookmaker} {_price_str(pick.price_quoted)}"
                f" (x{pick.tax_multiplier}) "
                f"stake {pick.stake_pln} | return {returned} | "
                f"clv exec {_signed_pct(pick.clv_exec)} "
                f"sharp {_signed_pct(pick.clv_sharp)}"
            )
        summary = ledger_summary(session)
    roi = summary["roi"]
    typer.echo(
        f"picks {summary['n_picks']} | settled {summary['n_settled']} | "
        f"staked {summary['total_staked_pln']:.2f} PLN | "
        f"returned {summary['total_returned_pln']:.2f} PLN | "
        f"profit {summary['profit_pln']:+.2f} PLN | "
        f"ROI {_signed_pct(roi) if roi is not None else '—'}"
    )
    typer.echo(
        f"CLV (n={summary['n_with_clv']}): "
        f"exec {_signed_pct(summary['mean_clv_exec'])} | "
        f"sharp {_signed_pct(summary['mean_clv_sharp'])} | "
        f"shopping {_signed_pct(summary['mean_shopping_value'])}"
    )


study_app = typer.Typer(
    no_args_is_help=True,
    help="Market-information studies (Phase 1 of the ADR 0010 program).",
)
app.add_typer(study_app, name="study")


@study_app.command("movement")
def study_movement(
    league: Annotated[str, typer.Option(help="League code")] = "E0",
    start: Annotated[str, typer.Option(help="First prediction date, ISO")] = "2021-08-01",
    model: Annotated[str, typer.Option(help="Model name (see backtest)")] = "dixon-coles",
    refit_days: Annotated[int, typer.Option(help="Refit cadence, days")] = 28,
    min_train_matches: Annotated[int, typer.Option(help="Training-history gate")] = 380,
    half_life: Annotated[float, typer.Option(help="Time-decay half-life, days")] = (
        DEFAULT_HALF_LIFE_DAYS
    ),
    end: Annotated[str | None, typer.Option(help="Last evaluation date, ISO")] = None,
    buckets: Annotated[int, typer.Option(help="Divergence quantile buckets")] = 5,
) -> None:
    """Does the open→close move side with the model where it disagrees?"""
    configure_logging(get_settings().log_level)
    with session_scope() as session:
        try:
            result = run_movement_study(
                session,
                league=league,
                start=date.fromisoformat(start),
                end=date.fromisoformat(end) if end else None,
                model=model,
                refit_days=refit_days,
                min_train_matches=min_train_matches,
                half_life=half_life,
                n_buckets=buckets,
            )
        except NoDataError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        session.add(
            Backtest(
                created_at=datetime.now(tz=UTC),
                config={
                    "study": "movement",
                    "league": league,
                    "model": model,
                    "start": start,
                    "refit_days": refit_days,
                },
                metrics=result.metrics,
            )
        )
    typer.echo(result.report)


@study_app.command("latency")
def study_latency(
    reference: Annotated[
        str, typer.Option(help="The sharp book everyone else is measured against")
    ] = "pinnacle",
    source: Annotated[
        str | None, typer.Option(help="Restrict to one tape source, e.g. odds-api-io")
    ] = None,
    since: Annotated[
        str | None, typer.Option(help="Only ticks observed at/after this ISO instant")
    ] = None,
    markets: Annotated[str, typer.Option(help="Comma list of markets")] = "1x2,ou,ah",
    min_move: Annotated[
        float, typer.Option(help="Reference move size that counts, in probability")
    ] = DEFAULT_MIN_MOVE,
    min_observed: Annotated[
        int, typer.Option(help="Moves a book needs before its median is a finding")
    ] = DEFAULT_MIN_OBSERVED,
    out: Annotated[
        str | None, typer.Option(help="Also write the markdown report here")
    ] = None,
) -> None:
    """Which bookmaker follows the sharp line last (ADR 0014)?

    Expect "No measurement" until a feed carrying Polish books is recording:
    The Odds API has none, and one daily snapshot cannot resolve a delay
    measured in hours. The report names which precondition failed.
    """
    from pathlib import Path

    configure_logging(get_settings().log_level)
    with session_scope() as session:
        result = run_latency_map(
            session,
            reference=reference,
            source=source,
            since=_parse_utc_datetime(since) if since else None,
            markets=[m.strip() for m in markets.split(",") if m.strip()],
            min_move=min_move,
            min_observed=min_observed,
        )
    if out is not None:
        Path(out).write_text(result.report + "\n", encoding="utf-8")
        typer.echo(f"report written to {out}")
    typer.echo(result.report)


# --- ADR 0014: the odds-api.io PL feed and its validation ----------------

oddsio_app = typer.Typer(
    help="The odds-api.io Polish-book feed (ADR 0014) — optional, "
         "and informational until quote-check validates it."
)
app.add_typer(oddsio_app, name="oddsio")


def _oddsio_client() -> Any:
    from pitchprob.data.adapters.odds_api_io import OddsApiIoClient

    settings = get_settings()
    if not settings.odds_api_io_key:
        typer.echo(
            "PITCHPROB_ODDS_API_IO_KEY is not set (see .env). Sign up at "
            "odds-api.io; the free tier allows two bookmakers."
        )
        raise typer.Exit(code=1)
    return OddsApiIoClient(settings.odds_api_io_key)


@oddsio_app.command("books")
def oddsio_books(
    filter_text: Annotated[
        str, typer.Option("--filter", help="Substring to match, e.g. 'PL'")
    ] = "PL",
) -> None:
    """List the feed's bookmaker catalogue (no API key needed).

    This is how the PL coverage claim was checked in the first place: on
    2026-07-21 it showed Betclic PL, STS PL, eFortuna PL, Betfan PL,
    LVbet PL and Superbet active — and no Pinnacle.
    """
    from pitchprob.data.adapters.odds_api_io import OddsApiIoClient, scrub_http_error

    configure_logging(get_settings().log_level)
    try:
        books = OddsApiIoClient(api_key="").list_bookmakers()
    except httpx.HTTPError as exc:
        typer.echo(scrub_http_error(exc))
        raise typer.Exit(code=1) from None
    matches = [
        b for b in books if filter_text.lower() in str(b.get("name", "")).lower()
    ]
    for book in matches:
        state = "active" if book.get("active") else "inactive"
        typer.echo(f"{book.get('name')}  [{state}]")
    typer.echo(f"{len(matches)} of {len(books)} bookmakers match {filter_text!r}")


@oddsio_app.command("select")
def oddsio_select(
    books: Annotated[
        str, typer.Option(help="Comma list of feed bookmaker names")
    ] = "Betclic PL,STS PL",
    show: Annotated[
        bool, typer.Option("--show", help="Only print the current selection")
    ] = False,
) -> None:
    """Attach bookmakers to your key (free tier: two).

    Names must match the catalogue exactly — check with
    `pitchprob oddsio books`. Run with --show first to see what is already
    selected; re-running replaces the selection, it does not add to it.
    """
    from pitchprob.data.adapters.odds_api_io import scrub_http_error

    configure_logging(get_settings().log_level)
    client = _oddsio_client()
    try:
        if show:
            typer.echo(f"currently selected: {client.selected_bookmakers()}")
            return
        chosen = [book.strip() for book in books.split(",") if book.strip()]
        client.select_bookmakers(chosen)
        typer.echo(f"selected: {', '.join(chosen)}")
        typer.echo(f"confirmed by the API: {client.selected_bookmakers()}")
    except httpx.HTTPError as exc:
        typer.echo(scrub_http_error(exc))
        raise typer.Exit(code=1) from None


@oddsio_app.command("probe")
def oddsio_probe(
    event_id: Annotated[str, typer.Option(help="Feed event id")],
    books: Annotated[
        str, typer.Option(help="Comma list of feed bookmaker names")
    ] = "Betclic PL,STS PL",
) -> None:
    """Dump the raw market names the feed sends for one event.

    The market mapping is built from this output rather than from the
    vendor's documentation — names are the part of a third-party feed most
    likely to differ from what is written down.
    """
    from pitchprob.data.adapters.odds_api_io import (
        MARKET_NAMES,
        observed_markets,
        scrub_http_error,
    )

    configure_logging(get_settings().log_level)
    client = _oddsio_client()
    try:
        payload = client.fetch_odds(
            event_id, bookmakers=[b.strip() for b in books.split(",")]
        )
    except httpx.HTTPError as exc:
        typer.echo(scrub_http_error(exc))
        raise typer.Exit(code=1) from None
    for book, markets in observed_markets(payload).items():
        typer.echo(f"{book}:")
        for name in markets:
            mapped = MARKET_NAMES.get(name)
            typer.echo(f"  {name}  ->  {mapped or '(unmapped, skipped)'}")


@oddsio_app.command("poll")
def oddsio_poll(
    books: Annotated[
        str,
        typer.Option(
            help="Comma list of feed bookmaker names; empty = the key's "
            "selected books (avoids a 403 on an unselected book)"
        ),
    ] = "",
    within_hours: Annotated[
        int, typer.Option(help="Only fixtures kicking off within this window")
    ] = 48,
    league: Annotated[
        str | None, typer.Option(help="Feed league slug to restrict to")
    ] = None,
    max_events: Annotated[
        int, typer.Option(help="Request cap per sweep (free tier: 100 req/h)")
    ] = 40,
    all_events: Annotated[
        bool,
        typer.Option(
            "--all-events",
            help="Request every in-window fixture, not just tape-anchored ones "
            "(spends far more of the free-tier quota)",
        ),
    ] = False,
) -> None:
    """Sweep upcoming PL fixtures on the feed into the tape (source=odds-api-io).

    The scheduled poller behind the speaking loop (ADR 0016): run it on the
    always-on machine every few minutes, denser near kickoff. Feed prices
    stay UNVERIFIED until quote-check clears them (ADR 0014).
    """
    from pitchprob.data.adapters.odds_api_io import scrub_http_error
    from pitchprob.services.recorder import record_oddsio_snapshot

    configure_logging(get_settings().log_level)
    client = _oddsio_client()
    book_list = [b.strip() for b in books.split(",") if b.strip()] or None
    try:
        with session_scope() as session:
            summary = record_oddsio_snapshot(
                session,
                client,
                books=book_list,
                within_hours=within_hours,
                league=league,
                max_events=max_events,
                anchor_only=not all_events,
            )
    except httpx.HTTPError as exc:
        typer.echo(scrub_http_error(exc))
        raise typer.Exit(code=1) from None
    typer.echo(
        f"swept {summary.events_requested} fixtures in the next {within_hours}h "
        f"({summary.events_in_window} in window, {summary.events_priced} priced) "
        f"-> {summary.ticks_inserted} ticks from "
        f"{', '.join(summary.bookmakers) or '(none)'}"
    )
    if summary.stop_reason is not None:
        typer.echo(f"stopped early: {summary.stop_reason}")


@oddsio_app.command("record")
def oddsio_record(
    event_id: Annotated[str, typer.Option(help="Feed event id")],
    books: Annotated[
        str, typer.Option(help="Comma list of feed bookmaker names")
    ] = "Betclic PL,STS PL",
    movements: Annotated[
        bool,
        typer.Option(help="Also pull each book's full movement history (1X2)"),
    ] = False,
) -> None:
    """Append this feed's quotes to the tape as source=odds-api-io.

    With --movements, each book's recorded price history is appended too:
    one tick per movement at the instant the book moved, which is the only
    resolution fine enough for `pitchprob study latency`.
    """
    from pitchprob.data.adapters.odds_api_io import (
        SOURCE,
        event_ref,
        parse_odds,
        scrub_http_error,
    )

    configure_logging(get_settings().log_level)
    client = _oddsio_client()
    book_list = [b.strip() for b in books.split(",")]
    now = datetime.now(tz=UTC)
    try:
        payload = client.fetch_odds(event_id, bookmakers=book_list)
        ticks = parse_odds(payload, observed_at=now)
        if movements:
            reference = event_ref(payload)
            for book in book_list:
                ticks.extend(
                    client.fetch_movements(reference, bookmaker=book, market="1x2")
                )
    except httpx.HTTPError as exc:
        typer.echo(scrub_http_error(exc))
        raise typer.Exit(code=1) from None
    with session_scope() as session:
        for tick in ticks:
            session.add(
                OddsTick(
                    source=SOURCE,
                    sport_key=tick.sport_key,
                    event_id=tick.event_id,
                    commence_time=tick.commence_time,
                    home_team=tick.home_team,
                    away_team=tick.away_team,
                    bookmaker=tick.bookmaker,
                    market=tick.market,
                    selection=tick.selection,
                    line=tick.line,
                    price=Decimal(str(tick.price)),
                    observed_at=tick.observed_at or now,
                )
            )
    typer.echo(f"appended {len(ticks)} ticks from {', '.join(book_list)}")


quote_app = typer.Typer(
    help="Validate the feed against what the operator actually sees."
)
app.add_typer(quote_app, name="quote-check")


@quote_app.command("log")
def quote_check_log(
    book: Annotated[str, typer.Option(help="Feed bookmaker name, e.g. 'Betclic PL'")],
    home: Annotated[str, typer.Option(help="Home team as the feed names it")],
    away: Annotated[str, typer.Option(help="Away team as the feed names it")],
    market: Annotated[str, typer.Option(help="1x2 | ou | ah")],
    selection: Annotated[str, typer.Option(help="home/draw/away or over/under")],
    seen: Annotated[str, typer.Option(help="The price on the bookmaker's screen")],
    line: Annotated[str | None, typer.Option(help="Line for ou/ah")] = None,
    event_id: Annotated[str | None, typer.Option(help="Feed event id")] = None,
    notes: Annotated[str | None, typer.Option(help="Free-text note")] = None,
) -> None:
    """Record what you saw on the book's own site, right now.

    This is the ground truth the feed is judged against — type the price you
    can actually see, not the one you expected.
    """
    configure_logging(get_settings().log_level)
    with session_scope() as session:
        try:
            check = log_quote_check(
                session,
                bookmaker=book,
                home_team=home,
                away_team=away,
                market=market,
                selection=selection,
                price_seen=Decimal(seen),
                line=Decimal(line) if line else None,
                event_id=event_id,
                notes=notes,
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        feed = (
            f"{check.price_feed} (observed {check.feed_observed_at:%Y-%m-%d %H:%M}Z)"
            if check.price_feed is not None and check.feed_observed_at is not None
            else "(the feed had nothing for this selection)"
        )
    typer.echo(f"logged: you saw {seen}, feed said {feed}")


@quote_app.command("report")
def quote_check_report_command() -> None:
    """Has the feed earned the right to be a scanner input yet?"""
    configure_logging(get_settings().log_level)
    with session_scope() as session:
        _verdicts, report = quote_check_report(session)
    typer.echo(report)


# --- Phase 3: the risk layer (ADR 0015) ----------------------------------

risk_app = typer.Typer(
    no_args_is_help=True,
    help="Exposure limits, the drawdown circuit breaker, and the weekly "
         "'what the tape says' report.",
)
app.add_typer(risk_app, name="risk")


@risk_app.command("status")
def risk_status(
    bankroll: Annotated[
        float, typer.Option(help="Notional bankroll in PLN")
    ] = 500.0,
) -> None:
    """Where the ledger stands against every limit, right now."""
    from pitchprob.betting.risk import RiskLimits, breaker_tripped
    from pitchprob.services.ledger import realized_drawdown

    configure_logging(get_settings().log_level)
    limits = RiskLimits.for_bankroll(Decimal(str(bankroll)))
    with session_scope() as session:
        drawdown = realized_drawdown(session, bankroll=limits.bankroll_pln)
        tripped = breaker_tripped(drawdown, limits=limits)
    typer.echo(
        f"stake band {limits.min_stake_pln}-{limits.max_stake_pln} PLN | "
        f"per match {limits.max_match_stake_pln} | "
        f"per day {limits.max_daily_stake_pln} | "
        f"open picks {limits.max_open_picks}"
    )
    typer.echo(
        f"equity {drawdown.equity_pln:.2f} PLN (peak "
        f"{drawdown.peak_equity_pln:.2f}) | drawdown "
        f"{drawdown.drawdown_pln:.2f} of {limits.max_drawdown_pln:.2f} PLN "
        f"({100 * drawdown.fraction_of(limits.bankroll_pln):.1f}% of bankroll)"
    )
    typer.echo(
        "circuit breaker: TRIPPED — `pick log` refuses new bets "
        "(--override-risk to bypass, and it will be recorded)"
        if tripped
        else "circuit breaker: open"
    )


@risk_app.command("report")
def risk_report_command(
    days: Annotated[int, typer.Option(help="Money window, in days")] = 7,
    bankroll: Annotated[
        float, typer.Option(help="Notional bankroll in PLN")
    ] = 500.0,
    out: Annotated[
        str | None, typer.Option(help="Also write the markdown report here")
    ] = None,
    notify: Annotated[
        bool,
        typer.Option(
            "--notify",
            help="Also push the report to Telegram (stdout if no token set)",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(help="With --notify, print instead of pushing to Telegram"),
    ] = False,
) -> None:
    """The weekly report: what the tape says about the bets you placed.

    The money section covers the window; CLV covers the whole ledger,
    because it is the decision variable and needs every observation. Below
    four ISO weeks of bets no confidence interval is published — a bootstrap
    over one block is a straight line, not an interval.
    """
    from pathlib import Path

    from pitchprob.betting.risk import RiskLimits
    from pitchprob.services.risk_report import weekly_report

    configure_logging(get_settings().log_level)
    with session_scope() as session:
        result = weekly_report(
            session,
            now=datetime.now(tz=UTC),
            days=days,
            limits=RiskLimits.for_bankroll(Decimal(str(bankroll))),
        )
    if out is not None:
        Path(out).write_text(result.report + "\n", encoding="utf-8")
        typer.echo(f"report written to {out}")
    typer.echo(result.report)
    if notify:
        try:
            _build_notifier(dry_run=dry_run).send(result.report)
        except RuntimeError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from None
        typer.echo("report pushed")


@app.command("status")
def status_command() -> None:
    """The pre-round freshness gate: is the data current enough to act on?

    Four checks — results, xG, tape, ledger — each naming the command that
    fixes it. Exits non-zero when anything is stale, so scripts can gate on
    it. Run it before every scan on a match day; expect it to fail loudly
    in pre-season, because that is the honest answer until the first round
    is ingested.
    """
    from pitchprob.services.freshness import season_status

    configure_logging(get_settings().log_level)
    with session_scope() as session:
        result = season_status(session, now=datetime.now(tz=UTC))
    for check in result.checks:
        marker = "OK   " if check.ok else "STALE"
        typer.echo(f"{marker} {check.name:<8} {check.detail}")
        if check.action is not None:
            typer.echo(f"      fix: {check.action}")
    if not result.all_ok:
        raise typer.Exit(code=1)
    typer.echo("all checks passed — the data is current enough to act on")


def _build_notifier(*, dry_run: bool) -> "Notifier":
    """The notifier shared by `watch` and `risk report --notify`.

    Telegram when both credentials are set and not a dry run; stdout
    otherwise, with a one-line notice so a missing token is never silent.
    """
    from pitchprob.services.alerts import StdoutNotifier, TelegramNotifier

    settings = get_settings()
    if dry_run:
        return StdoutNotifier()
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    typer.echo(
        "no PITCHPROB_TELEGRAM_BOT_TOKEN/CHAT_ID set — printing to stdout "
        "(set both to push to Telegram, or pass --dry-run to silence this notice)"
    )
    return StdoutNotifier()


@app.command("watch")
def watch_command(
    interval: Annotated[
        int, typer.Option(help="Seconds between passes (ignored with --once)")
    ] = 300,
    once: Annotated[
        bool, typer.Option(help="Run a single pass and exit")
    ] = False,
    min_edge: Annotated[
        float,
        typer.Option(help="Minimum effective edge vs the sharp anchor before an alert"),
    ] = 0.02,
    window_hours: Annotated[
        int, typer.Option(help="How far ahead (hours) to scan for fixtures")
    ] = 72,
    book: Annotated[
        list[str] | None,
        typer.Option("--book", help="Restrict to these bookmakers (repeatable)"),
    ] = None,
    market: Annotated[
        list[str] | None,
        typer.Option("--market", help="Restrict to these markets (repeatable)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(help="Print alerts to stdout instead of pushing to Telegram"),
    ] = False,
    self_test: Annotated[
        bool,
        typer.Option(
            "--self-test",
            help="Send one clearly-marked test alert and exit — confirms "
            "delivery works before any real signal exists",
        ),
    ] = False,
    discover_chat: Annotated[
        bool,
        typer.Option(
            "--discover-chat",
            help="Print the numeric chat ids that have messaged the bot, then "
            "exit — paste one into PITCHPROB_TELEGRAM_CHAT_ID",
        ),
    ] = False,
) -> None:
    """The speaking loop (ADR 0016): scan upcoming PL quotes, push GRAJ/LEAD.

    Feed-driven and silent by default — it speaks only when an effective edge
    clears the threshold, the anchor is fresh, and the bet fits the risk
    limits (ADR 0015). Feed prices are unvalidated, so they push as UNVERIFIED
    leads, never an auto-PLAY (ADR 0014). Runs forever unless --once; each
    standing edge is announced once (the ``sent_alerts`` dedup log).
    """
    from time import sleep

    from pitchprob.betting.promos import parse_disabled
    from pitchprob.services.alerts import discover_chat_ids, format_test_alert
    from pitchprob.services.watch import run_watch

    settings = get_settings()
    configure_logging(settings.log_level)

    if discover_chat:
        if not settings.telegram_bot_token:
            typer.echo("PITCHPROB_TELEGRAM_BOT_TOKEN is not set (see .env)")
            raise typer.Exit(code=1)
        chats = discover_chat_ids(settings.telegram_bot_token)
        if not chats:
            typer.echo(
                "no chats have messaged the bot yet — open Telegram, send the "
                "bot any message, then re-run. (chat_id must be numeric, not "
                "the bot's @username)"
            )
            raise typer.Exit(code=1)
        for chat_id, label in chats:
            typer.echo(f"chat_id={chat_id}  ({label})")
        return

    notifier = _build_notifier(dry_run=dry_run)

    if self_test:
        try:
            notifier.send(format_test_alert())
        except RuntimeError as exc:
            typer.echo(str(exc))
            typer.echo(
                "hint: if this is a 403/'chat not found', your chat_id is wrong "
                "— run `pitchprob watch --discover-chat` for the numeric id"
            )
            raise typer.Exit(code=1) from None
        typer.echo("test alert sent")
        return

    books = frozenset(book) if book else None
    markets = frozenset(market) if market else None
    window = timedelta(hours=window_hours)

    while True:
        with session_scope() as session:
            result = run_watch(
                session,
                notifier,
                min_edge=min_edge,
                window=window,
                markets=markets,
                books=books,
                disabled_promos=parse_disabled(settings.disabled_promos),
            )
        typer.echo(
            f"[{datetime.now(tz=UTC):%Y-%m-%d %H:%M:%S} UTC] "
            f"scanned {result.events_scanned} events · sent {len(result.alerts)} · "
            f"{result.suppressed_duplicates} already announced"
        )
        if settings.healthchecks_watch_url:
            # a missed heartbeat must not crash the loop
            with contextlib.suppress(httpx.HTTPError):
                httpx.get(settings.healthchecks_watch_url, timeout=10.0)
        if once:
            break
        sleep(interval)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
