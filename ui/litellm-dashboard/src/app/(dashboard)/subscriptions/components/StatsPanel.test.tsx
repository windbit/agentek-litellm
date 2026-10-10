import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { StatsDay, StatsReport } from "../api";
import StatsPanel from "./StatsPanel";

const day = (overrides: Partial<StatsDay>): StatsDay => ({
  day: "2026-10-10",
  requests: 0,
  tokens: 0,
  spend: 0,
  failures: {},
  switches: {},
  state_seconds: {},
  ...overrides,
});

describe("StatsPanel", () => {
  it("shows failures by reason, time in state and the top model over the period", () => {
    const report: StatsReport = {
      days: [
        day({ day: "2026-10-09", failures: { limit: 1 }, state_seconds: { RATE_LIMITED: 3600 } }),
        day({ failures: { limit: 2 }, requests: 5, tokens: 6000, spend: 0.12 }),
      ],
      top_models: [{ model: "gpt-5.4", requests: 5, tokens: 6000, spend: 0.12 }],
    };

    render(<StatsPanel report={report} />);

    expect(screen.getByText("limit: 3")).toBeInTheDocument();
    expect(screen.getByText("Limit reached: 1 h 0 min")).toBeInTheDocument();
    expect(screen.getByText(/gpt-5.4: 5 requests/)).toBeInTheDocument();
  });
});
