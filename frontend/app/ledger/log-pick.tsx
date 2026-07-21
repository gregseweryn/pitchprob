"use client";

/** Log an executed bet from the browser (ADR 0013/0015).
 *
 * The form is a second front door to the same ledger, never a looser one:
 * the exposure limits and the drawdown breaker run server-side, so a bet
 * the CLI would refuse is refused here too. What the browser adds is that
 * the refusal can be *shown* properly — which limit, and what overriding it
 * would cost — instead of a stack trace in a terminal. */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import type { TapeEvent } from "@/lib/api";
import { PickRefused, getTapeEvents, postPick } from "@/lib/api";
import { Button, Field, Input, Panel, Select } from "@/components/ui";
import { cx } from "@/lib/format";

const MARKETS = [
  { id: "1x2", label: "1X2 (gospodarz/remis/gość)", selections: ["home", "draw", "away"], line: false },
  { id: "ou", label: "Powyżej/poniżej goli", selections: ["over", "under"], line: true },
  { id: "ah", label: "Handicap azjatycki", selections: ["home", "away"], line: true },
] as const;

/** Selection values are the API's contract (ADR 0013); only the label the
 * operator reads is Polish. */
const SELECTION_LABELS: Record<string, string> = {
  home: "gospodarz",
  draw: "remis",
  away: "gość",
  over: "powyżej",
  under: "poniżej",
};

export function LogPickForm() {
  const router = useRouter();
  const [events, setEvents] = useState<TapeEvent[] | null>(null);
  const [eventId, setEventId] = useState("");
  const [market, setMarket] = useState<string>("1x2");
  const [selection, setSelection] = useState("home");
  const [line, setLine] = useState("");
  const [bookmaker, setBookmaker] = useState("");
  const [stake, setStake] = useState("5");
  const [price, setPrice] = useState("");
  const [taxFree, setTaxFree] = useState(false);
  const [override, setOverride] = useState(false);
  const [refusal, setRefusal] = useState<PickRefused | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getTapeEvents()
      .then((found) => {
        setEvents(found);
        if (found.length > 0) setEventId(found[0].event_id);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const spec = MARKETS.find((entry) => entry.id === market) ?? MARKETS[0];
  const fixture = (events ?? []).find((event) => event.event_id === eventId);

  function chooseMarket(next: string) {
    const target = MARKETS.find((entry) => entry.id === next) ?? MARKETS[0];
    setMarket(next);
    setSelection(target.selections[0]);
    if (!target.line) setLine("");
  }

  async function submit() {
    if (!fixture) {
      setError("Najpierw wybierz mecz z taśmy.");
      return;
    }
    setBusy(true);
    setRefusal(null);
    setError(null);
    setSaved(null);
    try {
      const pick = await postPick({
        home_team: fixture.home_team,
        away_team: fixture.away_team,
        kickoff_utc: fixture.commence_time,
        market,
        selection,
        line: spec.line ? line || null : null,
        bookmaker: bookmaker.trim(),
        stake_pln: stake.trim(),
        price_quoted: price.trim(),
        tax_free: taxFree,
        event_id: fixture.event_id,
        override_risk: override,
      });
      setSaved(
        `Zapisano zakład #${pick.id}${pick.risk_override ? " (POMINIĘTO LIMIT — zapisane na stałe)" : ""}`,
      );
      setPrice("");
      setOverride(false);
      // The tables above this form are server-rendered; refresh so the new
      // pick and the recomputed weekly report appear immediately.
      router.refresh();
    } catch (err) {
      if (err instanceof PickRefused) setRefusal(err);
      else setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Zapisz zakład, który postawiłeś"
      footnote="Zapisz go po postawieniu pieniędzy, z kursem, który naprawdę dostałeś — dziennik mierzy realne złotówki, nie kurs, na który liczyłeś."
    >
      <div className="flex flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Mecz">
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
          <Field label="Rynek">
            <Select value={market} onChange={(e) => chooseMarket(e.target.value)}>
              {MARKETS.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Typ">
            <Select
              value={selection}
              onChange={(event) => setSelection(event.target.value)}
            >
              {spec.selections.map((name) => (
                <option key={name} value={name}>
                  {SELECTION_LABELS[name] ?? name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={spec.line ? "Linia" : "Linia (nie dotyczy)"}>
            <Input
              value={line}
              onChange={(event) => setLine(event.target.value)}
              disabled={!spec.line}
              placeholder={spec.line ? "3.0" : "—"}
              inputMode="decimal"
            />
          </Field>
        </div>

        <div className="grid items-end gap-3 sm:grid-cols-[minmax(0,14rem)_6.5rem_6.5rem_auto]">
          <Field label="Bukmacher">
            <Input
              value={bookmaker}
              onChange={(event) => setBookmaker(event.target.value)}
              placeholder="betclic"
              className="!font-sans"
            />
          </Field>
          <Field label="Stawka (zł)">
            <Input
              value={stake}
              onChange={(event) => setStake(event.target.value)}
              inputMode="decimal"
            />
          </Field>
          <Field label="Kurs">
            <Input
              value={price}
              onChange={(event) => setPrice(event.target.value)}
              placeholder="2.10"
              inputMode="decimal"
            />
          </Field>
          <label className="flex h-9 items-center gap-2 text-sm sm:mt-[1.375rem]">
            <input
              type="checkbox"
              checked={taxFree}
              onChange={(event) => setTaxFree(event.target.checked)}
              className="size-4 accent-[var(--gold-ink)]"
            />
            bez podatku
          </label>
        </div>

        {refusal ? (
          <div
            role="alert"
            className="rounded-md border border-brick/30 bg-brick-soft px-4 py-3"
          >
            <p className="text-sm font-medium text-brick">
              {refusal.kind === "limit"
                ? "Warstwa ryzyka odrzuciła ten zakład"
                : "Ten zakład jest nieprawidłowy"}
            </p>
            <p className="mt-1 max-w-[70ch] text-xs leading-relaxed text-brick">
              {refusal.message}
            </p>
            {refusal.kind === "limit" ? (
              <label className="mt-3 flex items-center gap-2 text-xs text-brick">
                <input
                  type="checkbox"
                  checked={override}
                  onChange={(event) => setOverride(event.target.checked)}
                  className="size-4 accent-[var(--brick)]"
                />
                Postaw mimo to — zakład zostanie trwale oznaczony i pojawi
                się w raporcie tygodniowym.
              </label>
            ) : null}
          </div>
        ) : null}

        {error ? (
          <div
            role="alert"
            className="rounded-md border border-brick/30 bg-brick-soft px-4 py-3 text-sm text-brick"
          >
            {error}
          </div>
        ) : null}

        {saved ? (
          <p
            role="status"
            className={cx(
              "rounded-md border px-4 py-3 text-sm",
              saved.includes("POMINIĘTO")
                ? "border-brick/30 bg-brick-soft text-brick"
                : "border-line bg-surface text-ink",
            )}
          >
            {saved}
          </p>
        ) : null}

        <div className="flex justify-end">
          <Button onClick={submit} disabled={busy}>
            {busy ? "Zapisuję…" : override ? "Zapisz mimo limitu" : "Zapisz ten zakład"}
          </Button>
        </div>
      </div>
    </Panel>
  );
}
