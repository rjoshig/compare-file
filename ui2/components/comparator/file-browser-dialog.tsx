"use client";

import * as React from "react";
import { Folder, FileText, CornerLeftUp } from "lucide-react";
import { api } from "@/lib/api";
import type { BrowseResponse } from "@/lib/types";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";

interface Props {
  open: boolean;
  onClose: () => void;
  /** When picking a file, the chosen file path is returned. */
  onPick: (path: string) => void;
  /** "file" picks a .dat/.csv/.txt; "dir" picks the current directory. */
  mode: "file" | "dir";
}

export function FileBrowserDialog({ open, onClose, onPick, mode }: Props) {
  const [data, setData] = React.useState<BrowseResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback((path?: string) => {
    setLoading(true);
    setError(null);
    api
      .browse(path)
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    if (open) load();
  }, [open, load]);

  return (
    <Dialog open={open} onClose={onClose} title={mode === "dir" ? "Choose a directory" : "Choose a file"}>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="truncate font-mono">{data?.path ?? "…"}</span>
        {loading && <Spinner />}
      </div>

      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}

      {mode === "dir" && data && (
        <div className="mt-3">
          <Button
            size="sm"
            onClick={() => {
              onPick(data.path);
              onClose();
            }}
          >
            Use this directory
          </Button>
        </div>
      )}

      <div className="mt-3 max-h-72 overflow-y-auto rounded-md border border-border">
        {data?.parent && (
          <button
            className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-accent"
            onClick={() => load(data.parent ?? undefined)}
          >
            <CornerLeftUp className="h-4 w-4" /> ..
          </button>
        )}
        {data?.dirs.map((d) => (
          <button
            key={d.path}
            className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-accent"
            onClick={() => load(d.path)}
          >
            <Folder className="h-4 w-4 text-primary" /> {d.name}
          </button>
        ))}
        {mode === "file" &&
          data?.files.map((f) => (
            <button
              key={f.path}
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-sm hover:bg-accent"
              onClick={() => {
                onPick(f.path);
                onClose();
              }}
            >
              <span className="flex items-center gap-2">
                <FileText className="h-4 w-4 text-muted-foreground" /> {f.name}
              </span>
              {typeof f.size === "number" && (
                <span className="text-xs text-muted-foreground">{f.size} B</span>
              )}
            </button>
          ))}
        {data && data.dirs.length === 0 && (mode === "dir" || data.files.length === 0) && (
          <p className="px-3 py-4 text-center text-xs text-muted-foreground">Empty directory.</p>
        )}
      </div>
    </Dialog>
  );
}
