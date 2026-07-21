# Design system — pitchprob dashboard

Mood: "Sunday-morning racing form" — crisp broadsheet stats desk, daylight,
ink on paper, one measure of amber. The deliberate opposite of the
dark-neon sportsbook reflex and of terminal-quant dark mode.

## Color (OKLCH, restrained strategy)

| token | value | role |
|---|---|---|
| `--bg` | `oklch(0.985 0.003 91)` | page (true off-white, chroma toward brand hue) |
| `--surface` | `oklch(0.965 0.005 91)` | panels, table headers |
| `--line` | `oklch(0.90 0.008 91)` | hairline borders |
| `--ink` | `oklch(0.24 0.012 60)` | body text (≥4.5:1 everywhere) |
| `--ink-muted` | `oklch(0.45 0.015 70)` | secondary text (still ≥4.5:1 on bg) |
| `--gold` | `oklch(0.70 0.13 88)` | primary: bars, selection, emphasis fills |
| `--gold-ink` | `oklch(0.47 0.11 80)` | accessible amber for small text |
| `--gold-soft` | `oklch(0.94 0.035 91)` | amber tint fills |
| `--slate` | `oklch(0.45 0.07 258)` | away-side anchor, secondary data series |
| `--slate-soft` | `oklch(0.93 0.02 258)` | slate tint fills |
| `--brick` | `oklch(0.51 0.13 27)` | negative EV only |
| `--draw` | `oklch(0.72 0.012 91)` | draw share in probability bars |

Data-viz vocabulary: home = gold, draw = neutral, away = slate. Positive EV
= gold family; negative EV = brick. Accent never decorates.

## Typography

Geist Sans for UI, Geist Mono for every number (`tabular-nums` globally on
data cells). Fixed rem scale, ratio 1.2: 12.5 / 14 / 16 / 20 / 24 / 29px.
Probabilities always one decimal (`61.3%`), prices two (`2.05`).

## Components

Hairline-bordered panels (no shadows, no cards-in-cards), stacked
probability bars 6px tall with 2px gaps, dense tables with `--surface`
header rows.

**Verdict states (scanner).** Shape carries the meaning so the vocabulary
survives greyscale and colour-blindness: a *filled* badge is actionable
(PLAY, gold), an *outlined* one would be actionable but is conditional
(UNVERIFIED — an unvalidated feed price), a *neutral filled* one is the
routine rejection (NO BET), and a *dashed* one means the instrument could
not answer at all (STALE, NO ANCHOR). Every token pair used for these is
verified ≥4.5:1: gold-ink on gold-soft is 5.8:1, brick on brick-soft 5.3:1,
ink-muted on surface 6.7:1.

**Caveats are a component, not a footer.** `Caveats` renders the
disclaimer strings that arrive in the API payload as a labelled list
directly under the numbers they qualify. States: skeleton shimmer for model-fit latency (first request
per league takes seconds — say so in the skeleton), inline error banner
(brick tint), teaching empty states. Motion: 150–200ms ease-out state
transitions only; `prefers-reduced-motion` collapses to instant.
