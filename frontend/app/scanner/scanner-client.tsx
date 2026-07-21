"use client";

/** The PL value scanner (ADR 0013): type the prices Polish books are
 * showing, get a verdict against the tape's Shin-de-margined Pinnacle fair.
 *
 * The screen is arranged around what makes the verdict trustworthy rather
 * than around the verdict itself: the anchor and its age sit above the
 * table, because a +5% edge against a 40-hour-old sharp line is not a bet,
 * and the caveats arrive in the payload and are rendered as content. */

import { useEffect, useState } from "react";

import type { QuoteInput, ScanResponse, TapeEvent } from "@/lib/api";
import { getTapeEvents, postScan } from "@/lib/api";
import { ageLabel, cx, pct, pctOrDash, price as fmtPrice } from "@/lib/format";
import {
  Button,
  Caveats,
  ErrorBanner,
  Field,
  Input,
  Panel,
  Select,
  StatList,
  VerdictBadge,
} from "@/components/ui";

const MARKETS = [
  { id: "1x2", label: "1X2", selections: ["home", "draw", "away"], line: false },
  { id: "ou", label: "Over/Under", selections: ["over", "under"], line: true },
  { id: "ah", label: "Asian handicap", selections: ["home", "away"], line: true },
  {
    id: "corners_ou",
    label: "Corners O/U",
    selections: ["over", "under"],
    line: true,
  },
  {
    id: "corners_ah",
    label: "Corners handicap",
    selections: ["home", "away"],
    line: true,
  },
] as const;

type QuoteRow = {
  bookmaker: string;
  price: string;
  taxFree: boolean;
  boostedPrice: string;
};

const EMPTY_ROW: QuoteRow = {
  bookmaker: "",
  price: "",
  taxFree: false,
  boostedPrice: "",
};

/** The tape's anchor, and how much of it to believe. */
function AnchorHeader({ result }: { result: ScanResponse }) {
  const { anchor } = result;
  if (!anchor) {
    return (
      <p className="max-w-[70ch] text-sm text-ink-muted">
        The tape has no complete Pinnacle market for{" "}
        <span className="num">
          {result.market}
          {result.line ? ` ${result.line}` : ""}
        </span>{" "}
        on this fixture, so every quote below reads{" "}
        <span className="num">NO ANCHOR</span>. That is a refusal, not a
        failure: comparing against a neighbouring line would be a wrong
        answer rather than a missing one.
      </p>
    );
  }
  const stale = anchor.age_hours > 30;
  return (
    <StatList
      items={[
        {
          label: "Sharp anchor",
          value: `${anchor.bookmaker} ${fmtPrice(Number(anchor.price))}`,
        },
        {
          label: "De-margined fair",
          value: pct(anchor.fair_probability),
          hint: "Shin, from the complete market",
        },
        {
          label: "Observed",
          value: (
            <span className={cx(stale && "text-brick")}>
              {ageLabel(anchor.age_hours)}
            </span>
          ),
          hint: stale
            ? "Older than 30h — verdicts downgrade to STALE"
            : new Date(anchor.observed_at).toISOString().slice(0, 16) + "Z",
        },
        ...(result.model_probability != null
          ? [
              {
                label: "Model fair",
                value: pct(result.model_probability),
                hint: "Informational only — it never flips a verdict",
              },
            ]
          : []),
      ]}
    />
  );
}

