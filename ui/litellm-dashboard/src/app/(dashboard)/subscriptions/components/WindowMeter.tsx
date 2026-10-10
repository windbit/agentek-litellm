import { Progress } from "antd";
import type { WindowView } from "../api";
import { formatRemaining, meterColor } from "../utils";

interface WindowMeterProps {
  window: WindowView | null | undefined;
  nowMs: number;
}

export default function WindowMeter({ window, nowMs }: WindowMeterProps) {
  if (!window) {
    return <span className="text-gray-400">no data</span>;
  }
  const reset = formatRemaining(window.reset_at, nowMs);
  return (
    <div style={{ minWidth: 120 }}>
      <Progress
        percent={Math.min(window.used_percent, 100)}
        showInfo={false}
        size="small"
        strokeColor={meterColor(window.used_percent)}
      />
      <div className="flex justify-between gap-2 text-xs text-gray-500 whitespace-nowrap">
        <span>{Math.round(window.used_percent)}%</span>
        <span>{reset ? `resets in ${reset}` : ""}</span>
      </div>
    </div>
  );
}
