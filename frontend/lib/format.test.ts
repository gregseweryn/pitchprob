/** Audit finding A6: the dashboard now renders sharp CLV and bootstrap CIs;
 * these helpers are the only formatting logic behind that, so they get the
 * hand-computed-value treatment the backend formulas get. */

import { describe, expect, it } from "vitest";

import { ciRange, signedPct } from "./format";

describe("signedPct", () => {
  it("renders positive values with an explicit sign", () => {
    expect(signedPct(0.022)).toBe("+2.2%");
  });

  it("renders negative values", () => {
    expect(signedPct(-0.088)).toBe("-8.8%");
  });
});

describe("ciRange", () => {
  it("renders both bounds as signed percentages", () => {
    expect(ciRange({ lo: -0.088, hi: 0.022 })).toBe("(-8.8%, +2.2%)");
  });

  it("returns null when the interval is missing entirely", () => {
    expect(ciRange(undefined)).toBeNull();
    expect(ciRange(null)).toBeNull();
  });

  it("returns null when either bound is absent — no fake-precise dashes", () => {
    expect(ciRange({ lo: -0.01 })).toBeNull();
    expect(ciRange({ hi: 0.01, lo: null })).toBeNull();
  });

  it("treats a zero bound as present, not missing", () => {
    expect(ciRange({ lo: 0, hi: 0.01 })).toBe("(+0.0%, +1.0%)");
  });
});
