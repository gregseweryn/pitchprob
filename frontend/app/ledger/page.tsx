/** The forward pick ledger (ADR 0013) and the week's report (ADR 0015).
 *
 * The page is ordered by what decides things: CLV first, money second. ROI
 * over a few dozen 2-5 PLN bets is variance with a number attached; closing
 * -line value is the part that carries signal, and sharp CLV — timing alone
 * — leads exec CLV because their difference is the venue/promo component. */

import { getLedger } from "@/lib/api";
import { Caveats, ErrorBanner, Panel, StatList } from "@/components/ui";
import { LogPickForm } from "@/app/ledger/log-pick";
import {
  ageLabel,
  ciRange,
  cx,
  pctOrDash,
  pln,
  price as fmtPrice,
  signedPct,
  signedPln,
} from "@/lib/format";
import type { LedgerResponse, WeeklyMetrics } from "@/lib/api";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Ledger — pitchprob",
  description:
    "Real-money picks with the CLV decomposition, the weekly tape report, and the risk layer's state.",
};

/** Sign colouring, applied identically everywhere a CLV figure appears —
 * a value that is gold in one column and plain ink in the next reads as an
 * oversight rather than a distinction. */
function signColor(value: number | null | undefined): string {
  if (value == null || value === 0) return "";
  return value > 0 ? "text-gold-ink" : "text-brick";
}

function ClvBlock({ clv }: { clv: WeeklyMetrics["clv"] }) {
  if (clv.n === 0) {
    return (
      <p className="max-w-[70ch] text-sm text-ink-muted">
        No pick carries CLV yet. A bet earns one only after kickoff, when the
        tape has a closing fair to compare against — so an empty column here
        means &ldquo;not measurable&rdquo;, never &ldquo;zero&rdquo;.
      </p>
    );
  }
  const rows: Array<{
    label: string;
    mean: number | null;
    ci: ReturnType<typeof ciRange>;
    note: string;
  }> = [
    {
      label: "sharp CLV (timing)",
      mean: clv.mean_sharp,
      ci: ciRange(clv.sharp_ci),
      note: "Pinnacle at bet time vs the closing fair",
    },
    {
      label: "exec CLV (PLN-real)",
      mean: clv.mean_exec,
      ci: ciRange(clv.exec_ci),
      note: "the price actually executed, after tax or promo",
    },
    {
      label: "shopping / promo",
      mean: clv.mean_shopping,
      ci: ciRange(clv.shopping_ci),
      note: "exec minus sharp, paired per bet",
    },
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-ink-muted">
              <th className="pb-2 font-medium">component</th>
              <th className="pb-2 text-right font-medium">mean</th>
              <th className="pb-2 text-right font-medium">95% CI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.label} className="border-b border-line last:border-0">
                <td className="py-2">
                  {row.label}
                  <span className="block text-xs text-ink-muted">{row.note}</span>
                </td>
                <td className={cx("num py-2 text-right", signColor(row.mean))}>
                  {pctOrDash(row.mean)}
                </td>
                <td className="num py-2 text-right text-ink-muted">
                  {row.ci ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="max-w-[70ch] text-xs leading-relaxed text-ink-muted">
        {clv.n} bets across {clv.n_blocks}{" "}
        {clv.n_blocks === 1 ? "ISO week" : "ISO weeks"}.{" "}
        {rows.every((row) => row.ci === null) ? (
          <strong className="font-medium text-ink">
            No interval is published below four weeks of bets: a block
            bootstrap that resamples a single week returns that week every
            time, so the &ldquo;95% CI&rdquo; would have zero width — the most
            confident-looking output in the system, from the least evidence.
            Read the means as point estimates, not as a finding.
          </strong>
        ) : (
          "Intervals are block-bootstrapped over ISO weeks, the exchangeable unit for bets that share a match round."
        )}
      </p>
    </div>
  );
}

