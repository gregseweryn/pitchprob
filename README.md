# pitchprob

> Polska wersja / Polish summary: [README.pl.md](README.pl.md)

Football probability engine: estimates **calibrated probabilities** for match markets
(1X2, Over/Under, BTTS, Asian Handicap, Correct Score, …), computes expected value
against bookmaker odds, and validates itself with lookahead-free walk-forward backtests
benchmarked against the closing line.

> **Honesty policy (ADR 0004):** this project does *not* promise profits and does not
> claim to "beat the bookmaker". Its benchmark is the margin-removed Pinnacle closing
> line — the strongest publicly available probability estimate — and its reports state
> plainly when the models fall short of it. They usually do; that is the expected result
> and the reports are designed to show it rather than hide it.

## What works today (M1 core + M2 ML + M3 betting engine + M4 dashboard)

- **Data**: 21,589 matches across the top-5 European leagues (EPL, La Liga, Bundesliga,
  Serie A, Ligue 1), seasons 2014/15–2025/26, ingested from football-data.co.uk with
  bet365/Pinnacle/market-max/market-avg odds (early snapshot + closing where published) —
  ~1M odds quotes, zero silently dropped rows (hard failures are quarantined with
  reasons).
- **Models**: Dixon-Coles (time-decay weighted MLE with analytic gradients), independent
  Poisson (the baseline DC must beat), and Elo with an ordered-logit 1X2 mapping.
- **M2 ML layer**: per-match xG from Understat (99.98% coverage via the
  ``getLeagueData`` JSON API; raw responses are archived append-only under
  `data/understat/` — since the January 2026 FBref/Opta cutoff Understat is
  the only free xG source, so the raw material is snapshotted, not just
  cached), a leakage-free streaming feature builder (rolling
  goals/xG/shots/corners form, venue splits, rest days), an XGBoost 1X2 model with
  temporal-holdout early stopping, temperature/isotonic calibration, and a stacking
  ensemble (log-linear pool + per-class bias, ridge-fit on a temporal holdout). The
  prediction service serves the ensemble headline with component probabilities and
  stack weights alongside, cached per (league, data version).
- **Markets**: every goals market is derived from one score probability matrix
  (ADR 0002) — 1X2, Double Chance, Draw No Bet, totals at any line, BTTS, Correct
  Score, and Asian Handicap with full quarter-line split-stake settlement semantics.
- **Betting math**: implied probabilities, multiplicative and Shin overround removal,
  fair odds, EV, fractional Kelly.
- **M3 betting engine**: vig-aware bet selection (model probabilities log-linearly
  anchored to the de-margined market, hard price cap — ADR 0006), negative-binomial
  corners and cards models with O/U lines in every book, risk-tiered coupon
  generation (safe/balanced/value/high-risk bands) with per-leg machine-generated
  reasoning and an explicit independence caveat, and a fixtures.csv adapter so
  `pitchprob coupon` runs against the live upcoming-matches feed.
- **M4 dashboard** (`frontend/`, Next.js 15 + TypeScript + Tailwind — ADR 0007):
  fixture pricing with the full market book (all four models side by side, stack
  weights, every market family, corners/cards with their caveat), a coupon builder
  with per-leg reasoning, and the stored backtest history — disclaimers rendered as
  first-class content. `make web` starts API + dashboard on :8000/:3000.
- **Evaluation**: walk-forward backtester (its no-lookahead property is itself under
  test), log-loss / Brier / RPS / ECE, reliability tables, and staking simulation
  (flat + compounding Kelly) with closing-line value.
- **Interfaces**: a Typer CLI (`ingest / train / predict / backtest`) and a FastAPI
  read API (`/v1/leagues`, `/v1/matches`, `/v1/predictions`, `/v1/backtests`).

## Quickstart

```bash
make install                    # uv sync (Python 3.12, pinned via uv)

# Postgres (production target):
make db-up && make migrate      # Docker Postgres 16 on port 5433
# — or SQLite (zero-dependency dev fallback):
#   echo 'PITCHPROB_DATABASE_URL=sqlite:///data/pitchprob.db' > .env
#   uv run alembic upgrade head

uv run pitchprob ingest --all --from-year 2014 --to-year 2025
uv run pitchprob train --league E0
uv run pitchprob predict --league E0 --home "Manchester City" --away "Chelsea" \
    --odds 1.55,4.4,5.9
uv run pitchprob backtest --league E0 --start 2021-08-01
make serve                      # FastAPI on :8000, OpenAPI docs at /docs
make check                      # ruff + mypy --strict + 618 tests
```

