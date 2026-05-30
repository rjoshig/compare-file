"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { RotateCw, FileCog } from "lucide-react";
import { api } from "@/lib/api";
import type { SavedConfigSummary } from "@/lib/types";
import { fmtTime } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/misc";

export default function ConfigPage() {
  const router = useRouter();
  const [configs, setConfigs] = React.useState<SavedConfigSummary[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    api
      .listConfigs()
      .then((r) => setConfigs(r.configs))
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  return (
    <>
      <PageHeader
        title="Config"
        description="Saved comparison configs. Open one in the Field Comparator to run it again."
        action={
          <Button onClick={() => router.push("/comparator")}>
            <FileCog className="h-4 w-4" /> New comparison
          </Button>
        }
      />

      {error && (
        <Card>
          <CardContent className="p-5 text-sm text-destructive">Could not load configs: {error}</CardContent>
        </Card>
      )}

      {!configs && !error && <Skeleton className="h-48" />}

      {configs && (
        <Card>
          <CardContent className="p-0">
            {configs.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                No saved configs yet. Create one in the Field Comparator.
              </p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Name</TH>
                    <TH>File A</TH>
                    <TH>File B</TH>
                    <TH>Created</TH>
                    <TH className="text-right">Actions</TH>
                  </TR>
                </THead>
                <TBody>
                  {configs.map((c) => (
                    <TR key={c.name}>
                      <TD className="font-medium">{c.name}</TD>
                      <TD className="font-mono text-xs">{c.file_a_path}</TD>
                      <TD className="font-mono text-xs">{c.file_b_path}</TD>
                      <TD className="whitespace-nowrap text-xs text-muted-foreground">{fmtTime(c.created_at)}</TD>
                      <TD className="text-right">
                        <button
                          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                          onClick={() => router.push(`/comparator?config=${encodeURIComponent(c.name)}`)}
                        >
                          <RotateCw className="h-3.5 w-3.5" /> Open
                        </button>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </>
  );
}
