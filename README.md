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

## Setup

Requires **Python 3.12+**. The project standardizes on **pyenv** with
`3.12.7` pinned locally:

```bash
pyenv install 3.12.7      # one-time, if not already installed
pyenv local 3.12.7        # writes .python-version

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Verify the interpreter once activated:

```bash
python --version          # Python 3.12.7
```

## CLI usage

```bash
python -m segment_compare \
    --file-a examples/sample_a.dat \
    --file-b examples/sample_b.dat \
    --config-dir config/ \
    --output-dir results/
```

Output files are timestamped `<base>_YYYYMMDDHHMM.<ext>` (UTC) so
successive runs don't clobber each other — see **ADR-027**.

### Parallelism is configurable

The Phase 2 parallel pipeline is on by default. The worker count
is read from `config/runtime.json::parallel_workers` (stock default:
**8**). To override per-invocation:

```bash
# Force single-process (Phase 1 path)
python -m segment_compare --workers 1 ...

# Use 4 workers for this run, ignoring the config default
python -m segment_compare --workers 4 ...
```

Order of precedence: **CLI flag > config file**. See **ADR-028** for
why 8 is the default and **`docs/benchmarks/phase-2.md`** for the
measured speedup curve.

### CLI command reference

Every option, with a one-line description and a copy-pastable example.

#### `--file-a` / `--file-b` (required for runs)

Paths to the two input files. Required for every mode except
`--validate-config` and the `--version` / `--help` exits.

```bash
python -m segment_compare \
    --file-a /data/today/source_a.dat \
    --file-b /data/today/source_b.dat \
    --config-dir config/ \
    --output-dir results/
```

#### `--config-dir` (required)

Directory containing `segments.json`, `normalization.json`, and
`runtime.json`. Validated at startup; bad config exits 10.

```bash
# Stock config
python -m segment_compare --config-dir config/ ...

# Custom config (e.g., a different normalization profile)
python -m segment_compare --config-dir /etc/segment-compare/strict/ ...
```

#### `--output-dir` (required for runs)

Directory the eight output files land in. Created if missing.
Filenames are stamped `<base>_YYYYMMDDHHMM.<ext>` (UTC) so re-runs
sit beside each other (ADR-027).

```bash
python -m segment_compare ... --output-dir /var/log/segcmp/$(date +%Y%m%d)/
```

#### `--workers N`

Number of worker processes. **Default reads `runtime.json::parallel_workers`**
(stock config: 8). The CLI flag overrides the config (ADR-028).

```bash
# Use the config default (8 workers in stock config)
python -m segment_compare ...

# Force the single-process Phase 1 code path (deterministic, no IPC)
python -m segment_compare --workers 1 ...

# Tune for your hardware
python -m segment_compare --workers 4 ...   # 4-core box
python -m segment_compare --workers 16 ...  # 16-core production server
```

Output is byte-identical across worker counts (`tests/test_parallel.py`
pins this) so you can tune workers without worrying about
correctness drift.

#### `--external-sort`

Force a chunk-and-merge sort of both inputs before the
comparison. By default the engine trusts `runtime.json::input_sorted`
(stock: `true`). Pass this flag when input sort order is unknown
or you've just received unsorted output from an upstream system.

```bash
# Inputs may not be sorted by key
python -m segment_compare --external-sort \
    --file-a /unsorted/a.dat --file-b /unsorted/b.dat \
    --config-dir config/ --output-dir results/
```

Sorted intermediates land in `runtime.json::sort_temp_dir`
(stock: `/tmp/segment_compare`) as `sorted_a_<stamp>.dat` /
`sorted_b_<stamp>.dat`. The `summary.json` records the original
input paths, not these temp files (ADR-030).

Alternatively, flip `runtime.json::input_sorted` to `false`
permanently and the engine will sort on every run without a flag.

#### `--dry-run`

Parse and validate both input files without producing any output
files. Reports record counts and duplicate-key counts so an
operator can sanity-check inputs before paying for a full
comparison.

```bash
python -m segment_compare --dry-run \
    --file-a /data/today/source_a.dat \
    --file-b /data/today/source_b.dat \
    --config-dir config/ \
    --output-dir /tmp/unused/

