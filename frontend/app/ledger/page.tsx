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
        Żaden zakład nie ma jeszcze CLV. Zakład dostaje je dopiero po
        rozpoczęciu meczu, gdy taśma ma cenę zamknięcia do porównania — pusta
        kolumna znaczy „nie da się zmierzyć”, nigdy „zero”.
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
      label: "CLV ostre (wyczucie czasu)",
      mean: clv.mean_sharp,
      ci: ciRange(clv.sharp_ci),
      note: "Pinnacle w chwili zakładu vs cena zamknięcia",
    },
    {
      label: "CLV wykonane (realne złotówki)",
      mean: clv.mean_exec,
      ci: ciRange(clv.exec_ci),
      note: "kurs faktycznie wzięty, po podatku lub promocji",
    },
    {
      label: "wybór buka / promocja",
      mean: clv.mean_shopping,
      ci: ciRange(clv.shopping_ci),
      note: "wykonane minus ostre, parami dla każdego zakładu",
    },
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-ink-muted">
              <th className="pb-2 font-medium">składnik</th>
              <th className="pb-2 text-right font-medium">średnia</th>
              <th className="pb-2 text-right font-medium">przedział 95%</th>
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
        {clv.n} zakładów w {clv.n_blocks}{" "}
        {clv.n_blocks === 1 ? "tygodniu" : "tygodniach"}.{" "}
        {rows.every((row) => row.ci === null) ? (
          <strong className="font-medium text-ink">
            Poniżej czterech tygodni nie publikujemy przedziału ufności:
            losowanie z jednego tygodnia zwraca ten sam tydzień za każdym
            razem, więc „przedział 95%” miałby zerową szerokość — najpewniej
            wyglądająca liczba w całym systemie, zrobiona z najmniejszej
            ilości danych. Traktuj średnie jako szacunek, nie jako wynik.
          </strong>
        ) : (
          "Przedziały liczone metodą bootstrap po tygodniach — to najmniejsza jednostka, w której zakłady z jednej kolejki nie zaburzają wyniku."
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
            label: "Kapitał",
            value: pln(drawdown.equity_pln),
            hint: `szczyt ${pln(drawdown.peak_equity_pln)}`,
          },
          {
            label: "Obsunięcie",
            value: (
              <span className={cx(drawdown.breaker_tripped && "text-brick")}>
                {pln(drawdown.drawdown_pln)}
              </span>
            ),
            hint: `stop przy ${pln(drawdown.limit_pln)}`,
          },
          {
            label: "Bezpiecznik",
            value: drawdown.breaker_tripped ? (
              <span className="text-brick">zadziałał</span>
            ) : (
              "otwarty"
            ),
            hint: drawdown.breaker_tripped
              ? "dziennik odmawia nowych zakładów"
              : undefined,
          },
          ...Object.entries(taxFree).map(([book, state]) => ({
            label: `Bez podatku zostało (${book})`,
            value: pln(state.remaining_pln),
            hint: `wykorzystano ${pln(state.used_pln)} obrotu`,
          })),
        ]}
      />
      {overrides.length > 0 ? (
        <div className="rounded-md border border-brick/30 bg-brick-soft px-4 py-3">
          <h4 className="text-sm font-medium text-brick">
            {overrides.length}{" "}
            {overrides.length === 1 ? "zakład postawiony" : "zakłady postawione"} z pominięciem limitu
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
        <p className="text-xs text-ink-muted">Brak pominięć limitów.</p>
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
            <th className="pb-2 font-medium">początek</th>
            <th className="pb-2 font-medium">mecz</th>
            <th className="pb-2 font-medium">zakład</th>
            <th className="pb-2 font-medium">bukmacher</th>
            <th className="pb-2 text-right font-medium">stawka</th>
            <th className="pb-2 text-right font-medium">kurs</th>
            <th className="pb-2 text-right font-medium">efektywny</th>
            <th className="pb-2 text-right font-medium">ostry</th>
            <th className="pb-2 text-right font-medium">zwrot</th>
            <th className="pb-2 text-right font-medium">CLV ostre</th>
            <th className="pb-2 text-right font-medium">CLV wykon.</th>
            <th className="pb-2 text-right font-medium">wybór buka</th>
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
                    pominięto limit
                  </span>
                ) : null}
              </td>
              <td className="py-2 whitespace-nowrap">
                {pick.market}
                {pick.line ? ` ${pick.line}` : ""} {pick.selection}
              </td>
              <td className="py-2">
                {pick.bookmaker}
                {Number(pick.tax_multiplier) === 1 ? (
                  <span className="ml-1.5 text-xs text-ink-muted">bez podatku</span>
                ) : Number(pick.tax_multiplier) !== 0.88 ? (
                  <span className="ml-1.5 text-xs text-ink-muted">
                    po limicie ×{pick.tax_multiplier}
                  </span>
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
                  <span className="text-ink-muted">otwarty</span>
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
  return `„Zamknięcie” taśmy to ostatni dzienny snapshot przed gwizdkiem i bywa o kilka godzin za wczesny — najstarszy tutaj zaobserwowano ${ageLabel(
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
        <h1 className="text-2xl font-semibold tracking-tight">Dziennik</h1>
        <ErrorBanner
          message={`Nie mogę połączyć się z API (${apiError}). Uruchom aplikację skrótem pitchprob.bat i odśwież stronę.`}
        />
      </div>
    );
  }

  const { picks, summary, weekly, caveats } = data;
  const generated = new Date(weekly.generated_at);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dziennik</h1>
        <p className="mt-1 max-w-[70ch] text-sm text-ink-muted">
          Każdy zakład za prawdziwe pieniądze i to, co mówi o nim taśma.
          To sezon pomiarowy po 2–5 zł na zakład: wynikiem jest dowód na
          temat wartości wobec kursu zamknięcia, nie przychód.
        </p>
      </div>

      {picks.length === 0 ? (
        <Panel title="Nie zapisano jeszcze żadnego zakładu">
          <p className="max-w-[70ch] text-sm text-ink-muted">
            Zapisz pierwszy zakład formularzem poniżej — albo z terminala:{" "}
            <code className="num">
              pitchprob pick log --match &quot;arsenal&quot; --market ou
              --selection over --line 3.0 --book betclic --stake 5 --price
              2.10 --tax-free
            </code>
            . Każdy wpis zapamiętuje cenę Pinnacle&apos;a z chwili zakładu, więc
            CLV nie da się potem naciągnąć na swoją korzyść.
          </p>
        </Panel>
      ) : null}

      <Panel
        title="CLV — zmienna decyzyjna"
        footnote="Czytaj najpierw CLV ostre: to jedyna liczba o wyczuciu czasu. Wykonane minus ostre to wkład bukmachera i promocji."
      >
        <ClvBlock clv={weekly.clv} />
      </Panel>

      <Panel
        title={`Ostatnie ${weekly.window_days} dni`}
        footnote={`Wygenerowano ${generated.toISOString().replace("T", " ").slice(0, 16)} UTC`}
      >
        <StatList
          items={[
            { label: "Zakłady", value: weekly.window.n_picks },
            { label: "Postawiono", value: pln(weekly.window.staked_pln) },
            {
              label: "Rozliczone",
              value: weekly.window.n_settled,
              hint: `${picks.length - summary.n_settled} wciąż otwartych`,
            },
            {
              label: "Zrealizowany wynik",
              value: (
                <span className={signColor(weekly.window.profit_pln)}>
                  {signedPln(weekly.window.profit_pln)}
                </span>
              ),
              hint: "przy tej liczbie zakładów to wariancja, nie sygnał",
            },
            {
              label: "ROI sezonu",
              value: summary.roi == null ? "—" : signedPct(summary.roi),
              hint: `z ${summary.n_settled} rozliczonych zakładów`,
            },
          ]}
        />
      </Panel>

      <Panel title="Warstwa ryzyka">
        <RiskBlock weekly={weekly} />
      </Panel>

      <LogPickForm />

      {picks.length > 0 ? (
        <Panel
          title={`Zakłady (${picks.length})`}
          footnote={stalestClose(picks, weekly.generated_at)}
        >
          <PicksTable picks={picks} />
        </Panel>
      ) : null}

      <Caveats items={caveats} />
    </div>
  );
}
