/** Typed client for the pitchprob FastAPI read API (single source of truth —
 * the frontend never reimplements model math, ADR 0007).
 *
 * URL resolution: on the server, API_URL wins (in the compose stack that is
 * the internal service address http://api:8000, injected at runtime); in the
 * browser it falls back to the build-time NEXT_PUBLIC_API_URL or the
 * published localhost port. */

import type { Interval } from "@/lib/format";

const API_URL =
  (typeof window === "undefined" ? process.env.API_URL : undefined) ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000";

export type League = {
  code: string;
  name: string;
  country: string;
  match_count: number;
};

export type Outcome3 = { home: number; draw: number; away: number };

export type ValueEntry = {
  offered_price: number;
  model_probability: number;
  market_probability: number;
  p_bet: number;
  fair_price: number;
  expected_value: number;
  kelly_fraction: number;
};

export type CountsSection = {
  expected: { home: number; away: number; total: number };
  totals: Record<string, { over: number; under: number }>;
};

export type MarketBook = {
  league: string;
  home_team: string;
  away_team: string;
  generated_at: string;
  trained_on_matches: number;
  train_max_date: string;
  ensemble_weights: Record<string, number>;
  expected_goals: { home: number; away: number };
  markets: {
    "1x2": Record<string, Outcome3>;
    double_chance: { home_or_draw: number; home_or_away: number; draw_or_away: number };
    draw_no_bet: { home: number; away: number };
    totals: Record<string, { over: number; under: number }>;
    btts: { yes: number; no: number };
    asian_handicap: Record<string, { home: number; push: number; away: number }>;
    correct_score_top: Array<{ score: string; probability: number }>;
  };
  counts_markets: { caveat: string; corners?: CountsSection; cards?: CountsSection };
  value_analysis?: Record<"home" | "draw" | "away", ValueEntry>;
  disclaimer: string;
};

export type CouponLeg = {
  match_label: string;
  market: string;
  selection: string;
  probability: number;
  reasons: string[];
};

export type CouponsResponse = {
  tier: string;
  band: [number, number];
  caveat: string;
  coupons: Array<{ joint_probability: number; legs: CouponLeg[] }>;
};

export type BacktestRow = {
  id: number;
  created_at: string;
  config: Record<string, unknown>;
  metrics: Record<string, unknown>;
};

/** Prices and stakes cross the wire as strings: the backend keeps money in
 * Decimal and will not round it into a float on the way out. */
export type Money = string;

export type TapeEvent = {
  event_id: string;
  home_team: string;
  away_team: string;
  commence_time: string;
};

export type Verdict = "PLAY" | "NO BET" | "STALE" | "NO ANCHOR" | "UNVERIFIED";

export type QuoteInput = {
  bookmaker: string;
  price: Money;
  tax_free?: boolean;
  boosted_price?: Money | null;
  payout_haircut?: number;
  source?: "operator" | "feed";
};

export type ScanVerdict = {
  bookmaker: string;
  price_quoted: Money;
  price_effective: Money;
  tax_free: boolean;
  /** Payout regime the router applied: 1.00 / 0.94 / 0.88 (ADR 0017). */
  tax_multiplier: Money;
  boosted: boolean;
  promo_value: number | null;
  edge: number | null;
  edge_model: number | null;
  verdict: Verdict;
  source: "operator" | "feed";
};

export type ScanResponse = {
  event_id: string;
  home_team: string;
  away_team: string;
  commence_time: string;
  market: string;
  selection: string;
  line: Money | null;
  anchor: {
    bookmaker: string;
    price: Money;
    fair_probability: number;
    observed_at: string;
    age_hours: number;
    line: Money | null;
  } | null;
  model_probability: number | null;
  verdicts: ScanVerdict[];
  caveats: string[];
};

export type Pick = {
  id: number;
  kickoff_utc: string;
  home_team: string;
  away_team: string;
  market: string;
  selection: string;
  line: Money | null;
  bookmaker: string;
  stake_pln: Money;
  price_quoted: Money;
  price_effective: Money;
  tax_free: boolean;
  /** Payout regime behind price_effective: 1.00 / 0.94 / 0.88 (ADR 0017). */
  tax_multiplier: Money;
  price_sharp: Money | null;
  gross_return_pln: Money | null;
  settled_at: string | null;
  closing_observed_at: string | null;
  clv_exec: number | null;
  clv_sharp: number | null;
  clv_shopping: number | null;
  risk_override: boolean;
  risk_note: string | null;
};

