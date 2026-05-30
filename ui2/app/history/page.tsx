"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Search, ExternalLink, RotateCw } from "lucide-react";
import { api } from "@/lib/api";
import type { HistoryListResponse } from "@/lib/types";
import { fmtInt, fmtTime } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/misc";

const PAGE = 20;

export default function HistoryPage() {
  const router = useRouter();
  const [q, setQ] = React.useState("");
  const [offset, setOffset] = React.useState(0);
  const [data, setData] = React.useState<HistoryListResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback((query: string, off: number) => {
    setData(null);
    api
      .history({ limit: PAGE, offset: off, q: query || undefined })
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  React.useEffect(() => {
    load(q, offset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset]);

  function onSearch(e: React.FormEvent) {
    e.preventDefault();
    setOffset(0);
    load(q, 0);
  }

  return (
    <>
      <PageHeader title="History" description="Every recorded comparison run." />

      <form onSubmit={onSearch} className="mb-4 flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-8"
            placeholder="Search by file name or config…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <Button type="submit" variant="outline">
          Search
        </Button>
      </form>

      {error && (
        <Card>
          <CardContent className="p-5 text-sm text-destructive">Could not load history: {error}</CardContent>
        </Card>
      )}

      {!data && !error && <Skeleton className="h-64" />}

      {data && (
        <Card>
          <CardContent className="p-0">
            {data.runs.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">No runs match.</p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>When</TH>
                    <TH>Config</TH>
                    <TH>Files</TH>
                    <TH className="text-right">Matched</TH>
                    <TH className="text-right">Mismatched</TH>
                    <TH className="text-right">Elapsed</TH>
                    <TH className="text-right">Actions</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.runs.map((r) => (
                    <TR key={r.id}>
                      <TD className="whitespace-nowrap text-xs text-muted-foreground">{fmtTime(r.created_at)}</TD>
                      <TD>
                        <Link href={`/runs/${r.id}`} className="text-primary hover:underline">
                          {r.config_name || "—"}
                        </Link>
                      </TD>
                      <TD className="text-xs">
                        {r.file_a} ↔ {r.file_b}
                      </TD>
                      <TD className="text-right tabular-nums text-success">{fmtInt(r.records_matched)}</TD>
                      <TD className="text-right tabular-nums">
                        {r.records_mismatched > 0 ? (
                          <Badge variant="destructive">{fmtInt(r.records_mismatched)}</Badge>
                        ) : (
                          <Badge variant="success">0</Badge>
                        )}
                      </TD>
                      <TD className="text-right tabular-nums text-xs">{r.elapsed_seconds.toFixed(2)}s</TD>
                      <TD className="text-right">
                        <div className="flex items-center justify-end gap-3">
                          {r.config_name && (
                            <button
                              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                              onClick={() => router.push(`/comparator?config=${encodeURIComponent(r.config_name!)}`)}
                            >
                              <RotateCw className="h-3.5 w-3.5" /> Re-run
                            </button>
                          )}
                          {r.report_url && (
                            <a
                              href={r.report_url}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                            >
                              Report <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                          )}
                        </div>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {data && data.total > PAGE && (
        <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + PAGE >= data.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </>
  );
}
