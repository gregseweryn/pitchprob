/** Audit finding A6: the dashboard now renders sharp CLV and bootstrap CIs;
 * these helpers are the only formatting logic behind that, so they get the
 * hand-computed-value treatment the backend formulas get. */

import { describe, expect, it } from "vitest";

import { ageLabel, ciRange, pctOrDash, pln, signedPct, signedPln } from "./format";

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

describe("pctOrDash", () => {
  it("renders a dash for absent CLV rather than a zero", () => {
    // A pick without CLV has not been measured; printing 0.0% would assert
    // the market did not move, which nobody observed.
    expect(pctOrDash(null)).toBe("—");
    expect(pctOrDash(undefined)).toBe("—");
  });

  it("keeps a genuine zero distinct from absence", () => {
    expect(pctOrDash(0)).toBe("+0.0%");
  });
});

describe("pln", () => {
  it("renders money to the grosz from a Decimal string", () => {
    expect(pln("5")).toBe("5.00 PLN");
    expect(pln(10.5)).toBe("10.50 PLN");
  });
});

describe("signedPln", () => {
  it("uses a real minus sign for losses", () => {
    expect(signedPln(-75)).toBe("−75.00 PLN");
    expect(signedPln(0.5)).toBe("+0.50 PLN");
  });
});

describe("ageLabel", () => {
  it("uses minutes only under an hour, where it changes a decision", () => {
    expect(ageLabel(0.5)).toBe("30m ago");
  });

  it("rounds to hours through two days — a daily tape has no finer truth", () => {
    expect(ageLabel(10)).toBe("10h ago");
    expect(ageLabel(31.4)).toBe("31h ago");
  });

  it("switches to days beyond 48h", () => {
    expect(ageLabel(72)).toBe("3d ago");
  });

  it("does not pretend a future timestamp is an age", () => {
    expect(ageLabel(-1)).toBe("in the future");
  });
});
