import { PlusOutlined } from "@ant-design/icons";
import { Alert, Button, Spin, Typography } from "antd";
import { useState } from "react";
import CommitNumber from "./components/CommitNumber";
import LoginModal, { type LoginRequest } from "./components/LoginModal";
import SubscriptionsTable from "./components/SubscriptionsTable";
import { useSubscriptionActions, useSubscriptions, useSubscriptionStats } from "./hooks/useSubscriptions";
import type { SubscriptionView } from "./api";

function errorText(error: unknown): string | undefined {
  return error instanceof Error ? error.message : undefined;
}

interface SubscriptionsViewProps {
  accessToken: string | null;
}

export default function SubscriptionsView({ accessToken }: SubscriptionsViewProps) {
  const overview = useSubscriptions(accessToken);
  const stats = useSubscriptionStats(accessToken);
  const actions = useSubscriptionActions(accessToken);
  const [login, setLogin] = useState<LoginRequest | null>(null);

  if (overview.isLoading) {
    return <Spin className="m-8" />;
  }
  if (!overview.data) {
    return (
      <Alert
        className="m-6"
        type="error"
        message="Subscriptions are unavailable"
        description={errorText(overview.error)}
      />
    );
  }

  const { providers, subscriptions } = overview.data;
  const nowMs = overview.dataUpdatedAt;
  const defaultProvider = providers[0]?.provider ?? "chatgpt";

  const reauthorize = (subscription: SubscriptionView) =>
    setLogin({ provider: subscription.provider, subscription: { id: subscription.id, name: subscription.name } });

  return (
    <div className="p-6">
      <Typography.Title level={3}>Subscriptions</Typography.Title>
      <Typography.Paragraph type="secondary">
        Provider subscriptions and their state. Actions here are performed by the gateway.
      </Typography.Paragraph>
      {overview.error && (
        <Alert
          className="mb-4"
          type="warning"
          message="Could not refresh the subscriptions; showing the last data"
          description={errorText(overview.error)}
        />
      )}
      {stats.error && (
        <Alert
          className="mb-4"
          type="warning"
          message="Statistics are unavailable"
          description={errorText(stats.error)}
        />
      )}
      <div className="flex flex-wrap items-center gap-4 mb-4">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setLogin({ provider: defaultProvider })}>
          Subscription
        </Button>
        {providers.map((provider) => (
          <span key={provider.provider} className="flex items-center gap-2 text-sm">
            {provider.provider} concurrency ({provider.working}/{provider.subscriptions} working)
            <CommitNumber
              value={provider.concurrency_limit}
              min={1}
              placeholder="no limit"
              onCommit={(limit) => actions.setProviderConcurrency.mutateAsync({ provider: provider.provider, limit })}
            />
          </span>
        ))}
      </div>
      <SubscriptionsTable
        subscriptions={subscriptions}
        actions={actions}
        nowMs={nowMs}
        stats={stats.data}
        onReauthorize={reauthorize}
      />
      <Alert
        className="mt-4"
        type="info"
        message="Selection order: healthy before soft-limited, then priority (lower first), then the weekly window that resets sooner, then load."
      />
      <LoginModal
        accessToken={accessToken}
        request={login}
        onClose={() => setLogin(null)}
        onDone={() => {
          setLogin(null);
          actions.reload();
        }}
      />
    </div>
  );
}
