/** Formatting conventions (DESIGN.md): probabilities one decimal, prices two. */

export function pct(value: number): string {
  return `${(100 * value).toFixed(1)}%`;
}

export function price(value: number): string {
  return value.toFixed(2);
}

export function signed(value: number, digits = 3): string {
  const text = value.toFixed(digits);
  return value >= 0 ? `+${text}` : text;
}

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** A block-bootstrap interval as stored in backtest metrics (ADR 0010). */
export type Interval = { lo?: number | null; hi?: number | null };

export function signedPct(value: number): string {
  const text = `${(100 * value).toFixed(1)}%`;
  return value >= 0 ? `+${text}` : text;
}

/** Render a 95% CI as "(−8.8%, +2.2%)"; null when either bound is absent —
 * the caller renders nothing rather than a fake-precise dash pair. */
export function ciRange(interval: Interval | undefined | null): string | null {
  if (!interval || interval.lo == null || interval.hi == null) return null;
  return `(${signedPct(interval.lo)}, ${signedPct(interval.hi)})`;
}

/** PLN to the grosz. Money arrives as a Decimal string; parse once, here. */
export function pln(value: number | string): string {
  return `${Number(value).toFixed(2)} PLN`;
}

export function signedPln(value: number): string {
  const text = Math.abs(value).toFixed(2);
  return `${value < 0 ? "−" : "+"}${text} PLN`;
}

/** How old the anchor is, in the coarsest unit that is still honest.
 *
 * The tape snapshots daily, so minute precision on a 10-hour-old quote would
 * imply a freshness nobody has. Under an hour we say minutes, because that
 * is the range where it changes a decision. */
export function ageLabel(hours: number): string {
  if (hours < 0) return "in the future";
  if (hours < 1) return `${Math.round(hours * 60)}m ago`;
  if (hours < 48) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** A dash, not a zero. Absent CLV means "not measurable yet" — printing 0.0%
 * would assert the market did not move, which we did not observe. */
export function pctOrDash(value: number | null | undefined): string {
  return value == null ? "—" : signedPct(value);
}
