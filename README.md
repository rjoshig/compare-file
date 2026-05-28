# Segment File Comparator

Compare two large fixed-format segment-based data files and report matches,
mismatches, orphan keys, and duplicate keys.

The engine is a Python library with three planned entry points:

- **CLI** (Phase 1) — `python -m segment_compare ...`
- **Web UI** (Phase 3) — Vue.js 3 + Vite frontend talking to a FastAPI backend
- **Service mode** (Phase 4) — directory-watcher driven by Airflow/cron

Production scale target: **3 million records per file**. POC scope: 10K.

## File format at a glance

Every segment in the file has a 7-byte header:

```
[4-byte segment name][3-byte ASCII size][data...]
```

The size field is the **total** segment length in bytes (header + data), so
`TU4R019` means a 19-byte segment with 12 bytes of data. A record is a
sequence of segments framed by `TU4R` (key segment, first) and `ENDS`
(terminator, last), with a configurable record delimiter (default `\n`)
between records.

See `docs/architecture.md` for the full spec.

## What the tool produces

Eight output files per run, written to a run-specific output directory:

| File | Contents |
|---|---|
| `matches.dat` | Records matching in both files |
| `mismatches.dat` | Records differing on at least one segment (side-by-side) |
| `keymismatch_A.dat` | Keys only in File A |
| `keymismatch_B.dat` | Keys only in File B |
| `dups_A.dat` | Duplicate-key records pulled from File A |
| `dups_B.dat` | Duplicate-key records pulled from File B |
| `report.csv` | Per-mismatch rows |
| `summary.json` | Aggregates, timings, config hash, file metadata |

## Project status

Currently in **scaffolding / pre–Phase 1**. The engine has not been
implemented yet. See `docs/phase-plan.md` for the roadmap and
`docs/session-log.md` for the latest state.

## Setup (when Phase 1 code lands)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## CLI usage (target, when Phase 1 code lands)

```bash
python -m segment_compare \
    --file-a examples/sample_a.dat \
    --file-b examples/sample_b.dat \
    --config-dir config/ \
    --output-dir results/
```

## Repository layout

```
compare-file/
├── CLAUDE.md            # how to work on this repo
├── README.md            # you are here
├── pyproject.toml
├── .gitignore
├── config/              # JSON config: segments, normalization, runtime
├── docs/                # architecture, phase plans, decisions, session log
├── src/segment_compare/ # engine + CLI + API (Phase 3) + service (Phase 4)
├── tests/               # pytest suites
├── examples/            # small hand-crafted sample input files
└── ui/                  # Phase 3 Vue.js frontend (placeholder for now)
```

## Documentation

- `CLAUDE.md` — workflow and conventions
- `docs/architecture.md` — system design
- `docs/phase-plan.md` — phase-by-phase roadmap
- `docs/phase-1.md` … `phase-4.md` — detailed per-phase plans
- `docs/decisions.md` — architectural decision records
- `docs/session-log.md` — working journal

## License

Internal project — license TBD.
