import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { message } from "antd";
import { subscriptionsApi, type SettingsChange } from "../api";

const OVERVIEW_KEY = ["agentek-subscriptions"];
const STATS_KEY = ["agentek-subscription-stats"];
const REFRESH_INTERVAL_MS = 10_000;
const STATS_DAYS = 30;

export function useSubscriptions(accessToken: string | null) {
  return useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: () => subscriptionsApi.overview(accessToken!),
    enabled: Boolean(accessToken),
    refetchInterval: REFRESH_INTERVAL_MS,
  });
}

export function useSubscriptionStats(accessToken: string | null) {
  return useQuery({
    queryKey: STATS_KEY,
    queryFn: () => subscriptionsApi.stats(accessToken!, STATS_DAYS),
    enabled: Boolean(accessToken),
    refetchInterval: REFRESH_INTERVAL_MS * 6,
  });
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Request failed";
}

export function useSubscriptionActions(accessToken: string | null) {
  const queryClient = useQueryClient();
  const reload = () => queryClient.invalidateQueries({ queryKey: OVERVIEW_KEY });
  const onError = (error: unknown) => message.error(errorText(error));

  const setEnabled = useMutation({
    mutationFn: (arg: { id: string; enabled: boolean }) =>
      subscriptionsApi.setEnabled(accessToken!, arg.id, arg.enabled),
    onSuccess: reload,
    onError,
  });
  const updateSettings = useMutation({
    mutationFn: (arg: { id: string; change: SettingsChange }) =>
      subscriptionsApi.updateSettings(accessToken!, arg.id, arg.change),
    onSuccess: reload,
    onError,
  });
  const refreshLimits = useMutation({
    mutationFn: (id: string) => subscriptionsApi.refreshLimits(accessToken!, id),
    onSuccess: (result) => {
      message.info(result.refreshed ? "Limits refreshed" : "Limits were refreshed moments ago");
      reload();
    },
    onError,
  });
  const remove = useMutation({
    mutationFn: (id: string) => subscriptionsApi.remove(accessToken!, id),
    onSuccess: reload,
    onError,
  });
  const setProviderConcurrency = useMutation({
    mutationFn: (arg: { provider: string; limit: number | null }) =>
      subscriptionsApi.setProviderConcurrency(accessToken!, arg.provider, arg.limit),
    onSuccess: reload,
    onError,
  });

  return { setEnabled, updateSettings, refreshLimits, remove, setProviderConcurrency, reload };
}
