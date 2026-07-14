/** Typed client for the pitchprob FastAPI read API (single source of truth —
 * the frontend never reimplements model math, ADR 0007). */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