# Output:
#   dry-run OK: A=2955017 records (17888 dup occurrences), B=2954868 records (17908 dup occurrences)
```

Exit 0 on success, 11 on missing input, 20 on parse error, 10 on
bad config. The `--output-dir` argument is still required by
argparse but is never touched.

#### `--validate-config`

Load and validate the three config files without touching the
inputs. Useful in CI / deploy pipelines to catch config drift
before scheduling a real run.

```bash
python -m segment_compare --validate-config --config-dir config/

# Output:
#   config OK (audit hash: cc28308289c70addf18152208c4a4531a9a0bff85e7c54179dfbb5e61a58a8b6)
```

Exit 0 on valid config, 10 on any `ConfigError`. The audit hash
printed matches `summary.json::config_audit_hash` for any run that
later uses this config (ADR-017).

#### `--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}`

Logging verbosity (default `INFO`). Logs go to stderr.

```bash
# Quieter: only warnings and errors
python -m segment_compare --log-level WARNING ...

# Noisier: see the per-stage timings and per-worker progress
python -m segment_compare --log-level DEBUG ...
```

#### `--version`

Print the engine version and exit. Useful for embedding in run
metadata or for confirming which version is installed.

```bash
python -m segment_compare --version
# segment-compare 0.0.1
```

### Exit codes

```
0   success, no mismatches
1   success, mismatches found
2   completed with warnings (orphan keys or duplicate keys present)
10  config validation error
11  input file not found
12  output write error
20  parse error (corrupt input)
30  unexpected runtime error
```

Exit-code priority when multiple conditions hold: mismatches (1)
outranks orphans/dups (2). A successful run with no anomalies
returns 0.

### Common usage patterns

```bash
# 1. Smoke-test the engine against the committed fixture
python -m segment_compare \
    --file-a examples/sample_a.dat \
    --file-b examples/sample_b.dat \
    --config-dir config/ \
    --output-dir results/

# 2. Daily reconciliation, parallel, pre-sorted inputs
python -m segment_compare \
    --file-a /data/$(date +%Y%m%d)/extract_a.dat \
    --file-b /data/$(date +%Y%m%d)/extract_b.dat \
    --config-dir /etc/segment-compare/prod/ \
    --output-dir /var/log/segcmp/$(date +%Y%m%d)/ \
    --workers 8

# 3. Validate a new config before promoting to prod
python -m segment_compare --validate-config --config-dir /etc/segment-compare/new/

# 4. Quick check on incoming files (parse + count, no comparison)
python -m segment_compare --dry-run \
    --file-a /staging/incoming_a.dat \
    --file-b /staging/incoming_b.dat \
    --config-dir config/ \
    --output-dir /tmp/unused/

# 5. One-off comparison on unsorted ad-hoc files
python -m segment_compare --external-sort \
    --file-a /tmp/ad_hoc_a.dat \
    --file-b /tmp/ad_hoc_b.dat \
    --config-dir config/ \
    --output-dir results/

# 6. Deterministic single-process run (e.g., for diffing summary.json
#    fields where worker timing variance would mask real changes)
python -m segment_compare --workers 1 \
    --file-a A --file-b B --config-dir config/ --output-dir results/

# 7. Verbose debug run (parsing problem, suspected corruption)
python -m segment_compare --log-level DEBUG \
    --file-a A --file-b B --config-dir config/ --output-dir results/ 2> debug.log
