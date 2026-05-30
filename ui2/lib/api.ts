// Typed client over the FastAPI backend. All calls go through Next.js rewrites
// (/api/* -> :8000), so no absolute origin / CORS is needed in the browser.

import type {
  BrowseResponse,
  DashboardResponse,
  HistoryListResponse,
  RunDetail,
  RunResponse,
  SaveConfigRequest,
  SavedConfigSummary,
  TemplateBundle,
} from "./types";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => http<{ status: string }>("/api/health"),

  templateLayouts: () => http<TemplateBundle>("/api/template-layouts"),

  saveConfig: (body: SaveConfigRequest) =>
    http<{ name: string }>("/api/configs", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listConfigs: () =>
    http<{ configs: SavedConfigSummary[] }>("/api/configs"),

  getConfig: (name: string) =>
    http<SaveConfigRequest>(`/api/configs/${encodeURIComponent(name)}`),

  run: (config_name: string, output_dir: string) =>
    http<RunResponse>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ config_name, output_dir }),
    }),

  dashboard: (recent = 5) =>
    http<DashboardResponse>(`/api/dashboard?recent=${recent}`),

  history: (params: { limit?: number; offset?: number; q?: string } = {}) => {
    const sp = new URLSearchParams();
    if (params.limit != null) sp.set("limit", String(params.limit));
    if (params.offset != null) sp.set("offset", String(params.offset));
    if (params.q) sp.set("q", params.q);
    const qs = sp.toString();
    return http<HistoryListResponse>(`/api/history${qs ? `?${qs}` : ""}`);
  },

  historyDetail: (id: number) => http<RunDetail>(`/api/history/${id}`),

  browse: (path?: string) =>
    http<BrowseResponse>(`/api/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`),

  // Fetch a sibling file (summary.json, matches.dat, ...) from a run dir token.
  runFileUrl: (reportUrl: string, name: string) =>
    reportUrl.replace(/\/report$/, `/${name}`),
};
