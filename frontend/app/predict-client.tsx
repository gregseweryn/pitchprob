"use client";

/** Fixture pricing: pick a league and two teams, optionally paste offered
 * 1X2 prices, get the full market book. First request per league fits the
 * models server-side (a few seconds) — the skeleton says so instead of
 * pretending it's instant. */

import { useCallback, useEffect, useState } from "react";

import type { League, MarketBook } from "@/lib/api";
import { getTeams, postPrediction } from "@/lib/api";
import { BookView } from "@/components/book";
import { Button, ErrorBanner, Field, Input, Panel, Select } from "@/components/ui";

function BookSkeleton() {
  return (
    <div aria-live="polite" className="flex flex-col gap-4">
      <p className="text-sm text-ink-muted">
        Fitting models for this league — the first request per league takes a few
        seconds; repeats are instant.
      </p>
      <div className="skeleton h-8 w-2/3" />
      <div className="grid gap-5 md:grid-cols-2">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="skeleton h-48" />
        ))}
      </div>
    </div>
  );
}

export function PredictClient({ leagues }: { leagues: League[] }) {
  const [league, setLeague] = useState(leagues[0]?.code ?? "E0");
  const [teams, setTeams] = useState<string[]>([]);
  const [home, setHome] = useState("");
  const [away, setAway] = useState("");
  const [odds, setOdds] = useState({ home: "", draw: "", away: "" });
  const [book, setBook] = useState<MarketBook | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getTeams(league)
      .then((names) => {
        if (cancelled) return;
        setTeams(names);
        setHome((current) => (names.includes(current) ? current : names[0] ?? ""));
        setAway((current) =>
          names.includes(current) && current !== names[0]
            ? current
            : names[1] ?? "",
        );
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err)),
      );
    return () => {
      cancelled = true;
    };
  }, [league]);

  const submit = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const offered =
        odds.home && odds.draw && odds.away
          ? ([Number(odds.home), Number(odds.draw), Number(odds.away)] as [
              number,
              number,
              number,
            ])
          : null;
      if (offered && offered.some((p) => !Number.isFinite(p) || p <= 1)) {
        throw new Error("Offered prices must be decimal odds greater than 1.00.");
      }
      setBook(
        await postPrediction({
          league,
          home_team: home,
          away_team: away,
          offered_1x2: offered,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [league, home, away, odds]);

  const sameTeams = home !== "" && home === away;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Price a fixture</h1>
        <p className="mt-1 max-w-[65ch] text-sm text-ink-muted">
          Full market probability book from the model stack — including where the
          models disagree with each other.
        </p>
      </div>

      <Panel>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <Field label="League">
            <Select value={league} onChange={(e) => setLeague(e.target.value)}>
              {leagues.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Home team">
            <Select value={home} onChange={(e) => setHome(e.target.value)}>
              {teams.map((name) => (
                <option key={name}>{name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Away team">
            <Select value={away} onChange={(e) => setAway(e.target.value)}>
              {teams.map((name) => (
                <option key={name}>{name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Offered 1X2 odds (optional)">
            <div className="flex gap-1.5">
              {(["home", "draw", "away"] as const).map((slot) => (
                <Input
                  key={slot}
                  value={odds[slot]}
                  onChange={(e) => setOdds({ ...odds, [slot]: e.target.value })}
                  placeholder={slot === "home" ? "2.05" : slot === "draw" ? "3.40" : "3.80"}
                  inputMode="decimal"
                  aria-label={`Offered ${slot} price`}
                  className="w-20"
                />
              ))}
            </div>
          </Field>
          <Button type="submit" disabled={loading || sameTeams || !home || !away}>
            {loading ? "Pricing…" : "Price the fixture"}
          </Button>
          {sameTeams ? (
            <p className="text-xs text-brick">Pick two different teams.</p>
          ) : null}
        </form>
      </Panel>

      {error ? <ErrorBanner message={error} /> : null}
      {loading ? <BookSkeleton /> : null}
      {!loading && book ? <BookView book={book} /> : null}
      {!loading && !book && !error ? (
        <p className="text-sm text-ink-muted">
          Pick a fixture above — any pairing of teams in the league, played or
          hypothetical.
        </p>
      ) : null}
    </div>
  );
}
