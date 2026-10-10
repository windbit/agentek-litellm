import { ReloadOutlined } from "@ant-design/icons";
import { Button, Popconfirm, Switch, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { StatsOverview, SubscriptionView } from "../api";
import { formatSpend, formatTokens, todayOf } from "../utils";
import type { useSubscriptionActions } from "../hooks/useSubscriptions";
import CommitNumber from "./CommitNumber";
import StateCell from "./StateCell";
import StatsPanel from "./StatsPanel";
import WindowMeter from "./WindowMeter";

type Actions = ReturnType<typeof useSubscriptionActions>;

interface SubscriptionsTableProps {
  subscriptions: SubscriptionView[];
  actions: Actions;
  nowMs: number;
  stats: StatsOverview | undefined;
  onReauthorize: (subscription: SubscriptionView) => void;
}

export default function SubscriptionsTable({
  subscriptions,
  actions,
  nowMs,
  stats,
  onReauthorize,
}: SubscriptionsTableProps) {
  const columns: ColumnsType<SubscriptionView> = [
    {
      title: "Enabled",
      key: "enabled",
      width: 80,
      render: (_, subscription) => (
        <Switch
          checked={subscription.enabled}
          loading={actions.setEnabled.isPending && actions.setEnabled.variables?.id === subscription.id}
          onChange={(enabled) => actions.setEnabled.mutate({ id: subscription.id, enabled })}
        />
      ),
    },
    {
      title: "Subscription",
      key: "name",
      render: (_, subscription) => (
        <div>
          <div className="font-semibold">{subscription.name}</div>
          <div className="text-xs text-gray-500">
            {[subscription.email, subscription.plan].filter(Boolean).join(" · ")}
          </div>
        </div>
      ),
    },
    {
      title: "State",
      key: "state",
      render: (_, subscription) => <StateCell state={subscription.state} nowMs={nowMs} />,
    },
    {
      title: "5 hours",
      key: "five_hour",
      render: (_, subscription) => <WindowMeter window={subscription.limits?.five_hour} nowMs={nowMs} />,
    },
    {
      title: "Week",
      key: "weekly",
      render: (_, subscription) => <WindowMeter window={subscription.limits?.weekly} nowMs={nowMs} />,
    },
    {
      title: "Priority",
      key: "priority",
      render: (_, subscription) => (
        <CommitNumber
          value={subscription.priority}
          onCommit={(priority) =>
            priority !== null && actions.updateSettings.mutate({ id: subscription.id, change: { priority } })
          }
        />
      ),
    },
    {
      title: "Concurrency",
      key: "concurrency",
      render: (_, subscription) => (
        <CommitNumber
          value={subscription.concurrency_limit}
          min={1}
          placeholder="provider"
          onCommit={(limit) =>
            actions.updateSettings.mutate({ id: subscription.id, change: { max_concurrency: limit } })
          }
        />
      ),
    },
    {
      title: "Today (UTC)",
      key: "today",
      render: (_, subscription) => {
        const today = todayOf(stats?.subscriptions[subscription.id]);
        if (!today) {
          return <span className="text-gray-400">-</span>;
        }
        return (
          <div className="text-xs whitespace-nowrap">
            <div>{today.requests} requests</div>
            <div>
              {formatTokens(today.tokens)} tokens · {formatSpend(today.spend)}
            </div>
          </div>
        );
      },
    },
    {
      title: "",
      key: "actions",
      render: (_, subscription) => (
        <div className="flex flex-wrap gap-2">
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={actions.refreshLimits.isPending && actions.refreshLimits.variables === subscription.id}
            onClick={() => actions.refreshLimits.mutate(subscription.id)}
          >
            Limits
          </Button>
          <Button size="small" onClick={() => onReauthorize(subscription)}>
            Reauthorize
          </Button>
          <Popconfirm
            title={`Remove ${subscription.name}?`}
            description="The subscription and its statistics are deleted."
            okText="Remove"
            okButtonProps={{ danger: true }}
            onConfirm={() => actions.remove.mutate(subscription.id)}
          >
            <Button size="small" danger>
              Remove
            </Button>
          </Popconfirm>
        </div>
      ),
    },
  ];

  return (
    <Table<SubscriptionView>
      rowKey="id"
      columns={columns}
      dataSource={subscriptions}
      pagination={false}
      expandable={{
        expandedRowRender: (subscription) => <StatsPanel report={stats?.subscriptions[subscription.id]} />,
      }}
      scroll={{ x: "max-content" }}
    />
  );
}