function RiskBlock({ weekly }: { weekly: WeeklyMetrics }) {
  const { drawdown, tax_free: taxFree, overrides } = weekly;
  return (
    <div className="flex flex-col gap-4">
      <StatList
        items={[
          {
            label: "Equity",
            value: pln(drawdown.equity_pln),
            hint: `peak ${pln(drawdown.peak_equity_pln)}`,
          },
          {
            label: "Drawdown",
            value: (
              <span className={cx(drawdown.breaker_tripped && "text-brick")}>
                {pln(drawdown.drawdown_pln)}
              </span>
            ),
            hint: `stop at ${pln(drawdown.limit_pln)}`,
          },
          {
            label: "Circuit breaker",
            value: drawdown.breaker_tripped ? (
              <span className="text-brick">tripped</span>
            ) : (
              "open"
            ),
            hint: drawdown.breaker_tripped
              ? "pick log refuses new bets"
              : undefined,
          },
          ...Object.entries(taxFree).map(([book, state]) => ({
            label: `Tax-free left (${book})`,
            value: pln(state.remaining_pln),
            hint: `${pln(state.used_pln)} of turnover used`,
          })),
        ]}
      />
      {overrides.length > 0 ? (
        <div className="rounded-md border border-brick/30 bg-brick-soft px-4 py-3">
          <h4 className="text-sm font-medium text-brick">
            {overrides.length} pick
            {overrides.length === 1 ? "" : "s"} placed as a risk override
          </h4>
          <ul className="mt-2 flex flex-col gap-1.5">
            {overrides.map((entry) => (
              <li key={entry.pick_id} className="text-xs leading-relaxed text-brick">
                <span className="num">#{entry.pick_id}</span> {entry.fixture} (
                <span className="num">{pln(entry.stake_pln)}</span>) —{" "}
                {entry.risk_note}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-xs text-ink-muted">No limit overrides on record.</p>
      )}
    </div>
  );
}

function PicksTable({ picks }: { picks: LedgerResponse["picks"] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[62rem] text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-ink-muted">
            <th className="pb-2 font-medium">kickoff</th>
            <th className="pb-2 font-medium">fixture</th>
            <th className="pb-2 font-medium">bet</th>
            <th className="pb-2 font-medium">book</th>
            <th className="pb-2 text-right font-medium">stake</th>
            <th className="pb-2 text-right font-medium">quoted</th>
            <th className="pb-2 text-right font-medium">effective</th>
            <th className="pb-2 text-right font-medium">sharp</th>
            <th className="pb-2 text-right font-medium">return</th>
            <th className="pb-2 text-right font-medium">sharp CLV</th>
            <th className="pb-2 text-right font-medium">exec CLV</th>
            <th className="pb-2 text-right font-medium">shopping</th>
          </tr>
        </thead>
        <tbody>
          {picks.map((pick) => (
            <tr key={pick.id} className="border-b border-line last:border-0">
              <td className="num py-2 whitespace-nowrap">
                {pick.kickoff_utc.slice(0, 10)}
              </td>
              <td className="py-2">
                {pick.home_team} v {pick.away_team}
                {pick.risk_override ? (
                  <span
                    title={pick.risk_note ?? undefined}
                    className="ml-1.5 rounded border border-brick/40 px-1 text-xs text-brick"
                  >
                    override
                  </span>
                ) : null}
              </td>
              <td className="py-2 whitespace-nowrap">
                {pick.market}
                {pick.line ? ` ${pick.line}` : ""} {pick.selection}
              </td>
              <td className="py-2">
                {pick.bookmaker}
                {pick.tax_free ? (
                  <span className="ml-1.5 text-xs text-ink-muted">tax-free</span>
                ) : null}
              </td>
              <td className="num py-2 text-right">
                {Number(pick.stake_pln).toFixed(2)}
              </td>
              <td className="num py-2 text-right">
                {fmtPrice(Number(pick.price_quoted))}
              </td>
              <td className="num py-2 text-right">
                {fmtPrice(Number(pick.price_effective))}
              </td>
              <td className="num py-2 text-right text-ink-muted">
                {pick.price_sharp ? fmtPrice(Number(pick.price_sharp)) : "—"}
              </td>
              <td className="num py-2 text-right">
                {pick.gross_return_pln == null ? (
                  <span className="text-ink-muted">open</span>
                ) : (
                  Number(pick.gross_return_pln).toFixed(2)
                )}
              </td>
              <td className={cx("num py-2 text-right", signColor(pick.clv_sharp))}>
                {pctOrDash(pick.clv_sharp)}
              </td>
              <td className={cx("num py-2 text-right", signColor(pick.clv_exec))}>
                {pctOrDash(pick.clv_exec)}
              </td>
              <td className="num py-2 text-right text-ink-muted">
                {pctOrDash(pick.clv_shopping)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** How stale the tape's "close" was for the oldest pick that has one.
 *
 * Measured against the report's own `generated_at` rather than the render
 * clock: the server already stamped the instant it computed everything, so
 * using it keeps the render pure and the number reproducible. */
function stalestClose(
  picks: LedgerResponse["picks"],
  generatedAt: string,
): string | undefined {
  const ages = picks
    .map((pick) => pick.closing_observed_at)
    .filter((observed): observed is string => observed != null)
    .map(
      (observed) =>
        (new Date(generatedAt).getTime() - new Date(observed).getTime()) /
        3_600_000,
    );
  if (ages.length === 0) return undefined;
  return `The tape's "close" is the last daily snapshot before kickoff and can sit hours early — the oldest here was observed ${ageLabel(
    Math.max(...ages),
  )}.`;
}

export default async function LedgerPage() {
  let data: LedgerResponse | null = null;
  let apiError: string | null = null;
  try {
    data = await getLedger();
  } catch (error) {
    apiError = error instanceof Error ? error.message : String(error);
  }

  if (apiError || !data) {
    return (
      <div className="flex max-w-xl flex-col gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Ledger</h1>
        <ErrorBanner
          message={`The pitchprob API is not reachable (${apiError}). Start it with \`make serve\` and reload.`}
        />
      </div>
    );
  }

  const { picks, summary, weekly, caveats } = data;
  const generated = new Date(weekly.generated_at);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Ledger</h1>
        <p className="mt-1 max-w-[70ch] text-sm text-ink-muted">
          Every real-money bet, and what the tape says about it. This is a
          measurement season at 2–5 PLN a bet: the output is evidence about
          closing-line value, not income.
        </p>
      </div>

      {picks.length === 0 ? (
        <Panel title="No bets logged yet">
          <p className="max-w-[70ch] text-sm text-ink-muted">
            The ledger fills up from the command line, one executed bet at a
            time:{" "}
            <code className="num">
              pitchprob pick log --match &quot;arsenal&quot; --market ou
              --selection over --line 3.0 --book betclic --stake 5 --price
              2.10 --tax-free
            </code>
            . Each pick captures the Pinnacle anchor at bet time, so its
            timing CLV cannot be reconstructed favourably afterwards.
          </p>
        </Panel>
      ) : null}

      <Panel
        title="CLV — the decision variable"
        footnote="Read sharp CLV first: it is the only timing number. Exec minus sharp is what the venue and the promotion contributed."
      >
        <ClvBlock clv={weekly.clv} />
      </Panel>

      <Panel
        title={`Last ${weekly.window_days} days`}
        footnote={`Generated ${generated.toISOString().replace("T", " ").slice(0, 16)} UTC`}
      >
        <StatList
          items={[
            { label: "Picks", value: weekly.window.n_picks },
            { label: "Staked", value: pln(weekly.window.staked_pln) },
            {
              label: "Settled",
              value: weekly.window.n_settled,
              hint: `${picks.length - summary.n_settled} still open`,
            },
            {
              label: "Realized",
              value: (
                <span className={signColor(weekly.window.profit_pln)}>
                  {signedPln(weekly.window.profit_pln)}
                </span>
              ),
              hint: "variance, not signal, at this sample size",
            },
            {
              label: "Season ROI",
              value: summary.roi == null ? "—" : signedPct(summary.roi),
              hint: `over ${summary.n_settled} settled bets`,
            },
          ]}
        />
      </Panel>

      <Panel title="Risk layer">
        <RiskBlock weekly={weekly} />
      </Panel>

      <LogPickForm />

      {picks.length > 0 ? (
        <Panel
          title={`Picks (${picks.length})`}
          footnote={stalestClose(picks, weekly.generated_at)}
        >
          <PicksTable picks={picks} />
        </Panel>
      ) : null}

      <Caveats items={caveats} />
    </div>
  );
}
