import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
      in_flight: 2,
    },
  ],
};

const api = vi.hoisted(() => ({
  overview: vi.fn(),
  stats: vi.fn(),
  updateSettings: vi.fn(),
}));

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  subscriptionsApi: api,
}));

function renderView() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SubscriptionsView accessToken="token" />
    </QueryClientProvider>,
  );
}

async function priorityField(): Promise<HTMLElement> {
  const row = (await screen.findByText("chatgpt-main")).closest("tr")!;
  return within(row).getAllByRole("spinbutton")[0];
}

beforeEach(() => {
  api.overview.mockReset().mockResolvedValue(OVERVIEW);
  api.stats.mockReset().mockResolvedValue({ subscriptions: {} });
  api.updateSettings.mockReset();
});

describe("SubscriptionsView", () => {
  it("tells when a limited subscription returns and how many requests it serves now", async () => {
    renderView();

    await waitFor(() => expect(screen.getByText("returns in 2 d 3 h")).toBeInTheDocument());
    expect(screen.getByText("Limit reached")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("says statistics are unavailable instead of showing them empty", async () => {
    api.stats.mockRejectedValue(new Error("stats down"));

    renderView();

    await waitFor(() => expect(screen.getByText("Statistics are unavailable")).toBeInTheDocument());
    expect(screen.getByText("chatgpt-main")).toBeInTheDocument();
  });

  it("puts the saved priority back when saving fails", async () => {
    api.updateSettings.mockRejectedValue(new Error("rejected"));
    renderView();
    const priority = await priorityField();

    fireEvent.change(priority, { target: { value: "99" } });
    fireEvent.blur(priority);
    await waitFor(() => expect(api.updateSettings).toHaveBeenCalled());

    await waitFor(() => expect(priority).toHaveValue("10"));
  });

  it("puts the saved priority back when the field is cleared", async () => {
    renderView();
    const priority = await priorityField();

    fireEvent.change(priority, { target: { value: "" } });
    fireEvent.blur(priority);

    await waitFor(async () => expect(await priorityField()).toHaveValue("10"));
    expect(api.updateSettings).not.toHaveBeenCalled();
  });
});
