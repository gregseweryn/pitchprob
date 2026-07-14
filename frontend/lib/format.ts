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