```

For the full step-by-step explanation of what happens between
`python -m segment_compare ...` and the eight output files, see
**[docs/how-it-works.md](docs/how-it-works.md)**.

## Repository layout

High-level view of every directory and every file that ships with the
repo (generated artifacts like `.venv/`, `results*/`, `__pycache__/`,
`tests/fixtures/synth_*` benchmark data, and `*.egg-info/` are excluded):

```
compare-file/
├── CLAUDE.md                              # how to work on this repo
├── README.md                              # you are here
├── pyproject.toml                         # build + tool config (black, mypy, flake8 sections)
├── .flake8                                # flake8 settings
├── .gitignore
├── .python-version                        # pyenv pin (3.12.7)
├── config/
│   ├── segments.json                      # segment catalog + parser knobs
│   ├── normalization.json                 # per-segment strip/exclude rules
│   ├── runtime.json                       # hash method, workers, sort settings
│   └── segments.example-rdw.json          # example: per-file RDW prefix schema
├── docs/
│   ├── architecture.md
│   ├── decisions.md                       # ADRs (append-only)
│   ├── how-it-works.md                    # engine walkthrough + reliability math
│   ├── phase-plan.md
│   ├── phase-1.md
│   ├── phase-2.md
│   ├── phase-3.md
│   ├── phase-4.md
│   ├── session-log.md                     # working journal (read first / write last)
│   └── benchmarks/
│       └── phase-2.md                     # measured 3M-record numbers
├── src/
│   └── segment_compare/
│       ├── __init__.py
│       ├── __main__.py                    # CLI entry point
│       ├── parser.py                      # streaming segment + record parser
│       ├── config.py                      # JSON config loader + validator
│       ├── normalizer.py                  # position + composite dispatch
│       ├── hasher.py                      # blake2b + builtin behind a Protocol
│       ├── comparator.py                  # per-record multiset hash compare
│       ├── writer.py                      # eight output files + Summary
│       ├── pipeline.py                    # run() / run_parallel() / dry_run()
│       ├── partitioner.py                 # equal-count key partitioning
│       ├── worker.py                      # subprocess entry point
│       ├── merger.py                      # fold per-worker outputs
│       ├── external_sort.py               # chunk-and-merge sort
│       ├── py.typed                       # PEP-561 marker
│       └── api/
│           └── __init__.py                # Phase 3 FastAPI (placeholder)
├── tests/
│   ├── __init__.py
│   ├── synthetic_data.py                  # generate_pair(num_records, seed, ...)
│   ├── test_parser.py
│   ├── test_config.py
│   ├── test_normalizer.py
│   ├── test_hasher.py
│   ├── test_comparator.py
│   ├── test_writer.py
│   ├── test_pipeline.py
│   ├── test_partitioner.py
│   ├── test_merger.py
│   ├── test_external_sort.py
│   ├── test_field_normalizer.py
│   ├── test_field_config.py
│   ├── test_field_integration.py
│   ├── test_parallel.py
│   ├── test_main.py
│   └── test_synthetic_data.py
├── examples/
│   ├── README.md                          # sample-file format reference
│   ├── sample_a.dat
│   └── sample_b.dat
└── ui/
    └── README.md                          # Phase 3 placeholder
```

### Bootstrap a fresh checkout

If you're recreating this skeleton from scratch (or auditing that a
clone has every expected file), one of the two snippets below scaffolds
the entire tree with empty files. **Run it inside an empty directory
named `compare-file/`.** It only creates files that are missing — it
does not overwrite anything.

**Linux / macOS (bash or zsh):**

```bash
mkdir -p config docs/benchmarks src/segment_compare/api tests examples ui

# Top-level files
touch CLAUDE.md README.md pyproject.toml .flake8 .gitignore .python-version

# config/
touch config/segments.json config/normalization.json config/runtime.json \
      config/segments.example-rdw.json

# docs/
touch docs/architecture.md docs/decisions.md docs/how-it-works.md \
      docs/phase-plan.md docs/phase-1.md docs/phase-2.md docs/phase-3.md \
      docs/phase-4.md docs/session-log.md docs/benchmarks/phase-2.md

# src/segment_compare/
touch src/segment_compare/__init__.py src/segment_compare/__main__.py \
      src/segment_compare/parser.py src/segment_compare/config.py \
      src/segment_compare/normalizer.py src/segment_compare/hasher.py \
      src/segment_compare/comparator.py src/segment_compare/writer.py \
      src/segment_compare/pipeline.py src/segment_compare/partitioner.py \
      src/segment_compare/worker.py src/segment_compare/merger.py \
      src/segment_compare/external_sort.py src/segment_compare/py.typed \
      src/segment_compare/api/__init__.py

