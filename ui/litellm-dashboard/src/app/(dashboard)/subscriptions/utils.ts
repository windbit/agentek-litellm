import type { StatsDay, StatsReport } from "./api";

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;
const SOFT_LIMIT_PERCENT = 95;
const FULL_PERCENT = 100;

export const STATE_LABELS: Record<string, string> = {
  ACTIVE: "Working",
  SOFT_LIMITED: "Soft limit",
  RATE_LIMITED: "Limit reached",
  HALF_OPEN: "Checking",
  OVERLOADED: "Overloaded",
  BROKEN: "Broken",
  AUTH_REFRESHING: "Refreshing token",
  AUTH_FAILED: "Needs reauthorization",
  BANNED: "Banned",
  DISABLED: "Disabled",
};

// Ant Design tag colors per state.
export const STATE_COLORS: Record<string, string> = {
  ACTIVE: "green",
  SOFT_LIMITED: "blue",
  RATE_LIMITED: "orange",
  HALF_OPEN: "cyan",
  OVERLOADED: "orange",
  BROKEN: "red",
  AUTH_REFRESHING: "cyan",
  AUTH_FAILED: "red",
  BANNED: "red",
  DISABLED: "default",
};

export const REASON_LABELS: Record<string, string> = {
  limit_exhausted: "Usage limit reached",
  soft_threshold: "Weekly window above the soft threshold: used only when nothing else is available",
  unclassified_series: "A series of unclassified errors without a success",
  probe_failed: "The liveness probe failed",
  unhealthy: "Reported unhealthy",
  unauthorized: "The provider rejected the token",
  token_revoked: "The token was revoked",
  account_deactivated: "The account was deactivated",
  operator: "Switched off by an operator",
  probe_pending: "Waiting for a liveness probe",
  expired: "The state expired",
};

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? "";
}

/** "2 d 3 h", "3 h 5 min", "12 min"; "now" once the moment has passed. */
export function formatRemaining(untilIso: string | null, nowMs: number): string {
  if (!untilIso) {
    return "";
  }
  const left = Date.parse(untilIso) - nowMs;
  if (left <= 0) {
    return "now";
  }
  const days = Math.floor(left / DAY_MS);
  const hours = Math.floor((left % DAY_MS) / HOUR_MS);
  const minutes = Math.floor((left % HOUR_MS) / MINUTE_MS);
  if (days > 0) {
    return `${days} d ${hours} h`;
  }
  if (hours > 0) {
    return `${hours} h ${minutes} min`;
  }
  return `${Math.max(minutes, 1)} min`;
}

export function meterColor(percent: number): string {
  if (percent >= FULL_PERCENT) {
    return "#f5222d";
  }
  return percent >= SOFT_LIMIT_PERCENT ? "#faad14" : "#52c41a";
}

export function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(1)}M`;
  }
  return tokens >= 1_000 ? `${(tokens / 1_000).toFixed(1)}k` : String(tokens);
}

export function formatSpend(spend: number): string {
  return `$${spend.toFixed(2)}`;
}

export function formatDuration(seconds: number): string {
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}

export function sumCounts(days: StatsDay[], field: "failures" | "switches" | "state_seconds"): Record<string, number> {
  const total: Record<string, number> = {};
  for (const day of days) {
    for (const [key, value] of Object.entries(day[field])) {
      total[key] = (total[key] ?? 0) + value;
    }
  }
  return total;
}

export function totalsOf(report: StatsReport): { requests: number; tokens: number; spend: number } {
  return report.days.reduce(
    (sum, day) => ({
      requests: sum.requests + day.requests,
      tokens: sum.tokens + day.tokens,
      spend: sum.spend + day.spend,
    }),
    { requests: 0, tokens: 0, spend: 0 },
  );
}

/** The last entry is today (UTC). */
export function todayOf(report: StatsReport | undefined): StatsDay | undefined {
  return report?.days[report.days.length - 1];
}
