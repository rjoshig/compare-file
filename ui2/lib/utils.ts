import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind class names, resolving conflicts (shadcn convention). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Compact integer formatting, e.g. 12_345 -> "12,345". */
export function fmtInt(n: number | null | undefined): string {
  return typeof n === "number" ? n.toLocaleString("en-US") : "0";
}

/** Render an ISO timestamp as a short local string, or "—" if absent. */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