Example output (real run, July 2026):

```
Manchester City vs Chelsea  (E0, trained on 4560 matches to 2026-05-24)
expected goals: 2.11 - 0.98

1X2                home   draw   away
  dixon_coles       62.7%  21.6%  15.8%
  elo               68.2%  18.8%  13.0%
...
Value vs offered odds (Dixon-Coles + Shin market blend, quarter-Kelly):
  home  price 1.55  fair 1.59  Expected value -0.027  kelly 0.000
```

Negative EV at realistic prices is the *correct* answer most of the time.

## Honest results (walk-forward, EPL 2021-08 → 2026-05, weekly refits)

1,900 out-of-sample predictions, each made by a model trained strictly on matches
before it (1,730 with Pinnacle closing odds for the benchmark comparison):

| 1X2 metric | Dixon-Coles | Pinnacle closing (Shin de-margined) | gap |
|---|---|---|---|
| Log-loss | 0.9755 | 0.9464 | +3.1% |
| RPS | 0.2007 | 0.1917 | +4.7% |
| ECE (home win) | **0.021** | — | — |

The model is well calibrated (ECE 0.021) and lands within ~5% of the closing line on
RPS — close, and still behind the market, exactly as theory predicts for a
results-only model.

A flat-stake simulation betting every selection with model EV > +3% against the best
listed market price placed 2,189 bets: **ROI −1.8%**, hit rate 27.5%, max drawdown
116 units, mean closing-line value **+0.4%**. Slightly positive CLV with slightly
negative ROI over ~2,200 bets is the regime where variance dominates edge: the system
finds mild value signals, and none of them survive the margin with confidence. That
is the honest state of the art for a results-only model — the roadmap items (xG
features, ensembling, calibration layers) exist to close the gap, not to promise
crossing it.

Reproduce with: `uv run pitchprob backtest --league E0 --start 2021-08-01`
(results are stored in the `backtests` table).

### M3: what vig-aware selection did (and honestly did not) fix

Same protocol, flat stakes on selections with EV > 3% at best listed prices:

| E0 2021–26 | naive ROI | blended ROI (w=0.4, cap 8.0) | naive CLV | blended CLV |
|---|---|---|---|---|
| Dixon-Coles | −0.2% (2,194 bets) | −0.7% (1,560 bets) | +0.4% | **+1.3%** |
| Ensemble | −11.2% (2,192 bets) | −8.9% (1,537 bets) | +0.5% | **+1.6%** |

Anchoring to the market improved every diagnostic — 30% fewer bets, higher hit
rates, closing-line value tripled — but it cannot rescue a model with biased
tails: under *identical* selection the ensemble still loses 8× more than
Dixon-Coles. The ensemble's calibration win (ECE 0.014) was measured on the
home outcome; its draw/away tails are where the losing bets come from.
Calibration where you display is not calibration where you bet. Consequently
the current recommendation baked into the docs: **the betting path uses
Dixon-Coles probabilities; the ensemble serves the headline display** until
per-class calibration lands in M4. ROI confidence bands at ~1,500 bets are
roughly ±5pp, so DC-blended's true edge is statistically indistinguishable
from zero — exactly what an honest engine should report at this stage.

Reproduce with: `uv run pitchprob backtest --league E0 --start 2021-08-01
--model ensemble --refit-days 28 --selector blended`.

### M4.5: the calibration experiment (a negative result worth keeping)

The M3 hypothesis — fix the ensemble's draw/away tails and its betting
losses follow — was tested with `CalibratedEnsembleModel` (per-class
recalibration on a chronological holdout disjoint from the stack holdout;
backtests now report ECE for *all three* classes):

| E0 2021–26 (n=1730, blended selector) | log-loss | ECE h/d/a | ROI | CLV |
|---|---|---|---|---|
| Dixon-Coles | 0.9725 | — | **−0.7%** | +1.3% |
| Raw ensemble | 0.9756 | .014 / – / – | −8.9% | +1.6% |
| + isotonic calibration | 1.3012 | .054/.072/.062 | −8.1% | +1.0% |
| + temperature calibration | 0.9815 | **.017/.018/.019** | −8.2% | +1.3% |

