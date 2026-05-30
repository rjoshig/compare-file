import { CheckCircle2, ExternalLink } from "lucide-react";
import type { RunResponse } from "@/lib/types";
import { fmtInt } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export function RunResult({ result }: { result: RunResponse }) {
  const metrics: { label: string; value: number; tone?: "success" | "destructive" }[] = [
    { label: "Matched", value: result.records_matched, tone: "success" },
    { label: "Mismatched", value: result.records_mismatched, tone: "destructive" },
    { label: "A-only keys", value: result.keys_in_a_only },
    { label: "B-only keys", value: result.keys_in_b_only },
    { label: "Dups in A", value: result.dups_in_a },
    { label: "Dups in B", value: result.dups_in_b },
  ];
  return (
    <Card className="mb-4 border-success/50">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2 text-success">
          <CheckCircle2 className="h-4 w-4" /> Comparison complete
        </CardTitle>
        <a
          href={result.report_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
        >
          Open full report <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </CardHeader>
      <CardContent className="grid grid-cols-3 gap-4 sm:grid-cols-6">
        {metrics.map((m) => (
          <div key={m.label}>
            <div className="text-xs text-muted-foreground">{m.label}</div>
            <div className="text-lg font-semibold tabular-nums">
              {m.value > 0 && m.tone ? (
                <Badge variant={m.tone}>{fmtInt(m.value)}</Badge>
              ) : (
                fmtInt(m.value)
              )}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
