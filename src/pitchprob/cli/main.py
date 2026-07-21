"""pitchprob CLI: ingest → train → predict → backtest.

Every command opens its own transactional session; ``_make_downloader`` is a
seam that tests replace with an offline fake.
"""

import json as json_lib
from datetime import UTC, date, datetime
from typing import Annotated, Any

import httpx
import typer

from pitchprob.core.config import get_settings
from pitchprob.core.db import session_scope
from pitchprob.core.errors import PitchprobError
from pitchprob.core.logging import configure_logging
from pitchprob.data.dataset import load_matches_frame
from pitchprob.data.orm import Backtest, ModelRun
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
from pitchprob.services.movement_study import run_movement_study
from pitchprob.services.prediction import DEFAULT_HALF_LIFE_DAYS, build_market_book
from pitchprob.services.recorder import (
    import_tape,
    record_snapshot,
    record_snapshot_to_csv,
)

app = typer.Typer(
    name="pitchprob",
    help="Football probability engine: honest, backtested market estimates.",
    no_args_is_help=True,
)


def _make_downloader() -> Downloader:
    settings = get_settings()
    return HttpDownloader(cache_dir=settings.data_dir / "raw")


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
    from pitchprob.data.understat_archive import SNAPSHOT_SUBDIR
    from pitchprob.data.xg_service import XgUpdateService

    configure_logging(get_settings().log_level)
    codes = list(LEAGUES) if all_leagues or not league else league
    downloader = HttpDownloader(
        cache_dir=get_settings().data_dir / "raw", headers=REQUIRED_HEADERS
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
