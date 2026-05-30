"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { FolderOpen, Play, Save } from "lucide-react";
import { api } from "@/lib/api";
import type { RunResponse, TemplateBundle } from "@/lib/types";
import { buildSideConfig, defaultSideState, sideConfigToState, type SideEditorState } from "@/lib/editor";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton, Spinner } from "@/components/ui/misc";
import { SegmentFieldEditor } from "@/components/comparator/segment-field-editor";
import { FileBrowserDialog } from "@/components/comparator/file-browser-dialog";
import { RunResult } from "@/components/comparator/run-result";

type BrowseTarget = { mode: "file" | "dir"; apply: (path: string) => void } | null;

export default function ComparatorPage() {
  return (
    <React.Suspense fallback={<Skeleton className="h-64" />}>
      <ComparatorInner />
    </React.Suspense>
  );
}

function ComparatorInner() {
  const params = useSearchParams();
  const [tpl, setTpl] = React.useState<TemplateBundle | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const [stateA, setStateA] = React.useState<SideEditorState | null>(null);
  const [stateB, setStateB] = React.useState<SideEditorState | null>(null);
  const [tab, setTab] = React.useState<"A" | "B">("A");

  const [configName, setConfigName] = React.useState("");
  const [outputDir, setOutputDir] = React.useState("");
  const [browse, setBrowse] = React.useState<BrowseTarget>(null);

  const [running, setRunning] = React.useState(false);
  const [runError, setRunError] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<RunResponse | null>(null);

  // Load templates, then — if arriving via Config "Open" / History "Re-run"
  // (?config=NAME) — repopulate both sides from the saved config. Falls back to
  // empty defaults if the config can't be loaded.
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const t = await api.templateLayouts();
        if (cancelled) return;
        setTpl(t);

        const name = params.get("config");
        if (name) {
          setConfigName(name);
          try {
            const saved = await api.getConfig(name);
            if (cancelled) return;
            setStateA(sideConfigToState(saved.file_a));
            setStateB(sideConfigToState(saved.file_b));
            return;
          } catch {
            /* fall through to defaults if the saved config can't be read */
          }
        }
        if (cancelled) return;
        setStateA(defaultSideState(t.layout_a));
        setStateB(defaultSideState(t.layout_b));
      } catch (e) {
        if (!cancelled) setError(String((e as Error).message ?? e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [params]);

  const canRun =
    !!stateA?.filePath &&
    !!stateB?.filePath &&
    !!stateA?.keyFieldName &&
    !!stateB?.keyFieldName &&
    !!outputDir &&
    !running;

  async function handleRun() {
    if (!tpl || !stateA || !stateB) return;
    setRunning(true);
    setRunError(null);
    setResult(null);
    try {
      const body = {
        name: configName.trim() || null,
        file_a: buildSideConfig(tpl.layout_a, stateA),
        file_b: buildSideConfig(tpl.layout_b, stateB),
      };
      const { name } = await api.saveConfig(body);
      const run = await api.run(name, outputDir);
      setResult(run);
    } catch (e) {
      setRunError(String((e as Error).message ?? e));
    } finally {
      setRunning(false);
    }
  }

  if (error) {
    return (
      <>
        <PageHeader title="Field Comparator" />
        <Card>
          <CardContent className="p-5 text-sm text-destructive">
            Could not load template layouts: {error}. Is the API running on :8000?
          </CardContent>
        </Card>
      </>
    );
  }

  if (!tpl || !stateA || !stateB) {
    return (
      <>
        <PageHeader title="Field Comparator" description="Configure and run a comparison." />
        <Skeleton className="h-64" />
      </>
    );
  }

  const side = tab === "A" ? stateA : stateB;
  const setSide = tab === "A" ? setStateA : setStateB;
  const layout = tab === "A" ? tpl.layout_a : tpl.layout_b;

  return (
    <>
      <PageHeader
        title="Field Comparator"
        description="Pick two files, choose which fields to compare, and run."
        action={
          <Button onClick={handleRun} disabled={!canRun}>
            {running ? <Spinner /> : <Play className="h-4 w-4" />}
            {running ? "Running…" : "Run comparison"}
          </Button>
        }
      />

      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Save className="h-4 w-4 text-primary" /> Run options
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <FilePicker
            label="File A"
            value={stateA.filePath}
            onChange={(p) => setStateA({ ...stateA, filePath: p })}
            onBrowse={() => setBrowse({ mode: "file", apply: (p) => setStateA({ ...stateA, filePath: p }) })}
          />
          <FilePicker
            label="File B"
            value={stateB.filePath}
            onChange={(p) => setStateB({ ...stateB, filePath: p })}
            onBrowse={() => setBrowse({ mode: "file", apply: (p) => setStateB({ ...stateB, filePath: p }) })}
          />
          <FilePicker
            label="Output directory"
            value={outputDir}
            onChange={setOutputDir}
            onBrowse={() => setBrowse({ mode: "dir", apply: setOutputDir })}
          />
          <div className="space-y-1.5">
            <Label>Config name (optional)</Label>
            <Input
              placeholder="unsaved if blank"
              value={configName}
              onChange={(e) => setConfigName(e.target.value)}
            />
          </div>
        </CardContent>
      </Card>

      {runError && (
        <Card className="mb-4">
          <CardContent className="p-4 text-sm text-destructive">Run failed: {runError}</CardContent>
        </Card>
      )}

      {result && <RunResult result={result} />}

      <div className="mb-3 mt-4 flex items-center gap-2">
        <TabButton active={tab === "A"} onClick={() => setTab("A")}>
          File A layout
        </TabButton>
        <TabButton active={tab === "B"} onClick={() => setTab("B")}>
          File B layout
        </TabButton>
        <label className="ml-auto flex items-center gap-2 text-sm text-muted-foreground">
          <Checkbox
            checked={side.inputSorted}
            onCheckedChange={(v) => setSide({ ...side, inputSorted: v })}
            aria-label="Input already sorted"
          />
          File {tab} already sorted by key
        </label>
      </div>

      <SegmentFieldEditor layout={layout} state={side} onChange={setSide} />

      <FileBrowserDialog
        open={browse !== null}
        mode={browse?.mode ?? "file"}
        onClose={() => setBrowse(null)}
        onPick={(p) => browse?.apply(p)}
      />
    </>
  );
}

function FilePicker({
  label,
  value,
  onChange,
  onBrowse,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  onBrowse: () => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <div className="flex gap-2">
        <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder="/path/to/file" />
        <Button variant="outline" size="icon" onClick={onBrowse} aria-label={`Browse ${label}`}>
          <FolderOpen className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "rounded-md px-3 py-1.5 text-sm font-medium transition-colors " +
        (active ? "bg-primary text-primary-foreground" : "bg-secondary text-secondary-foreground hover:opacity-80")
      }
    >
      {children}
    </button>
  );
}
