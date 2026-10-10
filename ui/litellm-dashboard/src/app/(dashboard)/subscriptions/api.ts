import { getGlobalLitellmHeaderName, getProxyBaseUrl } from "@/components/networking";
import { createApiClient } from "@/lib/http/client";

const client = createApiClient({ getBaseUrl: getProxyBaseUrl, getAuthHeaderName: getGlobalLitellmHeaderName });

const ROOT = "/agentek/subscriptions";

export interface WindowView {
  used_percent: number;
  reset_at: string;
}

export interface LimitsView {
  five_hour: WindowView | null;
  weekly: WindowView | null;
  observed_at: string;
  source: string;
}

export interface StateView {
  state: string;
  reason: string;
  source: string;
  until: string | null;
  entered_at: string;
}

export interface SubscriptionView {
  id: string;
  provider: string;
  name: string;
  email: string | null;
  plan: string | null;
  enabled: boolean;
  priority: number;
  concurrency_limit: number | null;
  state: StateView;
  limits: LimitsView | null;
  in_flight: number;
}

export interface ProviderView {
  provider: string;
  concurrency_limit: number | null;
  subscriptions: number;
  working: number;
}

export interface Overview {
  providers: ProviderView[];
  subscriptions: SubscriptionView[];
}

export interface DeviceLogin {
  device_auth_id: string;
  user_code: string;
  verify_url: string;
}

export type LoginTarget = { name: string } | { subscription_id: string };

export type LoginPollResult = { status: "pending" } | { status: "done"; subscription: SubscriptionView };

export type CountMap = Record<string, number>;

export interface StatsDay {
  day: string;
  requests: number;
  tokens: number;
  spend: number;
  failures: CountMap;
  switches: CountMap;
  state_seconds: CountMap;
}

export interface ModelUsage {
  model: string;
  requests: number;
  tokens: number;
  spend: number;
}

export interface StatsReport {
  days: StatsDay[];
  top_models: ModelUsage[];
}

export interface StatsOverview {
  subscriptions: Record<string, StatsReport>;
}

export interface SettingsChange {
  priority?: number;
  max_concurrency?: number | null;
}

export const subscriptionsApi = {
  overview: (accessToken: string) => client.get<Overview>(ROOT, { accessToken }),
  stats: (accessToken: string, days: number) =>
    client.get<StatsOverview>(`${ROOT}/stats`, { accessToken, query: { days } }),
  setEnabled: (accessToken: string, id: string, enabled: boolean) =>
    client.put<SubscriptionView>(`${ROOT}/${id}/enabled`, { accessToken, body: { enabled } }),
  updateSettings: (accessToken: string, id: string, change: SettingsChange) =>
    client.request<SubscriptionView>("PATCH", `${ROOT}/${id}`, { accessToken, body: change }),
  refreshLimits: (accessToken: string, id: string) =>
    client.post<{ refreshed: boolean; status: string; subscription: SubscriptionView }>(
      `${ROOT}/${id}/refresh-limits`,
      { accessToken },
    ),
  remove: (accessToken: string, id: string) => client.delete<void>(`${ROOT}/${id}`, { accessToken }),
  setProviderConcurrency: (accessToken: string, provider: string, limit: number | null) =>
    client.put<ProviderView>(`${ROOT}/providers/${provider}`, { accessToken, body: { concurrency_limit: limit } }),
  loginStart: (accessToken: string, provider: string) =>
    client.post<DeviceLogin>(`${ROOT}/login/start`, { accessToken, body: { provider } }),
  loginPoll: (accessToken: string, provider: string, login: DeviceLogin, target: LoginTarget) =>
    client.post<LoginPollResult>(`${ROOT}/login/poll`, {
      accessToken,
      body: { provider, device_auth_id: login.device_auth_id, user_code: login.user_code, ...target },
    }),
};
