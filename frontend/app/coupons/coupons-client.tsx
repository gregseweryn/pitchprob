"use client";

/** Coupon builder: pick a risk tier and 2-6 fixtures, get accumulators whose
 * joint probability sits inside the tier band, each leg with its reasons.
 * The independence caveat comes from the API and is always rendered. */

import { useCallback, useEffect, useState } from "react";

import type { CouponsResponse, League } from "@/lib/api";
import { getTeams, postCoupons } from "@/lib/api";
import { pct, cx } from "@/lib/format";
import { Button, ErrorBanner, Field, Panel, Select } from "@/components/ui";

const TIER_LABELS: Array<{ id: string; label: string; band: string }> = [
  { id: "safe", label: "Safe", band: "80–95%" },
  { id: "balanced", label: "Balanced", band: "60–80%" },
  { id: "value", label: "Value", band: "40–60%" },
  { id: "high_risk", label: "High risk", band: "20–40%" },
];

type FixtureRow = { league: string; home_team: string; away_team: string };

function TierPicker({
  tier,
  onChange,
}: {
  tier: string;
  onChange: (tier: string) => void;
}) {
  return (
    <div role="radiogroup" aria-label="Risk tier" className="flex flex-wrap gap-2">
      {TIER_LABELS.map((item) => (
        <button
          key={item.id}
          type="button"
          role="radio"
          aria-checked={tier === item.id}
          onClick={() => onChange(item.id)}
          className={cx(
            "rounded-md border px-3 py-1.5 text-sm transition-colors duration-150",
            "focus:outline-2 focus:outline-offset-1 focus:outline-gold-ink",
            tier === item.id
              ? "border-gold-ink bg-gold-soft font-medium text-gold-ink"
              : "border-line text-ink-muted hover:text-ink",
          )}
        >
          {item.label}
          <span className="num ml-1.5 text-xs opacity-80">{item.band}</span>
        </button>
      ))}
    </div>
  );
}

export function CouponsClient({ leagues }: { leagues: League[] }) {
  const defaultLeague = leagues[0]?.code ?? "E0";
  const [tier, setTier] = useState("balanced");
  const [teamsByLeague, setTeamsByLeague] = useState<Record<string, string[]>>({});
  const [rows, setRows] = useState<FixtureRow[]>([]);
  const [result, setResult] = useState<CouponsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ensureTeams = useCallback(
    async (league: string): Promise<string[]> => {
      if (teamsByLeague[league]) return teamsByLeague[league];
      const names = await getTeams(league);
      setTeamsByLeague((current) => ({ ...current, [league]: names }));
      return names;
    },
    [teamsByLeague],
  );

  useEffect(() => {
    void (async () => {
      try {
        const names = await ensureTeams(defaultLeague);
        setRows([
          { league: defaultLeague, home_team: names[0] ?? "", away_team: names[1] ?? "" },
          { league: defaultLeague, home_team: names[2] ?? "", away_team: names[3] ?? "" },
        ]);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- initial seed only
  }, []);

  const updateRow = async (index: number, patch: Partial<FixtureRow>) => {
    if (patch.league) {
      const names = await ensureTeams(patch.league);
      patch.home_team = names[0] ?? "";
      patch.away_team = names[1] ?? "";
    }
    setRows((current) =>
      current.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  };

  const submit = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await postCoupons({ tier, fixtures: rows }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const invalid = rows.some((row) => !row.home_team || row.home_team === row.away_team);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Coupons</h1>
        <p className="mt-1 max-w-[65ch] text-sm text-ink-muted">
          Accumulators whose estimated joint probability lands inside the tier band.
          Every leg explains why it was chosen.
        </p>
      </div>

      <Panel>
        <div className="flex flex-col gap-4">
          <TierPicker tier={tier} onChange={setTier} />

          <div className="flex flex-col gap-2">
            {rows.map((row, index) => {
              const teams = teamsByLeague[row.league] ?? [];
              return (
                <div key={index} className="flex flex-wrap items-end gap-2">
                  <Field label={index === 0 ? "League" : ""}>
                    <Select
                      value={row.league}
                      onChange={(e) => void updateRow(index, { league: e.target.value })}
                    >
                      {leagues.map((item) => (
                        <option key={item.code} value={item.code}>
                          {item.code}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label={index === 0 ? "Home" : ""}>
                    <Select
                      value={row.home_team}
                      onChange={(e) => void updateRow(index, { home_team: e.target.value })}
                    >
                      {teams.map((name) => (
                        <option key={name}>{name}</option>
                      ))}
                    </Select>
                  </Field>
                  <Field label={index === 0 ? "Away" : ""}>
                    <Select
                      value={row.away_team}
                      onChange={(e) => void updateRow(index, { away_team: e.target.value })}
                    >
                      {teams.map((name) => (
                        <option key={name}>{name}</option>
                      ))}
                    </Select>
                  </Field>
                  {rows.length > 1 ? (
                    <Button
                      variant="ghost"
                      aria-label={`Remove fixture ${index + 1}`}
                      onClick={() =>
                        setRows((current) => current.filter((_, i) => i !== index))
                      }
                    >
                      Remove
                    </Button>
                  ) : null}
                </div>
              );
            })}
          </div>

          <div className="flex gap-2">
            <Button
              variant="ghost"
              disabled={rows.length >= 6}
              onClick={() => setRows((current) => [...current, { ...current[0] }])}
            >
              Add fixture
            </Button>
            <Button onClick={() => void submit()} disabled={loading || invalid || !rows.length}>
              {loading ? "Building…" : "Build coupons"}
            </Button>
          </div>
          {invalid ? (
            <p className="text-xs text-brick">Each fixture needs two different teams.</p>
          ) : null}
        </div>
      </Panel>

      {error ? <ErrorBanner message={error} /> : null}
      {loading ? (
        <div aria-live="polite" className="flex flex-col gap-3">
          <p className="text-sm text-ink-muted">
            Pricing fixtures — first request per league takes a few seconds.
          </p>
          {[0, 1].map((i) => (
            <div key={i} className="skeleton h-32" />
          ))}
        </div>
      ) : null}

      {result && !loading ? (
        result.coupons.length === 0 ? (
          <Panel>
            <p className="text-sm text-ink-muted">
              No accumulator built from these fixtures lands inside the{" "}
              {pct(result.band[0])}–{pct(result.band[1])} band. Add fixtures or pick a
              different tier — an empty result is the engine declining, not failing.
            </p>
          </Panel>
        ) : (
          <div className="flex flex-col gap-4">
            {result.coupons.map((coupon, index) => (
              <Panel
                key={index}
                title={`#${index + 1} · joint probability ${pct(coupon.joint_probability)}`}
              >
                <ol className="flex flex-col gap-3">
                  {coupon.legs.map((leg, legIndex) => (
                    <li key={legIndex} className="text-sm">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="font-medium">{leg.match_label}</span>
                        <span className="num text-ink-muted">
                          {leg.market}: {leg.selection} · {pct(leg.probability)}
                        </span>
                      </div>
                      <ul className="mt-1 flex flex-col gap-0.5 text-xs text-ink-muted">
                        {leg.reasons.map((reason, reasonIndex) => (
                          <li key={reasonIndex}>+ {reason}</li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ol>
              </Panel>
            ))}
            <p className="text-xs leading-relaxed text-ink-muted">{result.caveat}</p>
          </div>
        )
      ) : null}
    </div>
  );
}
