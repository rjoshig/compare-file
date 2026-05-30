# Phase Plan

This is the master overview. Each phase has its own detailed plan linked
below. Phases are sequential — do not start phase N+1 until phase N's
acceptance criteria are met.

| Phase | Title | Status | Doc |
|---|---|---|---|
| 0 | Scaffolding | **complete** | (this commit) |
| 1 | Core engine (POC) | **complete** | [docs/phase-1.md](phase-1.md) |
| 2 | Production scale + field-level config | **complete** | [docs/phase-2.md](phase-2.md) |
| 3 | Web UI (Vue.js + FastAPI; + Next.js `ui2/` + SQLite history) | in progress | [docs/phase-3.md](phase-3.md) |
| 4 | Scheduled service mode | not started | [docs/phase-4.md](phase-4.md) |
| 5 | Parallelism & throughput efficiency | planned | [docs/phase-5.md](phase-5.md) |
| 6 | _(reserved — unallocated)_ | — | — |
| 7 | Multi-user hosting & authentication | planned | [docs/phase-7.md](phase-7.md) |

## Phase 0 — Scaffolding (this commit)

Project structure, documentation, configs, sample data, tooling. No
application code.

**Exit criteria:**
- All files in target repo layout present.
- All three config JSON files parse cleanly.
- `examples/sample_a.dat` and `examples/sample_b.dat` exist with the byte
  sizes documented in `examples/README.md`.
- `pytest` collects 0 tests and exits 0.
- Branch pushed.

## Phase 1 — Core engine (POC)

**Goal:** prove the comparison technique end-to-end on synthetic data
at 10K-record scale.

**Scope:**
- Streaming parser, record assembler.
- Position-based normalizer.
- Hasher (blake2b default + builtin switchable).
- Multiset comparator.
- Writer for all eight output files (matches, mismatches,
  keymismatch_A/B, dups_A/B, report.csv, summary.json).
- CLI entry point.
- Config loading + validation + run audit hash.
- Synthetic data generator.
- Unit tests per module + one integration test against
  `examples/sample_*.dat`.

**Out of scope:** parallelism, field-level config, performance tuning,
UI, service mode.

**Exit criteria:**
- `python -m segment_compare --file-a examples/sample_a.dat
  --file-b examples/sample_b.dat --config-dir config/ --output-dir results/`
  produces all eight outputs with the counts predicted by
  `examples/README.md`.
- All unit tests pass.
- `black`, `flake8`, `mypy --strict` clean.

See [docs/phase-1.md](phase-1.md).

## Phase 2 — Production scale + field-level config

**Goal:** handle 3M-record files efficiently and support real-world
layout differences via field-based normalization config.

**Scope:**
- Equal-count key partitioning across worker processes.
- Per-worker output files + merge step.
- Optional external sort if `input_sorted = false`.
- Field-level normalization config alongside Phase 1 position-based.
- Benchmark report.

**Exit criteria:**
- A 3M-record synthetic comparison completes within an agreed throughput
  target (TBD before Phase 2 kickoff).
- Field-level config produces identical results to equivalent
  position-based config on the same inputs.

See [docs/phase-2.md](phase-2.md).

## Phase 3 — Web UI

**Goal:** non-CLI users can configure, run, and explore comparisons via
a browser.

**Scope:**
- Vue.js 3 SPA in `ui/` (shipped).
- FastAPI backend in `src/segment_compare/api/`.
- Six screens: Run Configuration, Segment Selection, Field Configuration,
  Run Execution, Results Dashboard, Run History.
- SQLite for run history (ADR-043) — realized as a dual-written index
  alongside the ADR-041 directory-driven history; powers the `ui2/` dashboard.
- A second, visual UI in `ui2/` (Next.js + Tailwind + Recharts, ADR-044):
  Dashboard, Field Comparator, History, Config.
- Dry-run mode, sample record inspection, normalization rule tester.

**Exit criteria:**
- All six screens functional against a real engine run.
- API documented (FastAPI auto-doc).
- Browser test (manual or Playwright) of the happy path.

See [docs/phase-3.md](phase-3.md).

## Phase 4 — Scheduled service mode

**Goal:** Airflow/cron can trigger comparisons by dropping JSON config
files in a watched directory.

**Scope:**
- Directory-watcher entry point (one invocation = one scan).
- Lock file, pending/processing/archive flow.
- mailx email integration.
- Standardized exit codes.
- UI integration: "Generate Service Config" tab.

**Exit criteria:**
- End-to-end submission via JSON config file produces output + email.
- Stale-config alerting works.
- Exit codes match the published table.

See [docs/phase-4.md](phase-4.md).

## Phase 5 — Parallelism & throughput efficiency

**Goal:** make the existing parallel engine *efficient* at the 3M-record
target. Phase 2 shipped working parallelism but measured only 1.84× at 4
workers; Phase 5 closes that gap (profiling, size-aware partitioning /
work-stealing, lower IPC/serialization overhead, memory-mapped reads, tuned
defaults) without changing output semantics.

**Scope:** profile the pipeline at scale; improve load balancing and per-worker
overhead; tune `parallel_workers` / chunk size; optional streaming merge.

**Exit criteria:**
- A reproducible 3M-record benchmark (extends `docs/benchmarks/phase-2.md`).
- Measurable speedup over the Phase-2 baseline; output byte-identical at every
  worker count; toolchain clean; an ADR records the approach.

See [docs/phase-5.md](phase-5.md).

## Phase 6 — reserved

Intentionally unallocated. Phase 7 was numbered per operator request, leaving
Phase 6 as a slot for future work that sequences before multi-user hosting.

## Phase 7 — Multi-user hosting & authentication

**Goal:** make the tool safely usable by multiple concurrent users on one Linux
host — per-user login, a single admin-only page to create users and issue/reset
passwords, forced password change on first login, and per-user isolation of saved
configs and run history. No RBAC beyond user/admin.

**Scope:**
- Cookie-based server sessions + bcrypt; `users` / `sessions` tables in the
  SQLite index; auth guard on all `/api/*` (ADR-045).
- Admin-only user management endpoints + a single `ui2` `/admin` page;
  env-seeded bootstrap admin.
- Forced first-login password change (`must_change_password`).
- Per-user isolation: namespace `user_configs/<username>/`, add `user_id` to the
  `runs`/`configs` tables, filter every read by the logged-in user.

**Out of scope:** file upload / filesystem sandboxing (typed server paths kept —
trusted-user model), roles beyond user/admin, SSO/LDAP/OAuth, self-service email
reset, auth for the Vue `ui/`.

**Exit criteria:**
- Unauthenticated requests 401; login/logout manage a session cookie; admin can
  create/reset users; first login forces a password change; the admin page is
  admin-only; each user sees only their own configs + history; bcrypt hashes +
  SQLite sessions + `httpOnly`/`Secure` cookies; tests + toolchain clean; an ADR
  records the approach.

See [docs/phase-7.md](phase-7.md).