Two findings. First, isotonic **detonated** — step functions overfit
190-match calibration windows into worse-than-uniform log-loss; temperature
(one parameter) is the default for a reason. Second and more important:
temperature delivered the best per-class calibration of any model tested,
and the betting ROI *barely moved*. The ensemble's betting failure is not
marginal miscalibration — it is conditional, price-correlated error that no
marginal recalibration can repair. **Marginally calibrated is not
conditionally exploitable.** The betting path stays on Dixon-Coles; the
next credible attack on the gap is training on regions where the model has
demonstrated closing-line value, not post-hoc probability surgery (M5+).

Reproduce with: `... --model ensemble-cal --calibration temperature`.

### M5: the availability experiment (null result, money saved)

The question that gates all further data spend: *do player-availability
features improve match probabilities?* 40,500 pre-match injury/absence
records (API-Football free tier, 5 leagues, seasons 2022–24; 99%+ team
resolution) became `absences_home/away` features with strict coverage
semantics (0 = covered and none listed, NaN = no coverage). A/B on
identical data — GBM, E0, walk-forward 2023-08 → 2025-06, 760 predictions,
only the ablation differing:

| | log-loss | RPS | flat ROI (close) | flat ROI (early) |
|---|---|---|---|---|
| absences active | 0.96326 | 0.19718 | −8.9% | −6.9% |
| absences ablated | 0.96431 | 0.19719 | −6.3% | −8.8% |

**Null.** Paired block bootstrap by ISO week: log-loss delta −0.0011
(p=0.71), RPS delta −0.00001 (p=0.99), ROI delta null on both simulation
clocks (close p=0.31, early p=0.38), and the paired mean-CLV delta null
(early clock p=0.17) — nothing clears its 95% interval. Sharp CLV (the ADR
0011 primary endpoint) is deeply negative in *both* arms and barely moves
with absences (early clock: −5.4% active vs −5.6% ablated), consistent with
M7's finding that the
selector's bets lose to the closing line irrespective of team-news features.
The market already prices team news, and results-based ratings absorb
absence effects implicitly.
Consequences drawn: no API-Football paid upgrade (live absence counts can't
earn a subscription their historical counterpart earns nothing from), and no
LLM news-scoring layer (if machine-readable absence lists add zero,
LLM-scored press conferences — a noisier proxy for the same information —
start from a weaker position). Caveats stated: counts are crude (a star and
a reserve weigh the same), one league, and possibly post-hoc-edited source
lists — which would bias *toward* finding value, strengthening the null.

**Methodological correction (2026-07-20).** The originally published table
(active log-loss 0.9639) understated the test: the passthrough feature family
(`absences_home/away`) was never delivered to the *prediction* row —
`features_for` built it from history-derived state only, so every out-of-sample
prediction silently saw NaN absences in both arms. The A/B therefore measured
the *training-side* effect alone. The channel is now wired end to end
(`features_for`/`match_probabilities_at` take the passthrough values;
`default_predict` forwards them from the evaluation row), with a train/serve
parity test covering the passthrough column so it cannot regress. Re-running
with absences genuinely reaching the booster moved only the active arm
(0.9639 → 0.9633); the ablated arm is unchanged to the digit (0.9643), which
is exactly the fingerprint of the fix — and the verdict still holds. The
correction strengthens the null rather than weakening it: the feature now had
its full opportunity to matter, at train *and* serve time, and did not.

Reproduce with: `uv run pitchprob experiment compare --league E0
--start 2023-08-01 --end 2025-06-30 --model gbm --refit-days 28
--vs ablate=absences` (add `--at open` for the syndicate-clock row).

### M7: the inference engine and true CLV (ADR 0010)

An audit against professional-syndicate practice found that the previously
reported "CLV" had no timing dimension: both its legs (settlement price and
fair probability) came from the closing snapshot, so it measured cross-book
price dispersion at the close, not "did the market move toward our price
after we bet". Meanwhile ~1M stored odds quotes — early-snapshot prices for
four books across 1X2, OU 2.5 and Asian handicap — were never used.

> **Naming caveat (audit 2026-07-20):** what this README, the CLI flag
> `--at open` and the stored metrics call the "open" is **not the market
> open**. football-data.co.uk collects its non-closing quotes on Friday
> afternoons (weekend fixtures) and Tuesday afternoons (midweek) — a late
> pre-match **early snapshot**, roughly T-3 to T-1 before kickoff. The prose
> below says "early snapshot"; the flag and metric names keep "open" for
> compatibility with stored runs. The negative true-CLV verdicts are
> *conservative* under this correction: the measured early→close window is
> shorter than a true open→close window, so the market's move against these
> bets is understated, not overstated. No claim in this project refers to
> the true market open.

