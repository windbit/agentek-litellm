import type { StatsReport } from "../api";
import { formatDuration, formatSpend, formatTokens, STATE_LABELS, sumCounts, totalsOf } from "../utils";

interface StatsPanelProps {
  report: StatsReport | undefined;
}

const BAR_AREA_PX = 60;

export default function StatsPanel({ report }: StatsPanelProps) {
  if (!report) {
    return <span className="text-gray-400">No statistics yet</span>;
  }
  const totals = totalsOf(report);
  const failures = sumCounts(report.days, "failures");
  const switches = sumCounts(report.days, "switches");
  const states = sumCounts(report.days, "state_seconds");
  const peak = Math.max(1, ...report.days.map((day) => day.requests));

  return (
    <div className="flex flex-wrap gap-8 text-sm">
      <div>
        <div className="font-semibold mb-1">Last {report.days.length} days</div>
        <div className="text-gray-600 mb-2">
          {totals.requests} requests · {formatTokens(totals.tokens)} tokens · {formatSpend(totals.spend)}
        </div>
        <div className="flex items-end gap-px" style={{ height: BAR_AREA_PX }}>
          {report.days.map((day) => (
            <div
              key={day.day}
              title={`${day.day}: ${day.requests} requests, ${formatTokens(day.tokens)} tokens, ${formatSpend(day.spend)}`}
              className="bg-blue-400"
              style={{ width: 8, height: Math.max(2, (day.requests / peak) * BAR_AREA_PX) }}
            />
          ))}
        </div>
      </div>
      <CountList title="Failed attempts by reason" counts={failures} format={String} />
      <CountList title="Switches away by reason" counts={switches} format={String} />
      <CountList
        title="Time in state"
        counts={states}
        format={formatDuration}
        label={(state) => STATE_LABELS[state] ?? state}
      />
      <div>
        <div className="font-semibold mb-1">Top models</div>
        {report.top_models.length === 0 && <span className="text-gray-400">none</span>}
        {report.top_models.map((model) => (
          <div key={model.model}>
            {model.model}: {model.requests} requests, {formatTokens(model.tokens)}, {formatSpend(model.spend)}
          </div>
        ))}
      </div>
    </div>
  );
}

interface CountListProps {
  title: string;
  counts: Record<string, number>;
  format: (value: number) => string;
  label?: (key: string) => string;
}

function CountList({ title, counts, format, label = String }: CountListProps) {
  const entries = Object.entries(counts).filter(([, value]) => value > 0);
  return (
    <div>
      <div className="font-semibold mb-1">{title}</div>
      {entries.length === 0 && <span className="text-gray-400">none</span>}
      {entries.map(([key, value]) => (
        <div key={key}>
          {label(key)}: {format(value)}
        </div>
      ))}
    </div>
  );
}
