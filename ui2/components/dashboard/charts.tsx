"use client";

import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { SegmentMismatch } from "@/lib/types";

const TOOLTIP_STYLE = {
  background: "hsl(var(--card))",
  border: "1px solid hsl(var(--border))",
  borderRadius: 8,
  fontSize: 12,
  color: "hsl(var(--foreground))",
};

export function MismatchBySegment({ data }: { data: SegmentMismatch[] }) {
  if (data.length === 0) {
    return <Empty label="No mismatches recorded yet." />;
  }
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
        <XAxis
          dataKey="segment_name"
          tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
        />
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "hsl(var(--accent))" }} />
        <Bar dataKey="mismatch_count" name="Mismatches" radius={[4, 4, 0, 0]} fill="hsl(var(--destructive))" />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function MatchBreakdown({ matched, mismatched }: { matched: number; mismatched: number }) {
  const data = [
    { name: "Matched", value: matched, color: "hsl(var(--success))" },
    { name: "Mismatched", value: mismatched, color: "hsl(var(--destructive))" },
  ];
  if (matched + mismatched === 0) {
    return <Empty label="No records compared yet." />;
  }
  return (
    <ResponsiveContainer width="100%" height={260}>
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          innerRadius={60}
          outerRadius={95}
          paddingAngle={2}
        >
          {data.map((d) => (
            <Cell key={d.name} fill={d.color} />
          ))}
        </Pie>
        <Tooltip contentStyle={TOOLTIP_STYLE} />
      </PieChart>
    </ResponsiveContainer>
  );
}

function Empty({ label }: { label: string }) {
  return (
    <div className="flex h-[260px] items-center justify-center text-sm text-muted-foreground">
      {label}
    </div>
  );
}
