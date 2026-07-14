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
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
      {label}
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
