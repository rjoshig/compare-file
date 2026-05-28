# Architectural Decision Records

Each entry follows: **Title**, **Status**, **Context**, **Decision**,
**Consequences**. Decisions are append-only — supersede an old one by
adding a new entry that references it, never by editing in place.

---

## ADR-001 — Hash-based multiset comparison over pairwise O(n²)

**Status:** accepted

**Context:** Records contain repeating segments (e.g., 3 `TR01`s) that may
appear in different order in File A vs File B. Naively comparing every A
segment to every B segment is O(n²) per record and gets ugly with
duplicates.

**Decision:** For each segment type within a record, hash every
normalized segment instance and compare `collections.Counter` of hashes
between A and B. Equal Counters → match.

**Consequences:** O(n) per record. Handles duplicates and ordering
automatically. Requires a hash function (see ADR-002). Cannot tell which
specific instance differs when counts differ — only that the multisets
disagree (acceptable for the use case).

---

## ADR-002 — `hashlib.blake2b` default with built-in `hash()` switchable

**Status:** accepted

**Context:** We need a hash function for multiset comparison. blake2b is
cryptographically strong and 128-bit digests have negligible collision
risk at our scale. Python's built-in `hash()` is faster but only stable
within a process (PYTHONHASHSEED is randomized) and 64-bit (small
collision risk on 3M records).

**Decision:** Default to `blake2b(digest_size=16)`. Allow switching to
built-in `hash()` via `runtime.json::hash_method = "builtin"` for
single-process runs where speed matters more than cross-process
stability.

**Consequences:** Production-safe by default; opt-in speedup available.
Hashes are never persisted across runs, so built-in `hash()`'s
process-locality isn't a problem when used.

---

## ADR-003 — Cardinality inferred from data, not declared in config

**Status:** accepted

**Context:** Some segment types repeat per record; counts vary. We
could declare expected cardinality in config, but it's an ongoing
maintenance burden.

**Decision:** Don't declare cardinality. The Counter-based comparison
naturally handles "A has 3, B has 2".

**Consequences:** Adding a new segment is a config one-liner. Genuine
count discrepancies surface as ordinary mismatches.

---

## ADR-004 — Segment size read from data, not config

**Status:** accepted

**Context:** Each segment carries its own size in the header. We could
also declare expected sizes in config to catch corruption.

**Decision:** Read size from the header only. Config lists segment
names but no fixed sizes.

**Consequences:** Variable-length data per segment instance works
naturally. Corruption is detected by `ENDS` placement and stream
overrun, not by size cross-check.

---

## ADR-005 — `ENDS` as explicit record terminator

**Status:** accepted

**Context:** Records need a boundary. Options: a length prefix, a known
terminator segment, or a record delimiter only.

**Decision:** Every record ends with an `ENDS` segment, followed by a
configurable record delimiter (default `\n`).

**Consequences:** Unambiguous parsing. Missing `ENDS` is a clear
corruption signal. Slightly more verbose on the wire than length prefix.

---

## ADR-006 — Equal-count key partitioning over alphabetical range

**Status:** accepted (Phase 2)

**Context:** Parallel workers need a partition scheme. Alphabetical range
partitioning (e.g., A–F, G–M, …) skews badly when keys are like
`CUST00000001`…`CUST09999999` (all in the C range).

**Decision:** Sort the inner-join key list and split into N equal-count
chunks.

**Consequences:** Even worker load regardless of key distribution.
Requires a sorted key list, which we already need for the inner-join.

---

## ADR-007 — Position-based normalization in Phase 1, field-based in Phase 2

**Status:** accepted

**Context:** Real-world layout differences are easier to express as named
fields, but a position-based config gets us comparing data sooner.

**Decision:** Phase 1 ships position-based (`file_a_strip` /
`file_b_strip` / `exclude_positions`). Phase 2 adds field-based
(`file_a_layout` / `file_b_layout`) alongside. Both reduce to the same
downstream pipeline (a list of byte ranges to remove).

