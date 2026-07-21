import { getBacktests } from "@/lib/api";
import { Panel } from "@/components/ui";
import { ciRange, type Interval } from "@/lib/format";

export const dynamic = "force-dynamic";

type Metrics = {
  n_predictions?: number;
  model?: { log_loss?: number; rps?: number; ece_home?: number };
  benchmark_subset?: {
    model_rps?: number;
    closing_rps?: number;
    model_log_loss?: number;
    closing_log_loss?: number;
  };
  staking_flat?: {
    selector?: string;
    n_bets?: number;
    roi?: number;
    mean_clv?: number | null;
    mean_clv_sharp?: number | null;
    roi_ci?: Interval;
    clv_ci?: Interval;
    clv_sharp_ci?: Interval;
  };
};

function num(value: number | undefined | null, digits = 4): string {
  return value === undefined || value === null ? "—" : value.toFixed(digits);
}

function pctOrDash(value: number | undefined | null): string {
  return value === undefined || value === null ? "—" : `${(100 * value).toFixed(1)}%`;
}

function CiLine({ interval }: { interval: Interval | undefined }) {
  const text = ciRange(interval);
  return text ? <span className="block text-xs text-ink-muted">{text}</span> : null;
}

export default async function BacktestsPage() {
  let rows: Awaited<ReturnType<typeof getBacktests>> = [];
  let apiError: string | null = null;
  try {
    rows = await getBacktests();
  } catch (error) {
    apiError = error instanceof Error ? error.message : String(error);
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Backtesty</h1>
        <p className="mt-1 max-w-[65ch] text-sm text-ink-muted">
          Każdy zapisany przebieg walk-forward — model przeciwko cenie zamknięcia bez
          marży i to, co dałoby płaskie obstawianie. Mniejsze liczby pod spodem to
          przedziały ufności 95%. CLV ostre mierzy samo wyczucie czasu; CLV wykonane
          bez niego to tylko wybór lepszego bukmachera. Wiersze z ujemnym ROI są tymi
          uczciwymi.
        </p>
      </div>

      {apiError ? (
        <p className="rounded-md border border-brick/30 bg-brick-soft px-4 py-3 text-sm text-brick">
          Nie mogę połączyć się z API ({apiError}).
        </p>
      ) : rows.length === 0 ? (
        <Panel>
          <p className="text-sm text-ink-muted">
            Nie zapisano jeszcze żadnego backtestu. Uruchom go z terminala, np.{" "}
            <code className="num">
              uv run pitchprob backtest --league E0 --start 2021-08-01
            </code>{" "}
            — wyniki pojawią się tutaj.
          </p>
        </Panel>
      ) : (
        <div className="flex flex-col gap-3">
        {rows.some((row) => !(row.metrics as Metrics).staking_flat?.clv_sharp_ci) ? (
          <p className="max-w-[70ch] text-xs leading-relaxed text-ink-muted">
            Puste komórki „CLV ostre” i przedziałów to przebiegi zapisane, zanim
            dołożono rozbicie CLV na dwie etykiety — tych liczb po prostu nigdy dla
            nich nie policzono, więc kolumny są puste, a nie zerowe. Powtórz te
            przebiegi, żeby je uzupełnić.
          </p>
        ) : null}
        <div className="overflow-x-auto rounded-md border border-line">
          <table className="w-full min-w-[1000px] text-sm">
            <thead>
              <tr className="bg-surface text-left text-xs text-ink-muted">
                {[
                  "przebieg",
                  "liga",
                  "model",
                  "selektor",
                  "prognoz",
                  "log-loss",
                  "RPS",
                  "RPS zamknięcia",
                  "zakładów",
                  "ROI",
                  "CLV wykon.",
                  "CLV ostre",
                ].map((label, i) => (
                  <th
                    key={label}
                    className={`px-3 py-2 font-medium ${i > 3 ? "text-right" : ""}`}
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const config = row.config as Record<string, string | number>;
                const metrics = row.metrics as Metrics;
                return (
                  <tr key={row.id} className="border-t border-line">
                    <td className="num px-3 py-2">
                      #{row.id}
                      <span className="ml-2 text-xs text-ink-muted">
                        {new Date(row.created_at).toISOString().slice(0, 10)}
                      </span>
                    </td>
                    <td className="px-3 py-2">{String(config.league ?? "—")}</td>
                    <td className="px-3 py-2">{String(config.model ?? "—")}</td>
                    <td className="px-3 py-2">
                      {String(metrics.staking_flat?.selector ?? "naive")}
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {metrics.n_predictions ?? "—"}
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {num(metrics.benchmark_subset?.model_log_loss ?? metrics.model?.log_loss)}
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {num(metrics.benchmark_subset?.model_rps ?? metrics.model?.rps)}
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {num(metrics.benchmark_subset?.closing_rps)}
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {metrics.staking_flat?.n_bets ?? "—"}
                    </td>
                    <td
                      className={`num px-3 py-2 text-right ${
                        (metrics.staking_flat?.roi ?? 0) < 0 ? "text-brick" : "text-gold-ink"
                      }`}
                    >
                      {pctOrDash(metrics.staking_flat?.roi)}
                      <CiLine interval={metrics.staking_flat?.roi_ci} />
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {pctOrDash(metrics.staking_flat?.mean_clv)}
                      <CiLine interval={metrics.staking_flat?.clv_ci} />
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {pctOrDash(metrics.staking_flat?.mean_clv_sharp)}
                      <CiLine interval={metrics.staking_flat?.clv_sharp_ci} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        </div>
      )}
    </div>
  );
}
