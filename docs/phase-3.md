# Phase 3 — Web UI (Vue.js + FastAPI)

**Goal:** non-CLI users can configure, launch, and explore comparisons
through a browser. The engine underneath is the same Phase 2 library.

## Tech stack

- **Frontend:** Vue.js 3 (Composition API), Vite, plain CSS (or Tailwind
  if it lands cleanly).
- **Backend:** FastAPI + uvicorn.
- **Persistence:** SQLite for run history.
- **Communication:** REST + JSON for control; SSE or polling for live
  progress.

## Acceptance criteria

1. All six screens functional against real engine runs.
2. FastAPI auto-docs (`/docs`) reachable and accurate.
3. SQLite run history persists across restarts.
4. Happy-path browser test (manual checklist or Playwright) passes.
5. The CLI and the API call the same `pipeline.run` function — no
   duplicated comparison logic.

## Backend layout (`src/segment_compare/api/`)

```
api/
├── __init__.py
├── main.py        # FastAPI app instance, lifespan, middleware
├── routes.py      # endpoint handlers
├── models.py      # pydantic request/response models
├── runs.py        # run launcher + SSE progress channel
└── storage.py     # SQLite run history
```

### REST endpoints

| Method | Path | Purpose |
|---|---|---|
| POST   | `/api/runs` | Create + launch a run |
| GET    | `/api/runs` | List past runs |
| GET    | `/api/runs/{id}/status` | Status snapshot |
| GET    | `/api/runs/{id}/events` | SSE stream of progress events |
| GET    | `/api/runs/{id}/result` | Summary JSON |
| GET    | `/api/runs/{id}/files` | List output files |
| GET    | `/api/runs/{id}/files/{name}` | Download output file |
| DELETE | `/api/runs/{id}` | Delete a run + its outputs |
| GET    | `/api/configs/segments` | Current segments config |
| PUT    | `/api/configs/segments` | Replace segments config |
| GET    | `/api/configs/normalization` | Current normalization config |
| PUT    | `/api/configs/normalization` | Replace normalization config |
| GET    | `/api/filesystem/browse?path=...` | Restricted server-side file picker |

### File-browser security

`/api/filesystem/browse` is restricted to a configured list of allowed
roots. Requests outside those roots return 403. No symlink traversal.

## Frontend layout (`ui/`)

```
ui/
├── package.json
├── vite.config.js
├── index.html
├── src/
│   ├── main.js
│   ├── App.vue
│   ├── router/index.js
│   ├── views/
│   │   ├── RunConfig.vue
│   │   ├── SegmentSelection.vue
│   │   ├── FieldConfig.vue
│   │   ├── RunExecution.vue
│   │   ├── ResultsDashboard.vue
│   │   └── RunHistory.vue
│   ├── components/
│   │   ├── FileBrowser.vue
│   │   ├── SegmentTable.vue
│   │   ├── MismatchTable.vue
│   │   ├── ProgressBar.vue
│   │   └── SummaryCard.vue
│   ├── services/api.js
│   └── store/index.js
└── public/
```

## Screens

### 1. Run Configuration
- File-browser-driven path selection for File A and File B.
- "Files are sorted by key" toggle (default on).
- Worker count slider (1 → CPU count).
- Hash method dropdown (blake2b / builtin).
- Output directory picker.
- **Dry-run** toggle (parses + validates, no comparison).
- "Start Comparison" button.

### 2. Segment Selection
- Checkbox list of all known segments.
- Default selection from `config/segments.json` (e.g., 20 of 30 enabled).
- "Select all" / "Deselect all" / "Reset to defaults" / search.

### 3. Field Configuration (Phase 2 feature, surfaced here)
- Expandable per-segment panel.
- Per field: logical name, length, position, "compare this field"
  checkbox.
- Byte ruler showing layout visually.
- Tabs for File A vs File B layouts when they differ.

### 4. Run Execution
- Live progress: records processed, throughput, ETA.
- Log tail.
- Cancel button (sends a signal that triggers graceful shutdown).

### 5. Results Dashboard
- Summary cards: total records, matches, mismatches, orphans, dups,
  elapsed time.
- Bar chart: mismatches by segment type.
- Download buttons for all eight output files.
- Paginated mismatch table — filter by segment, search by key.
- "Inspect record" — opens a side-by-side viewer of the bytes from
  `mismatches.dat`.

### 6. Run History
- Table of past runs from SQLite: timestamp, files, summary stats,
  duration.
- "Re-run" → pre-fills Run Configuration with that run's options.

## Bonus tools (low-risk additions)

- **Normalization rule tester.** Paste raw segment bytes + pick rules,
  see the normalized output. Useful for diagnosing why two segments
  don't match.
- **Config export/import.** Download or upload the three JSON configs
  as a single archive.
- **Sample record inspection.** Pick a key, see parsed A vs B
  side-by-side without running a full comparison.

## Concurrency model

- The FastAPI process launches comparisons in a worker pool (`asyncio`
  + `concurrent.futures.ProcessPoolExecutor` wrapping `pipeline.run`).
- Progress events are pushed to per-run `asyncio.Queue`s; the SSE
  endpoint drains them to the browser.
- Run state is persisted to SQLite on every status transition so the
  UI survives backend restarts.

## Second UI + SQLite history (added after the Vue `ui/` shipped)

Both additions keep the Vue `ui/` and every existing endpoint untouched:

- **SQLite index (ADR-043).** `api/db.py` (stdlib `sqlite3`) is dual-written on
  config-save and run-complete, realizing this phase's "SQLite for run history"
  scope as a queryable index *alongside* the ADR-041 directory-driven history
  (which remains the source of truth). New endpoints: `GET /api/dashboard`,
  `GET /api/history?limit=&offset=&q=`, `GET /api/history/{id}`. Writes are
  best-effort / non-fatal; the index is rebuildable from disk.
- **`ui2/` (ADR-044).** A second, visual front-end — Next.js + Tailwind +
  Recharts, dark/light — with a Dashboard, a **Field Comparator** (shows every
  segment + size, key segment first; per-field Exclude defaults off so
  everything is compared; the key field has no exclude control; add fields to
  the key segment), History, and Config. It consumes the same `/api`; in dev it
  proxies `/api/*` to `:8000` (no CORS). See `ui2/README.md`.

## Ordered task list

1. FastAPI scaffolding + health endpoint + `/docs` reachable.
2. SQLite schema + storage layer.
3. `POST /api/runs` invoking `pipeline.run` in a worker pool.
4. SSE progress channel.
5. Output-file download endpoints.
6. Vue scaffold via `npm create vite@latest`.
7. `services/api.js` thin wrapper around the REST API.
8. Screens in the order they appear in a user flow (1, 4, 5, 6, 2, 3).
9. End-to-end manual test against `examples/sample_*.dat`.
