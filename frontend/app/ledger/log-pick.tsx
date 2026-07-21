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
  { id: "1x2", label: "1X2", selections: ["home", "draw", "away"], line: false },
  { id: "ou", label: "Over/Under", selections: ["over", "under"], line: true },
  { id: "ah", label: "Asian handicap", selections: ["home", "away"], line: true },
] as const;

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
      setError("Pick a fixture from the tape first.");
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
        `Logged pick #${pick.id}${pick.risk_override ? " (RISK OVERRIDE — recorded permanently)" : ""}`,
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
      title="Log a bet you placed"
      footnote="Record it after the money is down, with the price you actually got — the ledger measures PLN reality, not the price you hoped for."
    >
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
            <Select value={market} onChange={(e) => chooseMarket(e.target.value)}>
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
          <Field label={spec.line ? "Line" : "Line (n/a)"}>
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
          <Field label="Book">
            <Input
              value={bookmaker}
              onChange={(event) => setBookmaker(event.target.value)}
              placeholder="betclic"
              className="!font-sans"
            />
          </Field>
          <Field label="Stake (PLN)">
            <Input
              value={stake}
              onChange={(event) => setStake(event.target.value)}
              inputMode="decimal"
            />
          </Field>
          <Field label="Price">
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
            tax-free
          </label>
        </div>

        {refusal ? (
          <div
            role="alert"
            className="rounded-md border border-brick/30 bg-brick-soft px-4 py-3"
          >
            <p className="text-sm font-medium text-brick">
              {refusal.kind === "limit"
                ? "The risk layer refused this bet"
                : "This bet is not valid"}
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
                Place it anyway — the pick is marked permanently and appears
                in the weekly report.
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
              saved.includes("OVERRIDE")
                ? "border-brick/30 bg-brick-soft text-brick"
                : "border-line bg-surface text-ink",
            )}
          >
            {saved}
          </p>
        ) : null}

        <div className="flex justify-end">
          <Button onClick={submit} disabled={busy}>
            {busy ? "Saving…" : override ? "Log with override" : "Log this bet"}
          </Button>
        </div>
      </div>
    </Panel>
  );
}
