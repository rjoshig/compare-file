"use client";

import { cn } from "@/lib/utils";
import type { TemplateField } from "@/lib/types";

interface Block {
  label: string;
  bytes: number;
  kind: "header" | "field" | "key" | "excluded";
}

/** A proportional byte-layout strip: header bytes + each field's span. */
export function ByteRuler({
  headerBytes,
  fields,
  excludes,
  keyFieldName,
}: {
  headerBytes: number;
  fields: TemplateField[];
  excludes: Record<string, boolean>;
  keyFieldName: string;
}) {
  const blocks: Block[] = [
    { label: `HEADER · ${headerBytes}B`, bytes: headerBytes, kind: "header" },
    ...fields.map((f) => ({
      label: `${f.name} · ${f.length}B`,
      bytes: f.length,
      kind:
        f.name === keyFieldName
          ? ("key" as const)
          : excludes[f.name]
            ? ("excluded" as const)
            : ("field" as const),
    })),
  ];
  const total = blocks.reduce((s, b) => s + b.bytes, 0) || 1;

  const kindClass: Record<Block["kind"], string> = {
    header: "bg-muted text-muted-foreground",
    field: "bg-primary/20 text-foreground",
    key: "bg-primary text-primary-foreground",
    excluded: "bg-destructive/15 text-muted-foreground line-through",
  };

  return (
    <div className="flex h-7 w-full overflow-hidden rounded-md border border-border text-[10px]">
      {blocks.map((b, i) => (
        <div
          key={`${b.label}-${i}`}
          className={cn("flex items-center justify-center overflow-hidden whitespace-nowrap px-1", kindClass[b.kind])}
          style={{ width: `${(b.bytes / total) * 100}%` }}
          title={b.label}
        >
          {b.label}
        </div>
      ))}
    </div>
  );
}