M7 rebuilt the evaluation loop around them, changing **no model**:

- **Two simulation clocks.** `--at close` reproduces the legacy protocol
  bit-for-bit (regression-pinned to every published digit above). `--at open`
  bets the early snapshot only — early prices, early anchor — and
  measures **true CLV = early-snapshot price × Shin(closing fair) − 1**.
- **The beatable benchmark, measured for the first time**: on the E0 subset
  (n=1,730) the Pinnacle *early-snapshot* line scores log-loss **0.95018**
  vs the close's 0.94640. Dixon-Coles at 0.97248 (28d refits) is +2.3%
  behind the early snapshot vs +2.7% behind the close. The early snapshot is
  the weaker target, but only by ~0.4pp — no free lunch, and now the right
  gap is on the record.
- **OU 2.5 and Asian handicap betting** from the same score matrix, priced at
  the quoted early-snapshot line inside the walk-forward loop; realized
  settlement (incl. quarter-line split stakes) is property-tested to agree
  cell-for-cell with the probability-side markets module. AH CLV exists only
  where the closing line still matches the early-snapshot line (~57% of
  matches on a 2025 E0 sample; coverage is reported, not hidden).
- **Honest inference, finally**: every staking block now carries
  block-bootstrap confidence intervals (ISO-week blocks — bets within a
  round are correlated and IID resampling would flatter them), delivering
  what ADR 0004 promised. At ~260 bets the ROI interval is ±16pp: printed,
  not footnoted.
- **An experiment registry**: `pitchprob experiment run --name …` stores
  content-hashed runs; `pitchprob experiment compare … --vs key=value` runs
  a paired A/B on identical fixtures and block-bootstraps Δlog-loss/ΔRPS/
  ΔROI/ΔCLV — anything that misses the 95% interval is labeled a **null** in
  the generated report.

#### The first five-league, three-market, true-CLV baseline (and its verdict)

Dixon-Coles, weekly refits, 2021-08 → 2026-05, blended selector (w=0.4, cap
8.0, EV > 3%) betting 1X2 + OU 2.5 + AH at early-snapshot best prices:

| league | preds | model LL | early LL | close LL | bets | ROI (95% CI) | true CLV (95% CI) | p(CLV) |
|---|---|---|---|---|---|---|---|---|
| E0 | 1,900 | 0.9755 | 0.9502 | 0.9464 | 2,888 | −3.2% (−8.8, +2.2) | **−0.76%** (−1.12, −0.38) | .0005 |
| SP1 | 1,900 | 0.9828 | 0.9639 | 0.9630 | 2,644 | −5.3% (−11.5, +1.1) | **−1.28%** (−1.68, −0.86) | .0005 |
| D1 | 1,530 | 1.0009 | 0.9743 | 0.9712 | 2,107 | −4.4% (−11.1, +2.2) | **−0.78%** (−1.22, −0.31) | .003 |
| I1 | 1,900 | 0.9900 | 0.9705 | 0.9684 | 2,511 | −5.4% (−11.0, +0.2) | **−1.14%** (−1.60, −0.69) | .001 |
| F1 | 1,678 | 1.0024 | 0.9822 | 0.9820 | 2,535 | −6.2% (−12.0, −0.6) | **−0.90%** (−1.36, −0.43) | .0005 |

Three honest findings, in decreasing order of comfort:

1. **The beatable target is measured.** The early-snapshot line is 0.02pp
   (F1) to 0.4pp (E0) softer than the close in log-loss; the model sits
   2.0–2.7% behind the *early snapshot*. Ligue 1's line barely sharpens
   between the early snapshot and the close.
2. **The old "+1.3% CLV" was line-shopping value, not timing value.** Under
   the true clock the blended strategy's mean CLV is *significantly negative
   in every league*: the closing line systematically moves **against** its
   bets. Where the model disagrees with the early-snapshot price, the
   market's subsequent move sides with the market, on average. Any Phase 2 bet gate
   must find the minority of divergences the market later confirms — the
   aggregate says the default selector should not be betting early.
3. **One number that is *not* a headline:** the naive selector shows +6.3%
   AH CLV — an artifact suspect, not an edge: it selects best-price (max
   book) outliers against a Pinnacle fair, on the ~62% of matches whose AH
   line never moved. Decomposing this is Phase 1 work; it is recorded here
   so nobody mistakes it for alpha.