# tests/
touch tests/__init__.py tests/synthetic_data.py \
      tests/test_parser.py tests/test_config.py tests/test_normalizer.py \
      tests/test_hasher.py tests/test_comparator.py tests/test_writer.py \
      tests/test_pipeline.py tests/test_partitioner.py tests/test_merger.py \
      tests/test_external_sort.py tests/test_field_normalizer.py \
      tests/test_field_config.py tests/test_field_integration.py \
      tests/test_parallel.py tests/test_main.py tests/test_synthetic_data.py

# examples/ + ui/
touch examples/README.md examples/sample_a.dat examples/sample_b.dat
touch ui/README.md
```

**Windows (PowerShell):**

```powershell
$dirs = @(
    'config', 'docs/benchmarks', 'src/segment_compare/api',
    'tests', 'examples', 'ui'
)
$dirs | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }

$files = @(
    # top-level
    'CLAUDE.md','README.md','pyproject.toml','.flake8','.gitignore','.python-version',

    # config/
    'config/segments.json','config/normalization.json','config/runtime.json',
    'config/segments.example-rdw.json',

    # docs/
    'docs/architecture.md','docs/decisions.md','docs/how-it-works.md',
    'docs/phase-plan.md','docs/phase-1.md','docs/phase-2.md','docs/phase-3.md',
    'docs/phase-4.md','docs/session-log.md','docs/benchmarks/phase-2.md',

    # src/segment_compare/
    'src/segment_compare/__init__.py','src/segment_compare/__main__.py',
    'src/segment_compare/parser.py','src/segment_compare/config.py',
    'src/segment_compare/normalizer.py','src/segment_compare/hasher.py',
    'src/segment_compare/comparator.py','src/segment_compare/writer.py',
    'src/segment_compare/pipeline.py','src/segment_compare/partitioner.py',
    'src/segment_compare/worker.py','src/segment_compare/merger.py',
    'src/segment_compare/external_sort.py','src/segment_compare/py.typed',
    'src/segment_compare/api/__init__.py',

    # tests/
    'tests/__init__.py','tests/synthetic_data.py',
    'tests/test_parser.py','tests/test_config.py','tests/test_normalizer.py',
    'tests/test_hasher.py','tests/test_comparator.py','tests/test_writer.py',
    'tests/test_pipeline.py','tests/test_partitioner.py','tests/test_merger.py',
    'tests/test_external_sort.py','tests/test_field_normalizer.py',
    'tests/test_field_config.py','tests/test_field_integration.py',
    'tests/test_parallel.py','tests/test_main.py','tests/test_synthetic_data.py',

    # examples/ + ui/
    'examples/README.md','examples/sample_a.dat','examples/sample_b.dat',
    'ui/README.md'
)
$files | ForEach-Object {
    if (-not (Test-Path -LiteralPath $_)) {
        New-Item -ItemType File -Path $_ -Force | Out-Null
    }
}
```

After scaffolding, the real content for each file comes from this
repo's git history — clone or copy in the actual source rather than
filling in the empties by hand.

## Documentation

- **`docs/how-it-works.md` — engine walkthrough** (parse → index →
  normalize → hash → compare, with byte-level examples; reliability
  analysis with collision math; O(n) complexity argument). Read
  this first if you want to understand or trust the engine.
- `CLAUDE.md` — workflow and conventions
- `docs/architecture.md` — system design
- `docs/phase-plan.md` — phase-by-phase roadmap
- `docs/phase-1.md` … `phase-4.md` — detailed per-phase plans
- `docs/decisions.md` — architectural decision records (30 ADRs)
- `docs/benchmarks/phase-2.md` — measured 3M-record performance
- `docs/session-log.md` — working journal
- `examples/README.md` — sample-file format reference

## License

Internal project — license TBD.
