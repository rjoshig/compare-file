# ui2 — Next.js dashboard for segment-compare

A second, more visual front-end for the segment-compare engine. It is a
**sibling** of the Vue `ui/` and talks to the **same FastAPI `/api`** — it does
not replace `ui/`, and the backend treats both identically.

What it adds over `ui/`:

- **Dashboard** — headline totals, last-run metrics, "mismatches by segment"
  and "match breakdown" charts, and a recent-runs table.
- **Field Comparator** — pick two files, see **every segment with its size**,
  with the key segment (TU4R) shown first. Each field has an **Exclude**
  checkbox that is **off by default** (everything is compared); ticking it drops
  the field. The **key field has no exclude control** (it is always included).
  Add fields to the key segment, choose the key field, then run.
- **History** — full, paginated, searchable run history (more than the Vue
  view's newest-five), with re-run and report links.
- **Config** — the saved configs, openable in the comparator.

History and the dashboard are powered by the backend's SQLite index (ADR-043);
the comparator uses the existing `POST /api/configs` + `POST /api/runs`.

## Run it

The backend must be running first:

```bash
# from the repo root
pip install -e ".[api,dev]"
python -m uvicorn segment_compare.api.main:app --reload --port 8000
```

Then this UI:

```bash
cd ui2
npm install
npm run dev            # http://localhost:3000
```

`npm run dev` serves on **:3000** and proxies `/api/*` to the backend on
**:8000** (override with `SEGCMP_API_URL`; see `.env.local.example`), so there
is no CORS to configure in development.

## Scripts

- `npm run dev` — dev server on :3000
- `npm run build` / `npm run start` — production build / serve
- `npm run typecheck` — `tsc --noEmit`

## Stack

Next.js (App Router) · TypeScript · Tailwind CSS · Recharts · next-themes ·
lucide-react. UI primitives under `components/ui/` are lightweight,
shadcn-styled, dependency-free local components.