**Consequences:** Phase 1 keeps the surface area small. Phase 2 evolves
without breaking existing configs.

---

## ADR-008 — Per-segment normalization rules separate for File A and File B

**Status:** accepted

**Context:** A and B may come from different systems with different
layouts for the "same" segment.

**Decision:** Each segment's rule has a `file_a_*` and `file_b_*`
section. Strip rules can differ; the post-strip layout must align for
comparison to be meaningful.

**Consequences:** Cross-system reconciliation is supported by
configuration alone, no per-comparison code.

---

## ADR-009 — Exclude removes bytes rather than masks them

**Status:** accepted

**Context:** Timestamps and other always-different fields need to be
ignored. We could mask them (replace with `\x00`) or remove them.

**Decision:** Remove them. The hash sees a shorter byte string.

**Consequences:** Slightly cleaner semantics. Two segments are
"equivalent up to excluded bytes" iff their post-exclude bytes match
exactly.

---

## ADR-010 — Single copy in matches.dat, side-by-side in mismatches.dat

**Status:** accepted

**Context:** Matched records are equivalent after normalization, so
emitting both copies is waste. Mismatched records need both sides for
diagnosis.

**Decision:** `matches.dat` gets File A's bytes only. `mismatches.dat`
gets a side-by-side block with `=== KEY: ... ===` headers and `--- FILE
A ---` / `--- FILE B ---` separators.

**Consequences:** Smaller output for the common case. Mismatch output
is diagnostic-grade.

---

## ADR-011 — JSON config over YAML

**Status:** accepted

**Context:** Need a config format.

**Decision:** JSON. Stdlib parser, universal tooling, no indentation
pitfalls.

**Consequences:** Slightly more verbose than YAML. No comments — we
work around this with a `"$comment"` key convention that JSON parsers
ignore.

---

## ADR-012 — Engine as a library with multiple entry points

**Status:** accepted

**Context:** We need a CLI now (Phase 1), a web UI later (Phase 3), and
a service runner (Phase 4). Reimplementing comparison logic in each is
a maintenance nightmare.

**Decision:** All comparison logic lives in `pipeline.run` and the
modules it calls. CLI, FastAPI app, and service runner are thin wrappers
that handle their own I/O and call `pipeline.run`.

**Consequences:** One bug fix, three users of it. Clear test boundary
(test `pipeline.run` once; test wrappers for their own concerns).

---

## ADR-013 — Files assumed sorted by key by default

**Status:** accepted

**Context:** Inner-join requires either a sort or a hash-join. Production
extracts are typically already sorted; assuming that lets us stream both
files in parallel.

**Decision:** Default `input_sorted = true`. Phase 2 adds an optional
external-sort step for the false case.

**Consequences:** Fast path for the common case. Unsorted input is
handled but slower.

---

## ADR-014 — Vue.js 3 + Vite + FastAPI for Phase 3

**Status:** accepted

**Context:** Need a UI stack for Phase 3.

**Decision:** Vue.js 3 with Composition API, Vite as build tool,
FastAPI backend. No heavy state management framework (`store/index.js`
can be a hand-rolled reactive store if Pinia turns out to be overkill).

**Consequences:** Modern stack, low ceremony, plenty of community
material. No vendor lock-in beyond Vue itself.

---

## ADR-015 — Nested segments out of scope

**Status:** accepted

**Context:** Some fixed-format systems have segments that contain
sub-segments. Supporting this complicates the parser substantially.

**Decision:** Out of scope for all four phases in this plan. The parser
model stays flat: a record is a sequence of segments.

**Consequences:** If real-world data shows up nested, we revisit with
a new ADR and a phase plan extension.

---

## ADR-016 — Pluggable parser knobs in config from day one

**Status:** accepted