Reproduce with: `uv run pitchprob experiment run --name phase0-open-blended-E0
--league E0 --start 2021-08-01 --at open --markets 1x2,ou,ah --selector blended`
(runs stored content-hashed in the `backtests` table).

#### The movement study: does the line move toward the model? (a null that teaches)

For every prediction with both books, compare three directions: the market's
early→close movement `m`, the model's divergence from the early snapshot
`d`, and the realized outcome's direction `t` (all as probability vectors;
`m·d > 0` means the market moved toward the model). Dixon-Coles, 28-day
refits, 2021-08 → 2026-05:

| league | n | P(close moved toward truth) | P(moved toward model) | mean m·d (95% CI) | largest-divergence bucket |
|---|---|---|---|---|---|
| E0 | 1,730 | 0.534 | 0.479 | −0.00014 (−.00032, +.00003) | −0.00055 |
| SP1 | 1,707 | 0.523 | 0.480 | −0.00010 (−.00028, +.00010) | −0.00033 |
| D1 | 1,373 | 0.552 | 0.487 | **−0.00032** (−.00055, −.00007, p=.012) | −0.00058 |
| I1 | 1,717 | 0.535 | 0.469 | −0.00010 (−.00033, +.00017) | +0.00011 |
| F1 | 1,525 | 0.527 | 0.517 | +0.00007 (−.00017, +.00030) | +0.00034 |

The sanity check passes — the close is sharper than the early snapshot
everywhere. The verdict does not: **model-vs-early-snapshot divergence is an
error signal, not a steam signal.** The market does not follow Dixon-Coles anywhere; in Germany it
significantly moves *against* it, and in E0/SP1/D1 the effect is worst
exactly where the model disagrees most. This explains the negative true CLV
above mechanistically, and it sets the honest prior for the meta-gate phase:
a gate keyed on divergence magnitude alone would point the wrong way; any
positive-CLV subset must come from *conditional* features (book dispersion,
price band, league — note France) — or the gate's correct output is "do not
bet early with this model", which a real-money operation must be able to
say. Reproduce with: `uv run pitchprob study movement --league E0 --start
2021-08-01`.

#### Phase 2a: the CLV meta-gate (verdict: no early edge — and one trap disarmed)

A second model (XGBoost regressor, walk-forward, 90-day refits, whitelisted
bet-time features only) was trained to predict each candidate's **sharp CLV**
— Pinnacle early-snapshot price vs Pinnacle close fair, the timing-only
label — and to
bet only candidates predicted above a buffer. Design and prespecified
endpoints: `docs/superpowers/specs/2026-07-18-phase2-clv-meta-gate.md`.

The paired comparison against the blended baseline looked like a win: "CLV"
significantly better in 4 of 5 leagues. The primary endpoint says otherwise
— realized `clv_sharp` of the gated bets:

| league | gated bets | clv_exec (best price) | **clv_sharp** (95% CI) | p |
|---|---|---|---|---|
| E0 | 299 | +0.11% | **−1.32%** (−2.43, −0.07) | .037 |
| SP1 | 304 | +0.74% | +0.17% (−0.73, +1.04) | .70 |
| D1 | 424 | +1.23% | +0.25% (−0.70, +1.26) | .64 |
| I1 | 383 | +7.13% | −0.66% (−1.53, +0.20) | .15 |
| F1 | 201 | +0.96% | −0.67% (−1.56, +0.17) | .14 |

Pooled ≈ −0.4%. The gate concentrated 60–75% of its bets in Asian handicap,
where the best-vs-sharp price spread is widest — Serie A's +7.1% "CLV" at
best prices collapses to −0.66% against the sharp book. **The apparent
improvement is line-shopping value, not timing edge**, exactly the artifact
the two-label design existed to catch. Prespecified conclusion, published as
written: *no early timing edge with current models; the betting
recommendation remains bet-at-kickoff with line shopping; real-money betting
stays locked.* Every backtest now reports `clv_sharp` alongside `clv_exec`
for every selector so this misreading cannot recur (ADR 0011).

#### Phase 5 has started: the odds tape is rolling