function VerdictTable({ result }: { result: ScanResponse }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[46rem] text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-ink-muted">
            <th className="pb-2 font-medium">verdict</th>
            <th className="pb-2 font-medium">book</th>
            <th className="pb-2 text-right font-medium">quoted</th>
            <th className="pb-2 text-right font-medium">effective</th>
            <th className="pb-2 text-right font-medium">edge</th>
            <th className="pb-2 text-right font-medium">promo</th>
            <th className="pb-2 text-right font-medium">vs model</th>
          </tr>
        </thead>
        <tbody>
          {result.verdicts.map((verdict) => (
            <tr key={verdict.bookmaker} className="border-b border-line last:border-0">
              <td className="py-2">
                <VerdictBadge verdict={verdict.verdict} />
              </td>
              <td className="py-2">
                {verdict.bookmaker}
                {verdict.tax_free ? (
                  <span className="ml-1.5 text-xs text-ink-muted">tax-free</span>
                ) : null}
                {verdict.boosted ? (
                  <span className="ml-1.5 text-xs text-ink-muted">boosted</span>
                ) : null}
                {verdict.source === "feed" ? (
                  <span className="ml-1.5 text-xs text-ink-muted">feed</span>
                ) : null}
              </td>
              <td className="num py-2 text-right">
                {fmtPrice(Number(verdict.price_quoted))}
              </td>
              <td className="num py-2 text-right">
                {fmtPrice(Number(verdict.price_effective))}
              </td>
              <td
                className={cx(
                  "num py-2 text-right",
                  verdict.edge != null && verdict.edge > 0 && "text-gold-ink",
                  verdict.edge != null && verdict.edge < 0 && "text-brick",
                )}
              >
                {pctOrDash(verdict.edge)}
              </td>
              <td className="num py-2 text-right text-ink-muted">
                {verdict.promo_value == null
                  ? "—"
                  : `${(100 * verdict.promo_value).toFixed(1)}pp`}
              </td>
              <td className="num py-2 text-right text-ink-muted">
                {pctOrDash(verdict.edge_model)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ScannerClient() {
  const [events, setEvents] = useState<TapeEvent[] | null>(null);
  const [eventId, setEventId] = useState("");
  const [market, setMarket] = useState<string>("1x2");
  const [selection, setSelection] = useState("home");
  const [line, setLine] = useState("");
  const [modelProb, setModelProb] = useState("");
  const [rows, setRows] = useState<QuoteRow[]>([{ ...EMPTY_ROW }]);
  const [result, setResult] = useState<ScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTapeEvents()
      .then((found) => {
        setEvents(found);
        if (found.length > 0) setEventId(found[0].event_id);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const spec = MARKETS.find((entry) => entry.id === market) ?? MARKETS[0];

  function chooseMarket(next: string) {
    const target = MARKETS.find((entry) => entry.id === next) ?? MARKETS[0];
    setMarket(next);
    setSelection(target.selections[0]);
    if (!target.line) setLine("");
  }

  function updateRow(index: number, patch: Partial<QuoteRow>) {
    setRows((current) =>
      current.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  }

  async function submit() {
    setLoading(true);
    setError(null);
    try {
      const quotes: QuoteInput[] = rows
        .filter((row) => row.bookmaker.trim() && row.price.trim())
        .map((row) => ({
          bookmaker: row.bookmaker.trim(),
          price: row.price.trim(),
          tax_free: row.taxFree,
          boosted_price: row.boostedPrice.trim() || null,
        }));
      if (quotes.length === 0) {
        throw new Error("Add at least one book and price.");
      }
      setResult(
        await postScan({
          market,
          selection,
          quotes,
          line: spec.line ? line || null : null,
          event_id: eventId || null,
          model_probability: modelProb ? Number(modelProb) : null,
        }),
      );
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Scanner</h1>
        <p className="mt-1 max-w-[70ch] text-sm text-ink-muted">
          Type the prices you can see at Polish books; each is verdicted
          against the live Pinnacle fair from the odds tape, on effective
          prices after the 12% turnover tax. Expect NO BET — that is the
          honest answer most of the time.
        </p>
      </div>

      {events !== null && events.length === 0 ? (
        <Panel title="No fixtures on the tape">
          <p className="max-w-[70ch] text-sm text-ink-muted">
            The scanner anchors on the odds tape, and the tape has no upcoming
            fixtures right now. Record a snapshot with{" "}
            <code className="num">pitchprob record-odds --league all</code>,
            merge it with <code className="num">pitchprob import-tape</code>,
            then reload. Without an anchor the scanner will not guess.
          </p>
        </Panel>
      ) : null}

      <Panel title="Selection">
        <div className="flex flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Fixture">
              <Select
                value={eventId}
                onChange={(event) => setEventId(event.target.value)}
                disabled={!events || events.length === 0}
              >
                {(events ?? []).map((event) => (
                  <option key={event.event_id} value={event.event_id}>
                    {event.home_team} v {event.away_team} —{" "}
                    {event.commence_time.slice(0, 10)}
                  </option>
                ))}
                {events === null ? <option>loading…</option> : null}
              </Select>
            </Field>
            <Field label="Market">
              <Select
                value={market}
                onChange={(event) => chooseMarket(event.target.value)}
              >
                {MARKETS.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Selection">
              <Select
                value={selection}
                onChange={(event) => setSelection(event.target.value)}
              >
                {spec.selections.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={spec.line ? "Line (must match the tape)" : "Line (n/a)"}>
              <Input
                value={line}
                onChange={(event) => setLine(event.target.value)}
                disabled={!spec.line}
                placeholder={spec.line ? "3.0" : "—"}
                inputMode="decimal"
              />
            </Field>
          </div>

          <div className="border-t border-line pt-4">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
              <h4 className="text-sm font-medium">Quotes you can see</h4>
              <span className="text-xs text-ink-muted">
                one row per book; leave blank to skip
              </span>
            </div>
            <div className="mt-3 flex flex-col gap-2">
              {rows.map((row, index) => (
                <div
                  key={index}
                  className="grid items-end gap-3 sm:grid-cols-[minmax(0,16rem)_6.5rem_6.5rem_auto] sm:justify-start"
                >
                  {/* Labels only on the first row: repeating them down a
                      list is noise. Every input keeps its own aria-label so
                      the pairing survives for screen readers. */}
                  <Field label="Book" repeat={index > 0}>
                    <Input
                      value={row.bookmaker}
                      onChange={(event) =>
                        updateRow(index, { bookmaker: event.target.value })
                      }
                      placeholder="betclic"
                      aria-label={`Bookmaker, row ${index + 1}`}
                      className="!font-sans"
                    />
                  </Field>
                  <Field label="Price" repeat={index > 0}>
                    <Input
                      value={row.price}
                      onChange={(event) =>
                        updateRow(index, { price: event.target.value })
                      }
                      placeholder="2.10"
                      inputMode="decimal"
                      aria-label={`Quoted price, row ${index + 1}`}
                    />
                  </Field>
                  <Field label="Boosted to" repeat={index > 0}>
                    <Input
                      value={row.boostedPrice}
                      onChange={(event) =>
                        updateRow(index, { boostedPrice: event.target.value })
                      }
                      placeholder="—"
                      inputMode="decimal"
                      aria-label={`Boosted price, row ${index + 1}`}
                    />
                  </Field>
                  <label className="flex h-9 items-center gap-2 text-sm sm:mt-[1.375rem]">
                    <input
                      type="checkbox"
                      checked={row.taxFree}
                      onChange={(event) =>
                        updateRow(index, { taxFree: event.target.checked })
                      }
                      aria-label={`Tax-free promo, row ${index + 1}`}
                      className="size-4 accent-[var(--gold-ink)]"
                    />
                    tax-free
                  </label>
                </div>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button
                variant="ghost"
                type="button"
                onClick={() => setRows((current) => [...current, { ...EMPTY_ROW }])}
              >
                Add a book
              </Button>
              {rows.length > 1 ? (
                <Button
                  variant="ghost"
                  type="button"
                  onClick={() => setRows((current) => current.slice(0, -1))}
                >
                  Remove last
                </Button>
              ) : null}
              <div className="ml-auto flex items-end gap-3">
                <Field label="Model fair (optional)">
                  <Input
                    value={modelProb}
                    onChange={(event) => setModelProb(event.target.value)}
                    placeholder="0.52"
                    inputMode="decimal"
                    className="w-28"
                  />
                </Field>
                <Button onClick={submit} disabled={loading}>
                  {loading ? "Verdicting…" : "Verdict these quotes"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      </Panel>

      {error ? <ErrorBanner message={error} /> : null}

      {loading ? (
        <Panel title="Verdicts">
          <div className="flex flex-col gap-2" aria-busy="true">
            {[0, 1].map((row) => (
              <div
                key={row}
                className="h-8 animate-pulse rounded bg-surface motion-reduce:animate-none"
              />
            ))}
          </div>
        </Panel>
      ) : null}

      {result && !loading ? (
        <>
          <Panel
            title={`${result.home_team} v ${result.away_team} — ${result.market}${
              result.line ? ` ${result.line}` : ""
            } ${result.selection}`}
            footnote={`Kickoff ${new Date(result.commence_time)
              .toISOString()
              .replace("T", " ")
              .slice(0, 16)} UTC · tape event ${result.event_id}`}
          >
            <div className="flex flex-col gap-5">
              <AnchorHeader result={result} />
              <VerdictTable result={result} />
            </div>
          </Panel>
          <Caveats items={result.caveats} />
        </>
      ) : null}

      {!result && !loading && !error ? (
        <Panel title="Nothing verdicted yet">
          <p className="max-w-[70ch] text-sm text-ink-muted">
            Pick a fixture and market, type at least one price, and the
            scanner will price it against the tape. It compares{" "}
            <em>effective</em> prices: a taxed quote of 2.10 pays 1.85, so
            most quotes lose to the sharp fair before any edge is discussed.
            Value tends to show up in promotions and in prices that have not
            yet followed a sharp move.
          </p>
        </Panel>
      ) : null}
    </div>
  );
}