**Context:** The user anticipates "minor changes in data segments and
how to interpret" once real data arrives. Possible variants:
non-4-byte segment names, binary size fields (vs ASCII), non-ASCII
encodings, sizes excluding header, alternative record delimiters.

**Decision:** `config/segments.json::parser` carries:
`segment_name_bytes`, `size_field_bytes`, `size_encoding`,
`size_includes_header`, `data_encoding`. Phase 1 only honors the
defaults (4, 3, `ascii_int`, `true`, `ascii`) but the schema is in
place, so adding support for variants is a parser change, not a config
schema migration.

**Consequences:** Forward-compatible config. Phase 1 doesn't fully
implement the knobs but won't choke on them.

---

## ADR-017 — Run reproducibility via config hash in summary.json

**Status:** accepted

**Context:** Output bundles need to be self-describing for audit.

**Decision:** `config.py` computes a SHA-256 over the canonical
(sorted-key) JSON of the merged config bundle. `summary.json` records
the hash plus the source config paths.

**Consequences:** Cheap, unambiguous reproducibility check. Differing
outputs from "the same config" are immediately diagnosable.

---

## ADR-018 — Streaming + key→offset index design from Phase 1

**Status:** accepted

**Context:** Phase 1 is small enough to load both files in memory, but
Phase 2 isn't. If Phase 1 picks the in-memory shortcut, Phase 2 has to
rewrite.

**Decision:** Even in Phase 1, `pipeline.py` does an index-build pass
(`dict[key, (offset, length)]`) and then seeks into the file for each
joined record. Single process, but the architecture is what Phase 2
needs.

**Consequences:** Slightly more code in Phase 1, no rewrite in Phase 2.

---

## ADR-019 — Duplicate keys segregated to dedicated dup files per source

**Status:** accepted

**Context:** Production extracts are usually deduplicated upstream, but
data quality issues happen. Comparing a duplicated record is ambiguous;
silently joining-with-first is worse than visible failure.