`pitchprob record-odds` (ADR 0012) appends daily snapshots of every visible
bookmaker to the append-only `odds_ticks` table via The Odds API free tier.
First live snapshot (2026-07-19): the 2026/27 EPL opening round was already
priced — 630 quotes, 21 bookmakers including a live Pinnacle reference and
Betfair exchange prices. Polish-licensed books are *not* carried by the API;
executed PL prices will be captured in the forward pick ledger at bet time,
and every real-money conclusion will be computed on those, not on EU best
prices.

#### The forward pick ledger and the PL value scanner (ADR 0013)

The measurement instrument for 2026/27. Two commands, one thesis: after the
Phase 0–2a nulls the model is not the edge, so the **sharp anchor is
primary** — Shin-de-margined Pinnacle from the tape — and the model fair is
informational only. Because Polish books collect a **12% turnover tax on the
stake**, every comparison runs on *effective* prices (`quoted × 0.88`, or
`× 1.0` under a tax-free promo). That tax is larger than any edge this
project has ever measured, which is why "NO BET" is the expected output.

```bash
# Scanner: the operator types the PL prices he sees; the system verdicts them.
uv run pitchprob scan "arsenal" --market ou --selection over --line 3.0 \
    --quote betclic:2.10 --quote sts:2.05 --tax-free betclic
```

```
Arsenal vs Coventry City — kickoff 2026-08-21 19:00 UTC (event eb2553d10d63dc912b99f8fd0d675721)
ou 3.0 over | anchor: pinnacle 1.89 -> fair 50.0% | observed 2026-07-20 23:11 UTC (10h ago)
PLAY       betclic     quoted 2.10  eff 2.10  edge   +5.0%  promo +12.6pp
NO BET     sts         quoted 2.05  eff 1.80  edge   -9.8%
```

Same price, opposite verdicts: Betclic's "Gra bez podatku 2.0" (first 1,000
PLN of stakes unconditionally tax-free) is worth +13.6% of payout — more
than any model edge measured here, which is what makes it the default venue
for a 2–5 PLN measurement season. Promotions are priced as **instruments**,
not footnotes: `promo_ev` values a quote bare-and-taxed versus with its promo
(tax-free, boosted price, payout haircut for conditioned winnings), so
`promo_value` is the EV the promotion itself contributes.

Two refusals are deliberate. A line the tape does not currently quote yields
**NO ANCHOR**, never a comparison against a neighbouring line; and an anchor
older than 30 hours downgrades PLAY to **STALE** — the tape snapshots daily,
so freshness is payload, printed with every verdict, and a stale sharp line
is a prompt to fix the recorder rather than a basis for a bet.

```bash
# Ledger: log the executed bet, settle it, watch CLV — the decision variable.
uv run pitchprob pick log --match "arsenal" --market ou --selection over \
    --line 3.0 --book betclic --stake 5 --price 2.10 --tax-free
uv run pitchprob pick settle     # auto: results from `matches`, CLV from the tape
uv run pitchprob pick list
```

Each pick carries the **two-label CLV decomposition** (the ADR 0011 design
applied to real money): `clv_sharp` is the Pinnacle price at bet time versus
the closing fair — timing alone, auto-filled from the tape at log time so it
cannot be reconstructed favourably afterwards — while `clv_exec` uses the
price actually executed after tax or promo. Their difference is the
venue/shopping/promo component, which under the Phase 0–2a verdicts is the
only place value can live. When the season ends, the ledger will say which
one paid. Picks whose result cannot be matched are listed, never guessed;
tax-free stakes beyond the 1,000 PLN allowance are refused rather than
silently mispriced.

#### Corners, the line-latency map, and a Polish quote feed (ADR 0014)

Three audit follow-ups, each of which started by checking the claim against
the live API rather than the documentation. Two of the three assumptions did
not survive.

**Corners are real, Pinnacle prices them, and they arrive late.** Measured
2026-07-21: a fixture 31 days out returned nothing (and cost nothing —
empty responses are not billed); a fixture ~24 hours out returned Pinnacle
corner totals, corner handicaps and card totals. So the pilot records
corners for E0 fixtures inside a 26-hour window, one snapshot each:

```bash
uv run pitchprob record-corners --league E0 --within-hours 26 --reserve 60
```

That is **~43 of the 500 monthly credits**, taken from the headroom the main
tape leaves. Spending has two ceilings and both stops are printed, never
silent: the recorder refuses to spend below `--reserve`, because the main
1x2/ou/ah tape is the instrument the scanner and ledger anchor on, and an
experiment does not get to starve what it will be measured against.

