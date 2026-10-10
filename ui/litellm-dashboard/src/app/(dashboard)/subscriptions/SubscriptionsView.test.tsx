import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Overview } from "./api";
import SubscriptionsView from "./SubscriptionsView";

const OVERVIEW: Overview = {
  providers: [{ provider: "chatgpt", concurrency_limit: null, subscriptions: 1, working: 0 }],
  subscriptions: [
    {
      id: "s1",
      provider: "chatgpt",
      name: "chatgpt-main",
      email: "ops@example.com",
      plan: "Pro",
      enabled: true,
      priority: 10,
      concurrency_limit: null,
      state: {
        state: "RATE_LIMITED",
        reason: "limit_exhausted",
        source: "provider_response",
        until: new Date(Date.now() + (2 * 24 + 3) * 3_600_000 + 60_000).toISOString(),
        entered_at: new Date().toISOString(),
      },
      limits: null,
    },
  ],
};

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  subscriptionsApi: {
    overview: vi.fn(async () => OVERVIEW),
    stats: vi.fn(async () => ({ subscriptions: {} })),
  },
}));

describe("SubscriptionsView", () => {
  it("tells when a limited subscription returns", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SubscriptionsView accessToken="token" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("returns in 2 d 3 h")).toBeInTheDocument());
    expect(screen.getByText("Limit reached")).toBeInTheDocument();
  });
});