**Decision:** During the index-build pass, if a key appears more than
once in File A, **all** occurrences with that key are routed to
`dups_A.dat` (not the engine's join set). Same for File B with
`dups_B.dat`. `summary.json` counts duplicate keys per file.

**Consequences:** Eight output files instead of six. Duplicates surface
loudly. The keys involved never reach `matches.dat` / `mismatches.dat`
/ `keymismatch_*.dat`.

---

## ADR-020 — Python 3.10+, pytest, black + flake8, mypy strict

**Status:** superseded by ADR-025

**Context:** Need to lock toolchain to avoid bikeshedding.

**Decision:** Python 3.10+ (modern type-hint syntax: `list[str]`,
`X | None`). pytest for tests. black for formatting. flake8 for
linting. mypy with `strict = true` for type checking.

**Consequences:** All four tools must pass clean on every commit. No
ruff (chose flake8 + black per user preference).

---

## ADR-021 — Phase 1 file encoding fixed to ASCII

**Status:** accepted

**Context:** Real-world fixed-format files come in many encodings
(ASCII, Latin-1, UTF-8, EBCDIC). Supporting all of them in Phase 1 adds
complexity without proving the comparison engine.

**Decision:** Phase 1 assumes ASCII. The `data_encoding` config knob
exists per ADR-016 but only `ascii` is honored. Phase 2 (or whenever
real data demands it) expands the supported set.

**Consequences:** Phase 1 ships sooner. Real-world non-ASCII data
requires a Phase 2 parser change before it can be processed.

---

## ADR-022 — Hand-crafted sample files committed in `examples/`

**Status:** accepted

**Context:** A tiny representative sample lets the parser have a
real-data target from day one, and serves as living documentation of
the file format.

**Decision:** Commit `examples/sample_a.dat` and `examples/sample_b.dat`
(four records each, hand-crafted to cover match / mismatch / orphan
scenarios). Expected output counts documented in `examples/README.md`.

**Consequences:** Smoke-testable parser on commit 1. Future format
variants get sample fixtures in `examples/` too.

---

## ADR-023 — Eight output files instead of six

**Status:** accepted, supersedes nothing (extends the original spec)

**Context:** ADR-019 introduces dup files. The original spec listed six
output files.

**Decision:** The canonical output set is now eight:
`matches.dat`, `mismatches.dat`, `keymismatch_A.dat`,
`keymismatch_B.dat`, `dups_A.dat`, `dups_B.dat`, `report.csv`,
`summary.json`.

**Consequences:** Writer module owns eight file handles. UI / email
templates reference eight files.

---

## ADR-024 — Comparator iterator interface

**Status:** accepted

**Context:** Phase 2 parallelism shouldn't require rewriting the
comparator.

**Decision:** `pipeline.run` consumes
`Iterator[tuple[key, record_bytes_a, record_bytes_b]]`. Phase 1's
single-process index walk is one producer; Phase 2's process pool is
another. Downstream (normalize → hash → compare → write) is unchanged.

**Consequences:** Phase 2 work is "swap the producer", not "rewrite the
engine".

---

## ADR-025 — Python 3.12+ via pyenv (supersedes ADR-020 on Python version)

**Status:** accepted, supersedes ADR-020 (Python version only; pytest /
black / flake8 / mypy decisions from ADR-020 still stand)

**Context:** ADR-020 set the floor at Python 3.10+. Development is now
standardized on pyenv with Python 3.12 or newer. 3.12 brings
performance improvements, better error messages, PEP 695 generics
syntax, and aligns with the version the maintainer is running locally.
3.10 and 3.11 support is dropped to avoid CI matrix bloat and to allow
3.12+ syntax where it improves clarity.

**Decision:** Python 3.12+ is the supported floor. `pyenv` is the
expected version manager for local development; **3.12.7** is the
pinned local version (see `.python-version`). Tooling updated:

- `pyproject.toml::requires-python` → `>=3.12`
- `pyproject.toml::classifiers` → 3.12, 3.13
- `[tool.black]::target-version` → `["py312", "py313"]`
- `[tool.mypy]::python_version` → `"3.12"`

**Consequences:** Contributors must have Python 3.12+ on their path
(pyenv recommended). 3.12-specific syntax is fair game. Anyone still on
3.10/3.11 needs to upgrade before contributing. The black warning noted
in the 2026-05-28 session log (py312 target on a 3.11 interpreter) is
moot now.

---

## ADR-026 — Realistic fixture supersedes 10K synthetic for Phase 1 closure

**Status:** accepted, modifies Phase 1 plan in `docs/phase-1.md`

**Context:** The original Phase 1 plan called for a 10K-record
synthetic generator (`tests/synthetic_data.py::generate_pair`) plus a
10K-record integration test as the closing deliverable. While planning
phase-1 closure, the user supplied a production-shaped record layout
and asked for a small, deliberate, hand-built fixture covering all ten
scenarios instead. The fixture lives at
`examples/sample_a.dat` (10 records) and `examples/sample_b.dat` (11
records) and exercises every scenario in §"Synthetic test scenarios"
in `docs/phase-1.md`.

**Decision:** The realistic 10/11-record fixture replaces the planned
10K synthetic generator for Phase 1 acceptance criteria #3 and #4.
The single integration test
`tests/test_pipeline.py::test_run_against_sample_files_matches_oracle`
now satisfies both criteria. The synthetic generator becomes a Phase 2
benchmarking deliverable (`tests/synthetic_data.py` will land alongside
the 3M-record performance fixture).

**Consequences:**

- Phase 1 closes one commit sooner with a fixture the user has
  reviewed and recognizes.
- The realistic fixture is the canonical phase-1 oracle going forward;
  the old simple `TU4R019 + NM01017 + ENDS007` samples are removed.
- Synthetic record generation is still useful for ad-hoc scenario
  tests; the helper `tests/test_pipeline.py::_make_record` produces
  records compatible with the new config (key at TU4R data `[4, 16)`).
- Phase 2 benchmarking inherits the production-shaped layout, so the
  3M-record generator can reuse the same segment templates rather than
  reinventing them.

---

## ADR-027 — Timestamped output filenames (`<base>_YYYYMMDDHHMM.<ext>`)

**Status:** accepted

**Context:** Successive runs against the same output directory used to
clobber each other — the writer always wrote `matches.dat`,
`mismatches.dat`, etc. as bare names. In real operational use (Phase
4 service mode, manual investigations, ad-hoc CLI runs), an operator
wants to keep multiple runs side by side without manually rotating
directories.

**Decision:** Every output file produced by `pipeline.run` carries a
12-character UTC timestamp (`YYYYMMDDHHMM`) suffixed before the
extension. Examples: `matches_202605280358.dat`,
`report_202605280358.csv`, `summary_202605280358.json`. All eight
outputs from a single run share the stamp so they group naturally on
disk.

Implementation details:

- The stamp is computed once at the start of `pipeline.run`
  (`start_time.strftime("%Y%m%d%H%M")` in UTC) and threaded through to
  `OutputWriter(filename_stamp=...)`.
- The stamp is also stored on `Summary.filename_stamp` and emitted in
  `summary.json` so external callers can locate the sibling files.
- `pipeline.run` accepts an optional `run_timestamp: datetime` arg for
  deterministic tests; the CLI never sets it.
- `writer.stamped_filename(base, stamp)` is the single source of truth
  for the on-disk naming convention; callers (including tests) use it
  to resolve paths.
- Bare filenames (`matches.dat`, etc.) are still the default when
  `OutputWriter` is invoked without a stamp — preserves the
  `tests/test_writer.py` unit tests, which don't care about stamping.

**Consequences:**

- Operators get versioned outputs for free; no run-isolation logic
  needed in the wrapper.
- Per-run output directories (e.g., one subdir per run) are no longer
  required; flat output directories work fine and the stamp keeps
  things sorted chronologically.
- Minute-level granularity means two runs started in the same UTC
  minute against the same output directory will clobber each other.
  Acceptable for human-driven and scheduled-cadence use; if
  sub-minute resolution is needed, the stamp format becomes a config
  knob in a future ADR.
- Phase 4 service mode keeps its `{run_id}` archiving (different
  layer); the timestamped filenames are independent of the
  archive-directory scheme.

---

## ADR-028 — Worker count is configurable; default 8 in config; CLI overrides

**Status:** accepted

**Context:** Phase 2 introduces a `--workers N` CLI flag that selects
between the Phase 1 single-process path (N=1) and the parallel
pipeline (N>1). The initial implementation hard-coded the CLI default
to 1 so the new path was opt-in. Once correctness was verified, the
question became: what should "default behavior" mean for the engine
across CLI, FastAPI (Phase 3), and the scheduled-service runner
(Phase 4)? Production targets (3M records, big-org servers with
16+ cores) want parallelism by default; local laptop runs want
parallelism too (no penalty even on small inputs — see the bench
note below). One config knob, several entry points.

**Decision:** `runtime.json::parallel_workers` is the single source of
truth for the default worker count across **all** entry points. The
CLI `--workers N` flag, when supplied, overrides the config; when
omitted, the CLI reads `config.runtime.parallel_workers`. Phase 3 and
Phase 4 runners will adopt the same precedence (config default; per-
request / per-config-file overrides where applicable).

Stock-config default: **`parallel_workers: 8`**. Justification:

- Production target is big-org servers with ≥ 8 cores; 8 workers
  fully utilizes those.
- Local benchmarks show no measurable penalty for 8 workers on
  small fixtures (the 21-record realistic fixture runs in ~70 ms
  end-to-end via the parallel path on a laptop, including subprocess
  spawn). ProcessPoolExecutor's lazy task dispatch avoids worst-case
  spawn cost when chunks are tiny.
- 8 is a "production-shaped" number — not 1, not 16. Anything
  smaller fails to use modern hardware; anything bigger
  over-subscribes laptops.

CLI surface:

- `--workers N` overrides config for that invocation.
- Omitted: uses `runtime.json::parallel_workers`.
- Validation: `< 1` is rejected at the CLI boundary with exit code
  10 (config error).

**Consequences:**

- "Default behavior" is now parallel. Phase 1 unit tests that want
  the single-process code path explicitly pass `--workers 1`
  (deterministic + fast).
- Tuning the worker count is a config edit, not a code change —
  important for the Phase 4 service runner where each scheduled job
  is described by a JSON config.
- The configurable-default knob and the runtime `partition_strategy`
  field together future-proof the parallel pipeline: as new
  partitioning strategies land (e.g., size-balanced for skewed
  records), they slot in via config without CLI changes.

---

## ADR-029 — Field-based normalization: canonical form, dispatch, single-form-per-segment

**Status:** accepted

**Context:** Phase 2 introduces `FieldNormalizer` alongside the
existing `PositionNormalizer`. The two forms address different
realities:

- **Position-based** (Phase 1) — describe segment data as byte ranges
  to strip/exclude. Right when the layout is the same across both
  source systems and you only need to suppress specific positions
  (timestamps, segment counts, etc.).
- **Field-based** (Phase 2) — describe segment data as a named list
  of logical fields, possibly differing across A and B in field
  count, order, or per-field length. Right for cross-system
  reconciliation where Source A emits `first/middle/last` in a
  different physical order than Source B, or where one side carries
  a trailing filler that the other doesn't.

Three design questions came up while building this:

1. What's the canonical byte form a `FieldNormalizer` emits?
2. Can one segment use both forms in the same JSON entry?
3. What happens when the layout's total length disagrees with the
   segment's actual data length at runtime?

**Decision:**

1. **Canonical form is sorted `<name>=<value>` joined by `\x1F`
   (ASCII Unit Separator).** Each retained field becomes
   `name_bytes + b"=" + value_bytes`; the resulting list is sorted
   by encoded bytes (≡ sort by ASCII name) and joined. Sorting is
   what makes A and B with different *physical* field order produce
   *identical* canonical bytes — the engine compares by logical
   field name, not byte position (the headline Phase 2 capability).
2. **A single segment cannot mix position-form and field-form keys.**
   `_build_normalization` rejects entries that contain both kinds at
   load time with a clear error. Different segments in the same
   `normalization.json` may use different forms; that's the whole
   point of `CompositeNormalizer`.
3. **Length mismatch is a fatal error at first occurrence.** When
   the chosen layout's `sum(field.length for ...)` doesn't equal
   `len(raw_data)`, `FieldNormalizer.normalize` raises `ValueError`
   with the segment name, source, expected length, actual length, and
   field count. Layout must exactly cover the segment data — being
   permissive here would mask config typos and schema drift.

`CompositeNormalizer(position_rules, field_rules)` is the public
type the pipeline uses; it dispatches per segment based on which map
the segment appears in. Segments absent from both maps pass through
unchanged. The two maps live as separate fields on `ResolvedConfig`
(`normalization`, `field_normalization`) so the audit hash stays
stable and existing callers that only inspect `normalization` keep
working.

**Consequences:**

- The Phase 2 acceptance test
  `tests/test_field_integration.py::test_field_config_classifies_records_same_as_position_config`
  proves the two forms encode the same equivalence relation on the
  realistic 10/11-record fixture: byte-identical `*.dat` outputs,
  identical aggregate counts, identical per-segment statistics.
- Cross-system reconciliation (A has 4 fields, B has 5 with filler
  excluded) is now expressible in one `normalization.json` entry —
  no parser changes, no per-segment custom code.
- Adding new normalization forms later (e.g., a JSONPath-style
  selector for nested formats — out of scope per ADR-015) becomes
  another row on the `CompositeNormalizer` dispatch table, not a
  rewrite of the comparator.
- The separator `\x1F` and the `name=value` delimiter `=` are byte
  values that should not appear in real fixed-format ASCII data.
  If a future ADR opens the door to non-ASCII data, the encoding
  may need a small escape rule; for Phase 2's ASCII-only world,
  collisions are not possible by construction.

---

## ADR-030 — External chunk-and-merge sort for unsorted inputs

**Status:** accepted

**Context:** The Phase 1/2 inner-join assumes both inputs are sorted
by key (ADR-013). Most production extracts are pre-sorted, but some
sources deliver unsorted output. The architecture allowed for an
optional pre-sort pass; Phase 2 acceptance criterion #5 makes that
concrete.

Two reasonable algorithms:

1. **In-memory sort.** Load both files into memory, sort, run the
   pipeline. Simple but bounded by RAM; fails at the 3M-record scale
   that Phase 2 targets (~1.3 GiB per file).
2. **External chunk-and-merge sort.** Pass 1 buffers up to
   ``runtime.chunk_size`` records, sorts each chunk in memory, spills
   to a temp file in ``runtime.sort_temp_dir``. Pass 2 uses
   ``heapq.merge`` keyed on record key to interleave the spill files
   into a single sorted output. O(N log N) compute, O(chunk_size)
   memory.

**Decision:** External chunk-and-merge sort. ``src/segment_compare/external_sort.py``
houses ``external_sort_file(input_path, output_path, config)``. Hot
points:

- **Trigger.** `pipeline.run` and `pipeline.run_parallel` accept an
  ``external_sort: bool`` argument; if True (CLI: ``--external-sort``)
  OR ``config.runtime.input_sorted`` is False, both inputs are sorted
  before the index-build pass. Sorted copies land at
  ``runtime.sort_temp_dir / sorted_a_<stamp>.dat`` and
  ``sorted_b_<stamp>.dat``.
- **Originals preserved.** ``summary.json``'s ``file_a_path`` and
  ``file_a_size_bytes`` record the *original* input paths, not the
  sorted temp files. Auditors care about what was passed in; the
  sort is an implementation detail.
- **Chunk cleanup.** Temp chunk files (``chunk_*.dat`` under
  ``sort_temp_dir``) are deleted via a ``try/finally`` even on
  exception. The final sorted output is left on disk for the rest
  of the pipeline to read; cleanup of that file is the caller's
  responsibility (the run output dir is small per stamp, so this
  is acceptable in practice).
- **Memory.** Each spill batch is sorted with Python's Timsort on
  ``(key, raw_bytes)`` tuples. At ``chunk_size = 10_000`` and ~500
  bytes/record, peak buffer is ~5 MiB. The merge step holds one
  file descriptor per chunk; 3M / 10K = 300 fds for the full
  benchmark fixture, well under typical ulimits.

**Consequences:**

- 3M-record external sort takes ~74 s on the local laptop with peak
  RSS ~1.6 GiB. Roughly the same time budget as the comparison
  itself — sorting unsorted input doubles end-to-end wall time, as
  expected.
- The sort path is **serial**; it runs single-process even when
  ``--workers > 1``. Parallelizing the spill phase is a future
  optimization (each worker scans a byte range of input). Not
  required by any Phase 2 acceptance criterion.
- For files that already happen to be sorted, the sort path is a
  no-op semantically but still costs the chunk+merge pass.
  Operationally: leave ``input_sorted = true`` in config when
  inputs are pre-sorted; flip it (or pass ``--external-sort``) only
  for sources where sort order is unreliable.
- The Phase 2 test suite verifies the sort path produces engine
  output byte-identical to the sorted-input baseline (counts,
  ``*.dat`` outputs, ``report.csv``).
