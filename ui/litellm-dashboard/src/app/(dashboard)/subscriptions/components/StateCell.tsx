import { Tag } from "antd";
import type { StateView } from "../api";
import { formatRemaining, reasonLabel, STATE_COLORS, STATE_LABELS } from "../utils";

interface StateCellProps {
  state: StateView;
  nowMs: number;
}

export default function StateCell({ state, nowMs }: StateCellProps) {
  const remaining = formatRemaining(state.until, nowMs);
  const reason = reasonLabel(state.reason);
  return (
    <div style={{ minWidth: 150, maxWidth: 260 }}>
      <Tag color={STATE_COLORS[state.state] ?? "default"}>{STATE_LABELS[state.state] ?? state.state}</Tag>
      {remaining && <div className="text-xs text-gray-500">returns in {remaining}</div>}
      {reason && <div className="text-xs text-gray-400">{reason}</div>}
    </div>
  );
}
