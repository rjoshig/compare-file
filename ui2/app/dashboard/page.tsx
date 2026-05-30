"use client";

import * as React from "react";
import Link from "next/link";
import { CheckCircle2, XCircle, Layers, Copy, Gauge } from "lucide-react";
import { api } from "@/lib/api";
import type { DashboardResponse } from "@/lib/types";
import { fmtInt, fmtTime } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatCard } from "@/components/dashboard/stat-card";
import { MatchBreakdown, MismatchBySegment } from "@/components/dashboard/charts";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/misc";

export default function DashboardPage() {
  const [data, setData] = React.useState<DashboardResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    api
      .dashboard(8)
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  if (error) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <Card>
          <CardContent className="p-5 text-sm text-destructive">
            Could not load dashboard: {error}. Is the API running on :8000?
          </CardContent>
        </Card>
      </>
    );
  }

  if (!data) {
    return (
      <>
        <PageHeader title="Dashboard" description="Overview of comparison runs." />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      </>
    );
  }

  const { totals, last_run, recent_runs, mismatches_by_segment } = data;

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Aggregated metrics across all comparison runs."
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total runs" value={fmtInt(totals.total_runs)} icon={<Layers className="h-4 w-4 text-muted-foreground" />} />
        <StatCard label="Records matched" value={fmtInt(totals.total_matched)} tone="success" icon={<CheckCircle2 className="h-4 w-4 text-success" />} />
        <StatCard label="Records mismatched" value={fmtInt(totals.total_mismatched)} tone="destructive" icon={<XCircle className="h-4 w-4 text-destructive" />} />
        <StatCard label="Orphans + dups" value={fmtInt(totals.total_orphans + totals.total_dups)} tone="muted" icon={<Copy className="h-4 w-4 text-muted-foreground" />} />
      </div>

      {last_run && (
        <div className="mt-4">
          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Gauge className="h-4 w-4 text-primary" /> Last run
              </CardTitle>
              <span className="text-xs text-muted-foreground">
                {fmtTime(last_run.created_at)} · {last_run.config_name ?? "—"}
              </span>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3 lg:grid-cols-6">
              <Metric label="Matched" value={last_run.records_matched} tone="success" />
              <Metric label="Mismatched" value={last_run.records_mismatched} tone="destructive" />
              <Metric label="A-only keys" value={last_run.keys_in_a_only} />
              <Metric label="B-only keys" value={last_run.keys_in_b_only} />
              <Metric label="Dups A/B" value={last_run.dups_in_a + last_run.dups_in_b} />
              <Metric label="Throughput" value={Math.round(last_run.throughput_rps)} suffix=" rec/s" />
            </CardContent>
          </Card>
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Mismatches by segment</CardTitle>
          </CardHeader>
          <CardContent>
            <MismatchBySegment data={mismatches_by_segment} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Match breakdown (all runs)</CardTitle>
          </CardHeader>
          <CardContent>
            <MatchBreakdown matched={totals.total_matched} mismatched={totals.total_mismatched} />
          </CardContent>
        </Card>
      </div>

      <div className="mt-4">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Recent runs</CardTitle>
            <Link href="/history" className="text-xs text-primary hover:underline">
              View all →
            </Link>
          </CardHeader>
          <CardContent className="pt-0">
            {recent_runs.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No runs yet. Start one in the Field Comparator.
              </p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>When</TH>
                    <TH>Config</TH>
                    <TH>Files</TH>
                    <TH className="text-right">Matched</TH>
                    <TH className="text-right">Mismatched</TH>
                    <TH></TH>
                  </TR>
                </THead>
                <TBody>
                  {recent_runs.map((r) => (
                    <TR key={r.id}>
                      <TD className="whitespace-nowrap text-xs text-muted-foreground">{fmtTime(r.created_at)}</TD>
                      <TD>{r.config_name || "—"}</TD>
                      <TD className="text-xs">{r.file_a} ↔ {r.file_b}</TD>
                      <TD className="text-right tabular-nums text-success">{fmtInt(r.records_matched)}</TD>
                      <TD className="text-right tabular-nums">
                        {r.records_mismatched > 0 ? (
                          <Badge variant="destructive">{fmtInt(r.records_mismatched)}</Badge>
                        ) : (
                          <Badge variant="success">0</Badge>
                        )}
                      </TD>
                      <TD className="text-right">
                        <Link href={`/runs/${r.id}`} className="text-xs text-primary hover:underline">
                          Details
                        </Link>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function Metric({
  label,
  value,
  tone,
  suffix,
}: {
  label: string;
  value: number;
  tone?: "success" | "destructive";
  suffix?: string;
}) {
  const toneClass = tone === "success" ? "text-success" : tone === "destructive" ? "text-destructive" : "";
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${toneClass}`}>
        {fmtInt(value)}
        {suffix}
      </div>
    </div>
  );
}
