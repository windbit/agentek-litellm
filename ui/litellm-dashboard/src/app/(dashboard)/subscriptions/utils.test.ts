import { describe, expect, it } from "vitest";
import { formatDuration, formatRemaining, sumCounts } from "./utils";

const NOW = Date.parse("2026-10-10T12:00:00Z");

describe("formatRemaining", () => {
  it("shows days and hours for a weekly reset", () => {
    expect(formatRemaining("2026-10-12T15:00:00Z", NOW)).toBe("2 d 3 h");
  });

  it("shows hours and minutes under a day", () => {
    expect(formatRemaining("2026-10-10T15:05:00Z", NOW)).toBe("3 h 5 min");
  });

  it("says now once the moment has passed", () => {
    expect(formatRemaining("2026-10-10T11:00:00Z", NOW)).toBe("now");
  });

  it("is empty without a moment", () => {
    expect(formatRemaining(null, NOW)).toBe("");
  });
});

describe("sumCounts", () => {
  it("adds the same key across days", () => {
    const day = (limit: number) => ({
      day: "2026-10-10",
      requests: 0,
      tokens: 0,
      spend: 0,
      failures: { limit },
      switches: {},
      state_seconds: {},
    });
    expect(sumCounts([day(2), day(3)], "failures")).toEqual({ limit: 5 });
  });
});

describe("formatDuration", () => {
  it("never shows 60 minutes", () => {
    expect(formatDuration(3599.9 + 3600)).toBe("2 h 0 min");
  });
});