export type WeeklyMetrics = {
  generated_at: string;
  window_days: number;
  window: {
    n_picks: number;
    n_settled: number;
    staked_pln: number;
    profit_pln: number;
  };
  clv: {
    n: number;
    n_blocks: number;
    mean_exec: number | null;
    mean_sharp: number | null;
    mean_shopping: number | null;
    exec_ci: Interval | null;
    sharp_ci: Interval | null;
    shopping_ci: Interval | null;
  };
  tax_free: Record<string, { used_pln: number; remaining_pln: number }>;
  overrides: Array<{
    pick_id: number;
    placed_at: string;
    fixture: string;
    stake_pln: number;
    risk_note: string | null;
  }>;
  drawdown: {
    equity_pln: number;
    peak_equity_pln: number;
    drawdown_pln: number;
    limit_pln: number;
    breaker_tripped: boolean;
  };
};

export type LedgerResponse = {
  picks: Pick[];
  summary: {
    n_picks: number;
    n_settled: number;
    total_staked_pln: number;
    total_returned_pln: number;
    profit_pln: number;
    roi: number | null;
    n_with_clv: number;
    mean_clv_exec: number | null;
    mean_clv_sharp: number | null;
    mean_shopping_value: number | null;
  };
  weekly: WeeklyMetrics;
  caveats: string[];
};

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status text
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export async function getLeagues(): Promise<League[]> {
  return handle(await fetch(`${API_URL}/v1/leagues`, { cache: "no-store" }));
}

export async function getTeams(league: string): Promise<string[]> {
  return handle(await fetch(`${API_URL}/v1/leagues/${league}/teams`, { cache: "no-store" }));
}

export async function getBacktests(): Promise<BacktestRow[]> {
  return handle(await fetch(`${API_URL}/v1/backtests`, { cache: "no-store" }));
}

export async function postPrediction(request: {
  league: string;
  home_team: string;
  away_team: string;
  offered_1x2?: [number, number, number] | null;
}): Promise<MarketBook> {
  return handle(
    await fetch(`${API_URL}/v1/predictions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}

export async function postCoupons(request: {
  tier: string;
  fixtures: Array<{ league: string; home_team: string; away_team: string }>;
  max_legs?: number;
}): Promise<CouponsResponse> {
  return handle(
    await fetch(`${API_URL}/v1/coupons`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}

export async function getTapeEvents(query = ""): Promise<TapeEvent[]> {
  const search = query ? `?query=${encodeURIComponent(query)}` : "";
  return handle(
    await fetch(`${API_URL}/v1/scanner/events${search}`, { cache: "no-store" }),
  );
}

export async function postScan(request: {
  market: string;
  selection: string;
  quotes: QuoteInput[];
  line?: Money | null;
  event_id?: string | null;
  model_probability?: number | null;
  min_edge?: number;
}): Promise<ScanResponse> {
  return handle(
    await fetch(`${API_URL}/v1/scanner/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}

export type PickRequest = {
  home_team: string;
  away_team: string;
  kickoff_utc: string;
  market: string;
  selection: string;
  bookmaker: string;
  stake_pln: Money;
  price_quoted: Money;
  line?: Money | null;
  /** null/omitted = the API derives the regime automatically (ADR 0017). */
  tax_free?: boolean | null;
  event_id?: string | null;
  notes?: string | null;
  override_risk?: boolean;
};

/** A refusal that carries which kind it was. 409 = the ledger's state
 * forbids this bet (a limit, the breaker) — a decision to accept or
 * override. 400 = the bet is malformed — fix the form. Collapsing the two
 * into one "error" would leave the operator unable to tell which. */
export class PickRefused extends Error {
  constructor(
    message: string,
    readonly kind: "limit" | "invalid",
  ) {
    super(message);
    this.name = "PickRefused";
  }
}

export async function postPick(request: PickRequest): Promise<Pick> {
  const response = await fetch(`${API_URL}/v1/ledger/picks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (response.status === 409 || response.status === 400) {
    const body = await response.json().catch(() => ({}));
    throw new PickRefused(
      typeof body.detail === "string" ? body.detail : "Refused.",
      response.status === 409 ? "limit" : "invalid",
    );
  }
  return handle(response);
}

export async function getLedger(): Promise<LedgerResponse> {
  return handle(await fetch(`${API_URL}/v1/ledger`, { cache: "no-store" }));
}