**The latency map was not measurable from the existing tape — the audit was
wrong about that, and the correction is in the audit document.** The tape
held one snapshot; The Odds API carries no Polish-licensed bookmaker (the
22 books visible include `betclic_fr` and `winamax_fr`, not one PL book);
and a daily cadence quantises every delay to 24 hours when the phenomenon
lives in minutes. The module was built anyway, source-agnostic, and on
today's data it says so:

```
# Line-latency map — reference pinnacle, source (all)
- Reference moves of >= 2.0%: 0
- Resolution floor (median gap between reference snapshots): not defined

## No measurement
- the reference has only a single snapshot — a latency needs a series,
  and one observation is not one
```

Three biases are closed by construction, because each would flatter the
books: a bookmaker that never follows is *censored*, not fast, so the median
delay is taken over reactions only and always printed beside the reaction
rate; both sides are compared as de-margined probabilities, so widening a
margin is not an opinion; and a book absent when the sharp moved has no
baseline and leaves the denominator entirely.

**odds-api.io carries six Polish books — and no Pinnacle.** Its public
catalogue lists Betclic PL, STS PL, eFortuna PL, Betfan PL, LVbet PL and
Superbet as active, so it is a source of *followers*; the sharp anchor stays
Pinnacle from the main tape. Its `/odds/movements` endpoint returns
timestamped price histories, which is what will eventually make the latency
map say something.

The feed is wired in as **informational only**. `pitchprob quote-check log`
records what the operator sees on the bookmaker's own screen against what
the feed claimed at that instant, and a book is promoted only on a bar fixed
before any data existed: ≥30 checks, ≥95% agreement within 0.02, ≤10%
missing-or-stale. Until then `pitchprob scan --feed` returns **UNVERIFIED**
where it would otherwise say PLAY — the edge is still computed, because the
lead is real; the authorisation is not.

#### The risk layer: what the ledger refuses (ADR 0015)

Phase 3, on the agreed parameters — flat 2-5 PLN stakes, 500 PLN notional
bankroll. The obvious framing is the wrong one: at these stakes the roll
carries 100-250 bets, so **ruin is not the risk being managed**. Two others
are, and they set the thresholds.

The first is statistical. CLV is reported with a block bootstrap over ISO
weeks, which can price dependence between weeks but is blind to dependence
*inside* a fixture. Two bets on one match share the match, the team news and
usually the same price move — they enter the sample as two observations
while carrying about one observation of information, and the interval comes
out too narrow. So the per-fixture cap is set equal to the single-stake cap:
**one bet per fixture, enforced rather than intended.**

| limit | value | |
|---|---|---|
| stake band | 2-5 PLN | the program parameter |
| per fixture | 5 PLN | one bet per match |
| per day | 25 PLN | 5% of bankroll, ~5 bets |
| open picks | 15 | 75 PLN in flight |
| drawdown stop | 75 PLN | 15% of bankroll |

The second is fault detection: the likeliest way the season goes wrong is a
bug, not variance. The drawdown breaker is a smoke alarm, and it measures
**realized** P&L over settled picks in settlement order — open bets are
excluded, because counting positions that may still win would put 75 PLN of
phantom drawdown on the books, which is the entire threshold.

```bash
uv run pitchprob risk status     # every limit, and how close the breaker is
uv run pitchprob risk report     # the weekly "what the tape says"
```

A refused bet writes nothing. `--override-risk` places it anyway and stamps
the reason permanently on the pick, which the weekly report reads back — an
override that leaves no trace is worse than having no breaker, because it
makes the season unauditable while creating the impression of control.

The weekly report covers money over seven days and CLV over the whole
ledger, and **publishes no confidence interval below four distinct ISO weeks
of bets**: a bootstrap resampling one block returns that block every time,
so the "95% CI" collapses to zero width — the most confident-looking output
in the system, produced by the least evidence. It prints the means and marks
the section *not a finding*, the same way the latency map states its
resolution floor.

### M2 results: does the ML layer help? (same protocol, 28-day refits)

| E0 2021–26, closing-odds subset (n=1730) | log-loss | RPS | ECE (home) |
|---|---|---|---|
| Dixon-Coles | 0.9725 | 0.2002 | 0.021 |
| GBM (xG/form features) | 0.9796 | 0.2010 | 0.020 |
| **Ensemble (DC+Elo+GBM stack)** | 0.9756 | 0.2004 | **0.014** |
| Pinnacle closing (Shin de-margined) | 0.9464 | 0.1917 | — |

