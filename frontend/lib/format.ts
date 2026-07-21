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
