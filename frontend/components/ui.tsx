/** Shared UI vocabulary (DESIGN.md): hairline panels, dense tables, one
 * consistent form-control shape across every screen. */

import { cx } from "@/lib/format";

export function Panel({
  title,
  footnote,
  children,
  className,
}: {
  title?: string;
  footnote?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("rounded-md border border-line", className)}>
      {title ? (
        <h3 className="border-b border-line bg-surface px-4 py-2 text-sm font-medium">
          {title}
        </h3>
      ) : null}
      <div className="p-4">{children}</div>
      {footnote ? (
        <p className="border-t border-line px-4 py-2 text-xs leading-relaxed text-ink-muted">
          {footnote}
        </p>
      ) : null}
    </section>
  );
}

export function DataTable({
  head,
  rows,
  className,
}: {
  head: React.ReactNode[];
  rows: React.ReactNode[][];
  className?: string;
}) {
  return (
    <table className={cx("w-full text-sm", className)}>
      <thead>
        <tr className="text-left text-xs text-ink-muted">
          {head.map((cell, i) => (
            <th key={i} className={cx("pb-2 font-medium", i > 0 && "text-right")}>
              {cell}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, r) => (
          <tr key={r} className="border-t border-line">
            {row.map((cell, c) => (
              <td key={c} className={cx("py-1.5", c > 0 && "num text-right")}>
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Field({
  label,
  repeat = false,
  children,
}: {
  label: string;
  /** This field repeats a labelled row above it. Once the row grid kicks in
   * (sm+) the repeated label is noise and the column header carries the
   * meaning — but while the fields are stacked on mobile every one of them
   * needs its own label, so it is hidden by breakpoint, never dropped. */
  repeat?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
      <span className={cx(repeat && "sm:sr-only")}>{label}</span>
      {children}
    </label>
  );
}

const control =
  "h-9 rounded-md border border-line bg-bg px-2.5 text-sm text-ink " +
  "transition-colors duration-150 hover:border-ink-muted " +
  "focus:outline-2 focus:outline-offset-1 focus:outline-gold-ink " +
  "disabled:cursor-not-allowed disabled:opacity-50";

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(control, "pr-8", props.className)} />;
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(control, "num", props.className)} />;
}

export function Button({
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" }) {
  return (
    <button
      {...props}
      className={cx(
        "h-9 rounded-md px-4 text-sm font-medium transition-colors duration-150",
        "focus:outline-2 focus:outline-offset-1 focus:outline-gold-ink",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary"
          ? "bg-ink text-bg hover:bg-ink/85"
          : "border border-line text-ink hover:bg-surface",
        props.className,
      )}
    />
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-md border border-brick/30 bg-brick-soft px-4 py-3 text-sm text-brick"
    >
      {message}
    </div>
  );
}

/** Payload disclaimers rendered as content (PRODUCT.md): the caveats arrive
 * in the same JSON as the numbers, so they sit next to them — never in a
 * footer, never behind a tooltip. */
export function Caveats({
  items,
  title = "Przeczytaj to razem z liczbami",
}: {
  items: string[];
  title?: string;
}) {
  if (items.length === 0) return null;
  return (
    <section className="rounded-md border border-line bg-surface px-4 py-3">
      <h3 className="text-sm font-medium">{title}</h3>
      <ul className="mt-2 flex flex-col gap-1.5">
        {items.map((item, i) => (
          <li
            key={i}
            className="max-w-[70ch] text-xs leading-relaxed text-ink-muted"
          >
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Scanner verdict states. Shape carries the meaning so the vocabulary
 * survives greyscale and colour-blindness: a *filled* badge is actionable,
 * an *outlined* one would be actionable but is conditional, and a *dashed*
 * one means the instrument could not answer at all. */
const VERDICT_STYLES: Record<string, string> = {
  PLAY: "border-gold-ink bg-gold-soft text-gold-ink font-medium",
  UNVERIFIED: "border-gold-ink text-gold-ink",
  "NO BET": "border-line bg-surface text-ink-muted",
  STALE: "border-dashed border-ink-muted text-ink-muted",
  "NO ANCHOR": "border-dashed border-ink-muted text-ink-muted",
};

/** The API's verdict vocabulary is a stable contract (ADR 0013); only the
 * label the operator reads is translated. */
const VERDICT_LABELS: Record<string, string> = {
  PLAY: "GRAJ",
  UNVERIFIED: "NIEZWERYFIKOWANE",
  "NO BET": "NIE GRAJ",
  STALE: "NIEAKTUALNE",
  "NO ANCHOR": "BRAK KOTWICY",
};

const VERDICT_HINTS: Record<string, string> = {
  PLAY: "Przewaga przekracza próg wobec świeżej kotwicy.",
  UNVERIFIED:
    "Byłoby „graj”, ale kurs pochodzi z niezweryfikowanego feedu — sprawdź najpierw stronę bukmachera.",
  "NO BET": "Policzone i odrzucone: przewaga nie przekracza progu.",
  STALE: "Kotwica jest za stara, żeby na niej grać. Odśwież taśmę.",
  "NO ANCHOR": "Taśma nie kwotuje tej linii, więc nie ma z czym porównać.",
};

export function VerdictBadge({ verdict }: { verdict: string }) {
  return (
    <span
      title={VERDICT_HINTS[verdict]}
      className={cx(
        "inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-xs",
        VERDICT_STYLES[verdict] ?? "border-line text-ink-muted",
      )}
    >
      {VERDICT_LABELS[verdict] ?? verdict}
    </span>
  );
}

/** A labelled figure in a dense row — deliberately not the big-number stat
 * card: this surface is read like a form, not a marketing page. */
export function StatList({
  items,
}: {
  items: Array<{ label: string; value: React.ReactNode; hint?: string }>;
}) {
  return (
    <dl className="flex flex-wrap gap-x-8 gap-y-3">
      {items.map((item) => (
        <div key={item.label} className="min-w-[9rem]">
          <dt className="text-xs text-ink-muted">{item.label}</dt>
          <dd className="num mt-0.5 text-base">{item.value}</dd>
          {item.hint ? (
            <p className="mt-0.5 max-w-[28ch] text-xs leading-snug text-ink-muted">
              {item.hint}
            </p>
          ) : null}
        </div>
      ))}
    </dl>
  );
}

export function EvBadge({ value }: { value: number }) {
  const positive = value > 0;
  return (
    <span
      className={cx(
        "num inline-block rounded px-1.5 py-0.5 text-xs font-medium",
        positive ? "bg-gold-soft text-gold-ink" : "bg-surface text-ink-muted",
      )}
    >
      {positive ? "+" : ""}
      {(100 * value).toFixed(1)}% EV
    </span>
  );
}