Honest read: on log-loss/RPS the ensemble lands *between* its components — with
components this close and correlated, a few hundred holdout matches cannot estimate
stack weights precisely enough to guarantee beating the best component (the test
suite documents this as irreducible dilution, not a bug). What the ensemble *does*
deliver is *calibration*: ECE drops ~30% versus any single model, which is the
property an EV engine actually depends on. On the full E0 history the stack weights
are DC 0.29 / Elo 0.04 / GBM 0.41 — the GBM earns the largest weight through error
diversity despite losing to DC solo.

**A trap found and reported, not hidden:** the ensemble's flat-staking simulation
(EV > 3% vs best market price) lost **−11.2% ROI over 2,192 bets despite +0.5% mean
CLV** — its bias-corrected draw/away probabilities push more marginal longshots over
the EV threshold, exactly where the favourite-longshot bias makes market prices most
punishing (hit rate 25.9% vs DC's 28.0%). Naive EV thresholds over-select longshots;
vig-aware bet selection is the designated M3 betting-engine problem.

## Architecture

Modular monolith (ADR 0001), Python 3.12, src layout, DDD-flavored bounded contexts:

```
src/pitchprob/
├── core/         config (pydantic-settings), db session, logging, errors
├── data/         adapters (football-data.co.uk) → domain records → repositories
│                 + dataset.py read-models (DB → training frames)
├── markets/      pure functions: score matrix → every goals market   (ADR 0002)
├── models/       Dixon-Coles, Poisson, Elo (+ JSON param round-trips)
├── betting/      odds math (Shin/multiplicative de-margin), EV, Kelly,
│                 realized settlement (1X2/OU/AH incl. quarter lines)
├── evaluation/   walk-forward backtester, metrics, staking simulation,
│                 block-bootstrap significance (ADR 0004, ADR 0010)
├── services/     market-book construction, backtest harness (two clocks:
│                 close/open), experiment registry + paired comparison
├── api/          FastAPI read API
└── cli/          Typer commands
```

Postgres 16 is the system of record (long-format odds/predictions, ADR 0003) with
Alembic migrations; the code is dialect-portable and runs unmodified on SQLite for
zero-dependency development. See `docs/adr/` for the 15 architecture decision
records and `docs/superpowers/specs/` for the approved milestone design.

## Testing

618 tests: hand-computed reference values for every formula, hypothesis property tests
(market partitions sum to 1, quarter-line AH EV ≡ mean of adjacent half lines, Shin
books renormalize, realized settlement ≡ the probability-side markets module
cell-for-cell, block-bootstrap scale equivariance), analytic-vs-numeric gradient
checks, synthetic-data parameter recovery, a no-lookahead proof for the backtester,
and offline CLI/API integration tests. `mypy --strict` and `ruff` clean.

```bash
make test        # unit tests (no DB needed)
make test-int    # Postgres integration tests (needs make db-up)
```

## Roadmap

| Milestone | Scope |
|---|---|
| **M1 ✓** | Probability core: data, DC/Poisson/Elo, markets, EV, honest backtests, CLI + API |
| **M2 ✓** | xG (Understat), feature builder, XGBoost, calibration, stacking ensemble, model cache |
| **M3 ✓** | Vig-aware selection, NegBin corners/cards, risk-tiered coupons with reasons, fixtures feed |
| **M4 ✓** | Next.js dashboard: market books, coupon builder, backtest history |
| **M5 ✓** | Availability experiment (API-Football free tier): 40.5k injury records, measured verdict below |
| **M6 ✓** | Two production images + compose stack profile + CI (ADR 0009) |
| **M7 ✓** | Inference engine: true CLV at the early snapshot, OU/AH betting, block-bootstrap CIs, experiment registry (ADR 0010) |

## Deployment (M6, ADR 0009)

```bash
make stack-up      # builds Dockerfile.api + frontend/Dockerfile, boots
                   # db + api + web; API migrates on startup, dashboard on :3000
make stack-down
```

Two non-root images (python:3.12-slim/uv and node:22-alpine/Next standalone),
configuration via environment only, healthcheck-gated startup. Dev flows are
untouched: `make db-up` still starts just Postgres. CI (GitHub Actions)
mirrors the local gates exactly — ruff + mypy + full pytest against a
Postgres service container, eslint + tsc + build, plus both image builds.
Kubernetes/queues stay out until a measured need exists.
