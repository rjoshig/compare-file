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

**Status:** superseded by ADR-033 (position-based form removed; the
per-file layout schema is the only form going forward)

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

**Status:** superseded by ADR-033 (per-file rules now live as
``segments[]`` blocks inside each ``layout_file_*.json``; the
normalization.json file is gone)

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

**Status:** superseded by ADR-032 (floor lowered back to 3.10+); pyenv
3.12.7 remains the Mac dev pin per ADR-032

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

**Status:** superseded by ADR-033 on dispatch (position-form is gone;
the canonical-form bytes contract — sorted ``name=value`` joined by
``\x1F`` — is unchanged)

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

---

## ADR-031 — Per-file Record Descriptor Word (RDW) prefix is configurable, not parsed

**Status:** accepted (per-file ``rdw`` block now lives inside each
``layout_file_*.json`` rather than as a sibling under
``segments.json::parser`` — see ADR-033; the on-wire contract is
unchanged)

**Context:** Real-world inputs sometimes prepend a small fixed prefix to
every record before the key segment. Mainframe extracts are the most
common source — the classic RDW is a 4-byte header
(2-byte length + 2-byte reserved) sitting in front of each record. The
user surfaced this with a file that looks like
``[rdw1][rdw2][TU4R]…[ENDS]`` rather than ``[TU4R]…[ENDS]`` from byte
zero. Either File A, File B, or both may carry such a prefix; the two
sides can use different widths and encodings.

The user explicitly does *not* need the engine to interpret the RDW
values — the prefix is purely a framing artifact. The job is to skip it
cleanly so the rest of the record parses as today.

**Decision:** Add an optional per-file ``rdw`` block to
``segments.json::parser``:

```json
"parser": {
  "file_a": {"rdw": {"rdw1_bytes": 2, "rdw2_bytes": 2, "encoding": "binary_le_uint"}},
  "file_b": {"rdw": {"rdw1_bytes": 2, "rdw2_bytes": 3, "encoding": "ascii_int"}}
}
```

- Either block (or both) may be omitted; absent means "no RDW prefix on
  this side".
- ``rdw1_bytes`` and ``rdw2_bytes`` must both be > 0. The engine skips
  exactly ``rdw1_bytes + rdw2_bytes`` raw bytes before each record's
  key segment.
- ``encoding`` is one of ``"ascii_int"`` (zero-padded ASCII decimal) or
  ``"binary_le_uint"`` (unsigned little-endian integer). Stored on
  :class:`RdwConfig` for diagnostics; the skip path is encoding-
  agnostic. Future validation work (e.g., assert rdw1 equals the
  record's actual length) can lift this field without a schema change.
- Length validation is **out of scope** for this ADR. If the prefix
  lies about the record length, the engine still parses normally; the
  layout of the record bytes themselves is what matters.

Parser surface: ``iter_records(stream, parser_cfg, segments_cfg,
rdw_cfg=None)``. When ``rdw_cfg`` is set, the parser consumes
``rdw_cfg.total_bytes`` at the head of each iteration; a truncated RDW
raises :class:`ParseError`. ``Record.offset`` and ``Record.length`` are
reported *relative to the key segment*, so seeking back to a record by
``(offset, length)`` does **not** need to re-skip the RDW — the
single-record read path in pipeline / worker stays uniform.

Pipeline wiring:

- ``pipeline.run`` / ``pipeline.run_parallel`` read
  ``config.file_a_rdw`` / ``config.file_b_rdw`` and thread the right
  RDW into each per-file ``_index_file`` and ``external_sort_file``
  call.
- The external-sort pass strips the prefix on its way through; sorted
  temp copies never carry an RDW, so the post-sort indexing pass is
  called with ``rdw_cfg=None``. ``summary.json`` still records the
  *original* input paths (ADR-030 contract).
- Workers do not need RDW awareness — they read records by
  ``(offset, length)`` from the index, and those offsets already point
  past the RDW.

**Consequences:**

- A new schema example lives at ``config/segments.example-rdw.json``
  showing both files with different RDW widths and encodings. Copy and
  edit when onboarding a new source.
- Adding RDW support to a previously-plain pipeline is a config-only
  change. No code change is required to opt in.
- Encoding is currently informational; introducing length validation
  later is a parser-only change that can read the recorded encoding
  to decode rdw1 correctly.
- The skip happens at the iterator boundary, so every consumer
  (single-process pipeline, parallel pipeline, external sort, dry run)
  picks it up uniformly with no per-call-site special-casing.
- 14 new tests pin the behavior: parser-level (skip, truncated, EOF,
  None-is-identity, multi-record offsets), config loader (presence,
  absence, validation errors, example file loads), and an end-to-end
  pipeline test comparing an RDW-prefixed File A with a plain File B.
  Total suite: **212 tests passing**.

---

## ADR-032 — Python floor lowered to 3.10+ for cross-platform contribution (supersedes ADR-025 on the floor)

**Status:** accepted, supersedes ADR-025 on the supported Python version
only. ADR-020's pytest / black / flake8 / mypy strict tooling decisions
remain in effect.

**Context:** ADR-025 raised the floor to Python 3.12+ in part to align
with the Mac maintainer's pyenv-managed 3.12.7. A Windows contributor
running stock Python 3.11.1 cannot install the package because of the
`requires-python = ">=3.12"` constraint, and `pyenv-win` is not always
available in their environment. Separately, the production server
ships Python 3.6.5 with no install path; that constraint is out of
scope here and tracked under its own follow-up (see the 2026-05-28
session log).

A code audit confirmed the engine uses **no** 3.11- or 3.12-specific
syntax:

- Every module imports `from __future__ import annotations`, so
  `list[str]` / `X | None` / `tuple[int, int]` annotations are stored
  as strings at runtime — they work on 3.7+.
- The only 3.10+ language feature actually exercised is
  `@dataclass(slots=True)` (used throughout `parser.py`, `config.py`,
  `writer.py`, etc.).
- No `tomllib`, no `typing.Self`, no `typing.override`, no
  `ExceptionGroup` / `except*`, no PEP 695 generic syntax, no `match`
  statements.

So the floor can drop to 3.10 without touching code.

**Decision:** Lower the supported Python floor from 3.12+ back to
**3.10+** (3.10, 3.11, 3.12, and 3.13 are all supported). pyenv 3.12.7
remains the **recommended** local pin for the Mac maintainer (it is
the only interpreter the test suite has been run against), but is no
longer a hard requirement.

Tooling updates:

- `pyproject.toml::requires-python` → `>=3.10`
- `pyproject.toml::classifiers` → 3.10, 3.11, 3.12, 3.13; added
  classifiers for macOS and Windows operating systems.
- `[tool.black]::target-version` → `["py310", "py311", "py312",
  "py313"]`
- `[tool.mypy]::python_version` → `"3.10"` (mypy still type-checks
  under strict mode; lowering the target makes it flag accidental
  use of newer-than-3.10 syntax during review).

Docs updates:

- `README.md` — split Setup into Mac/Linux (pyenv) and Windows
  (PowerShell stock Python) subsections; added a "Production server"
  subsection that explicitly calls out the 3.6 gap as out-of-scope.
- `CLAUDE.md` — supported Python is now "3.10+, Mac dev pinned to
  3.12.7"; the version-drift warning is scoped to the Mac dev box.

**Consequences:**

- Windows contributors with stock Python 3.10/3.11/3.12 can now
  `pip install -e ".[dev]"` without changes.
- The Mac dev box stays on 3.12.7 via pyenv; nothing about the local
  workflow changes day-to-day.
- 3.10/3.11/3.12-specific syntax going forward must respect the 3.10
  floor. mypy's `python_version = "3.10"` setting enforces this in
  type-check; black formats consistently for all four targets.
- CI was not configured to run a matrix yet; if/when CI lands, it
  should test on 3.10 and 3.13 at minimum to keep the floor and
  ceiling honest.
- The production server's Python 3.6.5 + no-install constraint is
  **not** addressed by this ADR. A future ADR will pick between
  PyInstaller, python-build-standalone, and a container approach
  when prod deployment becomes a priority. Backporting the engine to
  3.6 is rejected outright: missing `dataclasses`, missing
  `from __future__ import annotations`, missing `slots=True`, and an
  interpreter that has had no security patches since 2021.

**Verification on 3.10/3.11/3.12:** the Mac dev box runs 3.12.7 and the
suite is green there (`black --check`, `flake8`, `mypy --strict`,
`pytest` 212 passing). The 3.10 / 3.11 paths are inferred from the
above audit but not exercised yet — Windows contributors should run
`pytest` once after `pip install -e ".[dev]"` and report any
regression so this ADR can be amended.

---

## ADR-034 — Context-sensitive segment aliasing (AD01-after-EM01 → EMAD)

**Status:** accepted; extends ADR-033

**Context:** A real-world feed reuses a single on-wire segment name
for two distinct logical meanings depending on where it sits in the
record. Concrete example from the user: ``AD01`` segments appearing
before an ``EM01`` segment in a record are ordinary postal addresses;
``AD01`` segments appearing **after** an ``EM01`` are email-related
addresses. They must be compared as separate buckets so a difference
in the postal-address group doesn't mask a difference in the
email-address group (or vice versa). The on-wire bytes cannot be
changed (the operator does not control the upstream extract).

The multiset-of-hashes comparator (ADR-001) already buckets segments
by name, so the fix is to give the second group of ``AD01`` a
*different* name at parse time. Renaming downstream — at the
comparator or normalizer level — would force every consumer
(comparator + normalizer + writer + summary aggregation) to learn
about aliases. Renaming once, right after parse, keeps the rest of
the engine oblivious.

**Decision:** Add an optional ``segment_aliases`` block to each layout
file. The pipeline walks each parsed record's segments in order; the
instant an ``after_segment`` is seen, every subsequent
``wire_name`` occurrence in that record is renamed to ``logical_name``
in memory. The on-disk bytes are never modified.

### Schema (per layout file)

```jsonc
{
  ...
  "segment_aliases": [
    { "wire_name": "AD01", "logical_name": "EMAD", "after_segment": "EM01" }
  ],
  "segments": [
    { "name": "TU4R", "role": "key", ... },
    { "name": "AD01", "size": 50, "fields": [ /* postal-address fields */ ] },
    { "name": "EM01", "size": 45, "fields": [ /* email fields */ ] },
    { "name": "EMAD", "size": 50, "fields": [ /* email-address fields */ ] },
    { "name": "ENDS", "role": "end", ... }
  ]
}
```

- ``segment_aliases`` is optional; omitted or empty list = no aliases.
- ``wire_name``, ``logical_name``, and ``after_segment`` must all be
  declared in ``segments[]``. Both shapes are needed because each
  logical name carries its own field layout (the post-EM01 ``AD01``
  bytes may be sliced and labeled differently from the pre-EM01
  ones).
- ``wire_name`` and ``logical_name`` must differ.
- Per-segment ``size`` for ``wire_name`` and ``logical_name`` must
  match — the same wire bytes are re-bucketed, not re-sized.
- At most **one alias per ``wire_name``** in a single layout. Mapping
  one wire segment to multiple logical names under different
  triggers would create ambiguous rename precedence; declared
  explicitly out of scope.
- Per-file (declared in each ``layout_file_*.json`` independently).
  In practice A and B will usually carry identical aliases, but the
  schema doesn't force symmetry. If A renames but B doesn't, the
  comparator will surface count differences on the renamed segment
  — that's the operator's choice.

### Trigger semantics

- **Once-triggered, stays-triggered within a record.** After
  ``after_segment`` has appeared in the current record, every
  subsequent ``wire_name`` is renamed until the record ends. Even if
  the trigger segment appears multiple times, the engine doesn't
  reset — there's no need to track which trigger "owns" a given
  rename.
- **No trigger, no rename.** A record that contains ``wire_name`` but
  no ``after_segment`` is left untouched (the rename never fires).
  Records with neither, or with only the trigger, are also untouched.
- **Per-record scope.** The trigger state resets at every record
  boundary; one record's EM01 cannot trigger renames in the next
  record.

### Where the rename runs

The parser stays pure — :class:`Segment` instances yielded by
``iter_records`` always carry the on-wire name. A pipeline-level
helper, ``pipeline._apply_aliases(record, aliases)``, returns a new
:class:`Record` whose ``segments`` tuple has any in-context
``wire_name`` instances rewritten to ``logical_name``. Wired into:

- ``pipeline._index_file`` — so per-segment counts in
  ``summary.json`` bucket renamed segments separately.
- ``pipeline._read_record_at`` and ``worker._read_record_at`` —
  so the comparator sees the logical names when hashing.

``external_sort`` does *not* apply the rename: the sort path
preserves raw bytes (record.raw is the on-wire encoding). The sorted
output still carries the wire names; the post-sort index pass re-applies
the rename. ``record.raw`` (used by dups/orphans/matches/mismatches
file writes) is unchanged, so on-disk output files retain the
original on-wire segment names — the rename is purely a comparison-time
concept.

### Consequences

- Operators can split one wire segment into multiple comparison
  buckets without touching the input files. Configuration-only fix.
- ``summary.json``'s ``per_segment`` block carries separate entries
  for ``AD01`` and ``EMAD``; ``report.csv`` rows for renamed
  segments cite the logical name. ``matches.dat`` /
  ``mismatches.dat`` / ``dups_*.dat`` / ``keymismatch_*.dat`` show
  the raw on-wire names since they emit ``record.raw``.
- The "one alias per wire_name" rule keeps the trigger logic
  unambiguous. A future ADR could relax this by introducing
  precedence rules (e.g., most-recently-triggered wins) if a real
  use case demands it.
- ``FileLayout`` and ``EngineConfig`` gain ``segment_aliases`` and
  ``file_a_aliases`` / ``file_b_aliases`` respectively. The audit
  hash (ADR-017) automatically covers the new field since it hashes
  the raw layout JSON.
- 11 new tests pin the behavior:
  - 9 layout-loader cases (default-empty, round-trip, every
    validation rule).
  - 2 end-to-end pipeline cases (AD01-after-EM01 buckets as EMAD;
    AD01-without-EM01 stays as AD01).

---

## ADR-035 — compare_reports.csv + compare_reports.html alongside summary.json

**Status:** accepted

**Context:** `summary.json` is the machine-readable source of truth
for a run's aggregate metrics — used by the API, the future Phase 4
service runner, and any downstream tooling that wants a deterministic
shape. But operators reading run results manually want two friendlier
views:

1. A **spreadsheet** view they can open in Excel / Google Sheets /
   Numbers, filter by section, sort by metric, paste into a Slack
   thread. Flattening JSON by hand for this is tedious and error-prone.
2. A **browser** view they can email or attach to a ticket — a
   self-contained HTML page with sectioned tables, no external
   assets required.

**Decision:** Every run produces two additional output files alongside
`summary.json`, both stamped per ADR-027:

- `compare_reports_<stamp>.csv` — 3-column long-format CSV with
  header `section,key,value`. Sections in declaration order:
  `run`, `inputs`, `counts`, `per_segment`, `timing`, `config_paths`.
  Per-segment rows use `<segment_name>.<stat>` keys so a single
  segment's four numbers stay grouped (and the file diffs predictably
  across identical-input runs).
- `compare_reports_<stamp>.html` — self-contained HTML report with
  inline CSS (no `<link>` or `<script>` tags, no external assets).
  Sectioned tables for Inputs, Aggregate counts, Per-segment
  breakdown, Timing, and Config provenance. Numbers are
  thousand-separated and right-aligned with tabular-nums; matched /
  mismatched counts are color-coded. All path-like and audit-hash
  strings are HTML-escaped via `html.escape` to prevent markup
  injection.

Both are produced by:
- `writer.write_compare_reports_csv(summary, path)` and
- `writer.write_compare_reports_html(summary, path)`,

called from `OutputWriter.finalize` (single-process path) and from
`pipeline.run_parallel` immediately after `write_summary` (parallel
master). The single-process and parallel paths produce identical
report content given identical aggregate inputs.

The CSV and HTML never carry information that isn't in `summary.json`
— they're projections, not new data. If you need a metric in the
reports, add it to `Summary` first; the writers reach in through the
same dataclass.

**Consequences:**

- Total output file count per run goes from 8 → 10. The full set is
  now: `matches.dat`, `mismatches.dat`, `keymismatch_A.dat`,
  `keymismatch_B.dat`, `dups_A.dat`, `dups_B.dat`, `report.csv`,
  `summary.json`, `compare_reports.csv`, `compare_reports.html`.
- The two report files are small (a few KB regardless of input size
  — they don't grow with record count) and written once at run end,
  so they add no measurable cost to the run.
- The HTML uses inline CSS only (per the "self-contained" property)
  — no theming flexibility, but it works without a network or a
  file-server. If a future need wants theming, the file can become
  a template lookup later without breaking the contract.
- 7 new tests pin the behavior:
  - CSV: header is exactly `section,key,value`; every documented
    section appears; key scalars + every per-segment stat are
    present; config_paths preserves the `layout_a` / `layout_b` /
    `runtime` order; round-trips via `csv.reader`.
  - HTML: starts with `<!DOCTYPE html>`, ends with `</html>`,
    contains no `<link>` or `<script>` tags; every section heading is
    present; metric values render in the body; dangerous characters
    (`<script>...`) injected into a config path are HTML-escaped.
  - `OutputWriter.finalize` emits all three (summary.json + the two
    reports) at the stamped paths.
- The parallel-pipeline test now asserts the parallel master writes
  the two reports too.
- This ADR does not change `summary.json`'s schema or the existing
  `report.csv` (per-mismatch-row file from ADR-023). Those remain
  the single sources for their respective use cases.

---

## ADR-036 — Per-key mismatch matrix + HTML report overhaul

**Status:** accepted; extends ADR-035

**Context:** After ADR-035, the run produced ``summary.json`` plus a
flat CSV and a row-wise HTML rendering of the same aggregates. Real
operators reviewing run results raised four concrete gaps:

1. **No per-record visibility.** The per-segment breakdown shows
   *how many* records had a mismatch in (say) ``NM01``, but not
   *which keys* mismatched on which segments. For triage, the
   operator wants a key-by-key matrix.
2. **No layout context in the report.** The HTML didn't show what the
   two files claimed to look like. Reviewing a mismatch without
   seeing the two layouts side-by-side forces a separate
   layout-file diff.
3. **Aggregate counts didn't link to the records.** "3 mismatches" is
   an integer; the operator immediately wants ``mismatches.dat`` open.
   The HTML didn't link to it.
4. **Side-by-side metrics, not row-wise.** Operators compare File A
   to File B; rendering each File-A value above the corresponding
   File-B value (two rows) is harder to scan than two columns.

**Decision:** Three changes:

### 1. New output file ``keys_mismatch_matrix_<stamp>.csv``

One row per joined-key record where at least one segment mismatched
(fully-matched records are intentionally omitted). Columns:

```csv
key,<seg1>,<seg2>,...,<segN>,segment_count_mismatch
```

- The segment columns are the union of segment names declared across
  ``layout_file_A.json`` and ``layout_file_B.json``, in File A's
  declared order with B's extras appended (``EngineConfig.known_segments``).
- Each cell is ``Y`` (segment matched in this record), ``N`` (segment
  mismatched), or empty (segment absent from both A's and B's record
  for this key).
- The trailing ``segment_count_mismatch`` column is a pipe-delimited
  list of segments whose count differs between A and B for this key
  (``status == count_diff``). Empty when no count differences exist
  for the key.

Implementation: ``pipeline.run`` and ``worker.run_worker`` build a
:class:`KeyMatrixEntry` whenever ``compare_records`` returns a
non-matched verdict. Single-process buffers them in memory; the
parallel master concatenates per-worker tuples in worker-id order
(which mirrors the join's sorted-key order). The full file is
written once at run end via ``write_keys_mismatch_matrix_csv``.

### 2. HTML report overhaul

The HTML report adds three new sections and rearranges one:

- **Layouts** (new) — File A and File B layouts side-by-side in a
  CSS-flex two-column block. Each column shows: layout filename,
  key segment / key field / key range, end segment, record
  delimiter, strip prefix, RDW, segment aliases, sort metadata, and
  a per-segment table with field-by-field breakdowns (KEY/exclude
  flags highlighted).
- **Inputs** (reworked) — now a side-by-side table with one column
  per file (Metric / File A / File B) instead of one row per file.
  The user explicitly asked for "side by side metrics in HTML not
  row wise."
- **Aggregate counts** (extended) — adds a "File" column whose cells
  are clickable relative-path links to the stamped output file for
  that metric: ``Records matched`` → ``matches_<stamp>.dat``,
  ``Duplicate keys in A`` → ``dups_A_<stamp>.dat``, etc. The
  ``keys_in_both`` row shows ``—`` since no single output file
  contains "the matches and mismatches together"; that's
  ``report.csv``'s job, surfaced separately in the CSV report's
  ``output_files`` section.
- **Per-key mismatch sample** (new) — first 20 rows from the
  matrix, rendered as a styled HTML table (Y = green, N = red,
  empty = grey dot). Includes a "Showing N of M" caption and a
  clickable link to the full CSV.

The Per-segment, Timing, and Config-provenance sections are
unchanged.

### 3. ``compare_reports.csv`` gains an ``output_files`` section

New section in the 3-column long-format CSV mapping each metric to
its run-stamped output filename:

```csv
output_files,records_matched,matches_202605290401.dat
output_files,records_mismatched,mismatches_202605290401.dat
output_files,keys_in_a_only,keymismatch_A_202605290401.dat
output_files,keys_in_b_only,keymismatch_B_202605290401.dat
output_files,dups_in_a,dups_A_202605290401.dat
output_files,dups_in_b,dups_B_202605290401.dat
output_files,report,report_202605290401.csv
output_files,summary,summary_202605290401.json
output_files,keys_mismatch_matrix,keys_mismatch_matrix_202605290401.csv
```

Spreadsheet users can pivot on the section to navigate from a
metric to its source file without rereading the docs.

### Engine wiring

- New :class:`KeyMatrixEntry` dataclass + ``build_key_matrix_entry``
  helper in ``writer.py``.
- New :class:`CompareReports` dataclass bundling
  ``summary + layout_a + layout_b + key_matrix_entries +
  matrix_segments + output_dir``. The report-writing functions take
  this bundle, so adding a new metric only touches ``Summary`` and
  the writers — no signature churn at the engine call sites beyond
  the bundle construction.
- ``OutputWriter.finalize(reports)`` writes all four report files
  (summary.json + the three human reports) in one step.
- ``WorkerResult`` gains ``key_matrix_entries: tuple[KeyMatrixEntry, ...]``.
- ``pipeline.run_parallel`` master folds per-worker entries by
  concatenation in worker-id order and writes the full matrix file +
  the HTML/CSV reports after the per-record merge completes.
- The parallel master's pre-cleanup list now includes
  ``keys_mismatch_matrix.csv`` so a worker crash doesn't leave a
  stale matrix from a previous run in place.

### Output count per run

Goes from 10 → 11 (added: ``keys_mismatch_matrix.csv``):
``matches.dat``, ``mismatches.dat``, ``keymismatch_A.dat``,
``keymismatch_B.dat``, ``dups_A.dat``, ``dups_B.dat``,
``report.csv``, ``summary.json``, ``compare_reports.csv``,
``compare_reports.html``, ``keys_mismatch_matrix.csv``.

### Memory cost

The matrix entries buffer in memory during the run. At the 3M-record
target with ~10% mismatch rate, that's ~300K entries × ~200 bytes
each ≈ 60 MB — within budget. If a future workload pushes this past
RAM, switch to per-worker file streaming (workers write CSV slices
to disk, master concatenates with header inserted once); the schema
doesn't need to change.

### Consequences

- Operators get key-level triage data without scripting against
  ``mismatches.dat``.
- The HTML is now a one-stop "what happened in this run" page: open
  it in a browser, see the layouts, the aggregate counts (linked),
  the per-segment breakdown, and a sample of which keys went wrong.
- The matrix is mismatch-only by design. If a future need wants
  "every joined key with its Y/N matrix even if fully matched",
  that's a separate file (proposed name:
  ``keys_full_matrix.csv``) since the size could grow to the full
  inner-join count.
- ``OutputWriter.finalize``'s signature changed from
  ``finalize(summary)`` to ``finalize(reports)``. Only the pipeline
  modules and tests call it; updated everywhere.
- 13 new writer tests cover: matrix header, matrix row content,
  matrix-empty-when-no-mismatches, HTML layouts section,
  HTML side-by-side rendering, HTML aggregate-count file links, HTML
  per-key sample with link to full file, CSV ``output_files``
  section. Plus a parallel-pipeline test asserting the parallel
  master emits the matrix with the right rows.
- ``ADR-035`` is extended (HTML structure now richer); the CSV
  long-format contract is unchanged except for the additive
  ``output_files`` section.

---

## ADR-037 — Per-run output subdirectory; bare filenames inside

**Status:** accepted; supersedes ADR-027 on filename stamping

**Context:** ADR-027 disambiguated successive runs by injecting a
``YYYYMMDDHHMM`` stamp into every output filename
(``matches_202605290428.dat``, ``summary_202605290428.json``, etc.).
That worked but flattened multiple runs into one directory, made
``ls`` output noisy, and ran two runs in the same minute into
collisions.

**Decision:** Each invocation of ``pipeline.run`` /
``pipeline.run_parallel`` creates its own subdirectory under
``--output-dir``:

```
results/
└── report-2026-05-29-04-28-15/
    ├── matches.dat
    ├── mismatches.dat
    ├── keymismatch_A.dat
    ├── keymismatch_B.dat
    ├── dups_A.dat
    ├── dups_B.dat
    ├── report.csv
    ├── summary.json
    ├── compare_reports.csv
    ├── compare_reports.html
    └── keys_mismatch_matrix.csv
```

- Subdirectory name format: ``report-%Y-%m-%d-%H-%M-%S`` (UTC).
  Seconds resolution removes ADR-027's same-minute collision risk.
- Files inside use **bare** names. The subdirectory provides the
  disambiguation; embedding the stamp in filenames as well would be
  redundant.
- ``Summary.filename_stamp`` (the field name is retained for
  backward compatibility of ``summary.json``'s schema) carries the
  subdirectory name. So ``filename_stamp`` is now
  ``"report-2026-05-29-04-28-15"`` rather than
  ``"202605290428"``.
- The HTML report's clickable file links and the CSV's
  ``output_files`` section reference bare names; both files live in
  the same per-run directory so relative links resolve naturally.
- The parallel ``_workers/`` scratch tree lives inside the per-run
  directory too, keeping a single run self-contained on disk.

**Consequences:**

- ``ls results/`` shows one directory per run instead of N×11 files.
- Two consecutive runs are guaranteed not to collide even within the
  same minute.
- Wired through ``pipeline.run`` and ``pipeline.run_parallel``;
  ``OutputWriter`` retains its ``filename_stamp`` parameter for tests
  that exercise it directly, but the pipeline now passes ``""``.
- All test helpers updated: ``tests/_helpers.py`` exposes a
  ``run_dir_for(ts)`` helper; pipeline / external_sort / parallel /
  main tests reach into ``out / FIXED_RUN_DIR / base`` instead of the
  old ``out / matches_<stamp>.dat``.
- ADR-027 is superseded on the filename-stamping rule. The 12-char
  stamp format ``%Y%m%d%H%M`` still exists in the codebase but is
  only used internally for the external-sort temp files in
  ``runtime.sort_temp_dir``, which sit outside the per-run output
  directory.

---

## ADR-038 — ``matches.dat`` is a sample; ``mismatches.dat`` stays full

**Status:** accepted

**Context:** Operators reviewing run outputs want quick spot-checks
of what matched in addition to full diagnostic detail on what did
not. At 3M records per file, a fully matched run produces a
``matches.dat`` of ~1 GB — too large to be useful as a spot-check
artifact, and providing nothing the per-segment / per-key reports
don't already summarize.

**Decision:** ``matches.dat`` is sampled; ``mismatches.dat`` is full.

- ``writer.MATCHES_SAMPLE_SIZE = 10`` caps the number of matched
  records written to ``matches.dat``. Aggregate counts
  (``Summary.records_matched``, the per-segment match counts, the
  HTML's "Records matched" cell) continue to reflect the **true**
  number of matched records.
- Single-process: the ``write_match`` call is gated on
  ``records_matched < MATCHES_SAMPLE_SIZE``; the counter still
  increments unconditionally.
- Parallel: each worker writes every match it sees into its per-
  worker ``matches.dat`` (workers don't coordinate). The master
  process post-merges and truncates the concatenated file to
  ``MATCHES_SAMPLE_SIZE`` records using the configured record
  delimiter as boundary. Truncation is a no-op when the delimiter
  is empty (back-to-back records) or when the file is already at
  or below the cap.
- ``mismatches.dat`` carries every mismatched record (side-by-side
  diagnostic blocks) — unchanged from before.

**Consequences:**

- ``matches.dat`` stays small regardless of input size.
- The aggregate ``records_matched`` count is still honest for
  downstream tooling; only the on-disk artifact is truncated.
- Future config knob (deferred): make the cap a per-run setting in
  ``runtime.json`` or a CLI flag (``--matches-sample N``,
  ``--mismatches-sample N``). For now it's a constant.
- The parallel and single-process paths still produce byte-identical
  ``matches.dat`` (both end up at ≤ 10 records). The pre-existing
  ``test_parallel_output_matches_single_process`` parametric test
  continues to enforce that.
- One new pipeline test pins the cap: generate 15 identical records,
  assert ``records_matched == 15`` and the on-disk file contains
  exactly 10 records.

---

## ADR-033 — Single per-file layout config replaces segments.json + normalization.json

**Status:** accepted (Stage 1: schema landed); supersedes **ADR-007**
(position-vs-field normalization split), **ADR-008** (per-segment
normalization rules), and **ADR-029** (field-based normalization with
position fallback). Stages 2 and 3 land the loader and engine cutover.

**Context:** The user described the existing two-file config
(`segments.json` for the catalog/parser knobs + `normalization.json`
for per-segment strip/exclude rules) as too low-level for humans
operating real-world feeds. The mental model an operator actually
holds is *"this file contains these segments, each carrying these
fields"* — not *"strip byte range [11, 19) from File A's CL01."*
Splitting framing knobs across two files also forced cross-file
reasoning for any layout change.

Five concrete pain points motivated the redesign:

1. The global `key_range` in `segments.json` assumed File A and File B
   put the record key at the *same* physical position inside TU4R.
   Real feeds may not.
2. Position-based normalization (`file_a_strip`, `exclude_positions`)
   required hand-counting byte offsets every time field widths changed.
3. There was no place to declare that File A is sorted while File B
   isn't — `runtime.input_sorted` was global.
4. RDW + per-file parser knobs (ADR-031) were starting to grow per-file
   sections inside the global parser block, foreshadowing more
   per-file divergence.
5. New layouts required onboarding two files (`segments.json` +
   `normalization.json`); easy to forget either.

**Decision:** Replace `config/segments.json` and `config/normalization.json`
with two per-file layout files:

- `config/layout_file_A.json`
- `config/layout_file_B.json`

Each file declares everything that is specific to its input: byte-level
parser knobs, optional `strip_leading_bytes`, optional `rdw`, sort
order, and an ordered list of segments with per-segment `size` and
per-field `name`/`length`/`exclude`/`key` flags. `config/runtime.json`
keeps its run-wide knobs (`hash_method`, `parallel_workers`,
`sort_temp_dir`, `chunk_size`, `partition_strategy`); the file-specific
sort knobs (`input_sorted`, `key_type`, `key_sort_order`) migrate into
the per-file `sort` block.

### Canonical schema

```jsonc
{
  "file_format": {
    "segment_name_bytes":   4,
    "size_field_bytes":     3,
    "size_encoding":        "ascii_int",
    "size_includes_header": true,
    "data_encoding":        "ascii",
    "record_delimiter":     "\n"
  },

  "strip_leading_bytes": null,          // OR { "size": N, "encoding": "binary"|"ascii" } — consumed per record before RDW
  "rdw":                 null,          // OR { "rdw1_bytes": N, "rdw2_bytes": M, "encoding": "binary_le_uint"|"ascii_int" }

  "sort": {
    "input_sorted": true,
    "order":        "ascending",
    "key_type":     "alphanumeric"
  },

  "segments": [
    { "name": "TU4R", "role": "key", "size": 30, "fields": [
        { "name": "data_prefix",   "length": 4,  "exclude": true },
        { "name": "account_nbr",   "length": 12, "key":     true },
        { "name": "source_branch", "length": 7                   }
    ]},
    /* ... more segments ... */
    { "name": "ENDS", "role": "end", "size": 10, "fields": [
        { "name": "segment_count", "length": 3, "exclude": true  }
    ]}
  ]
}
```

### Key design rules

1. **Default `exclude: false`.** Every field is compared by default;
   excluded fields must be flagged explicitly. Filler / padding /
   timestamp / segment-count bytes get an explicit named entry with
   `exclude: true` so the layout double-documents what every byte
   represents.
2. **Field-name = comparison anchor.** Fields with the same name in
   File A and File B compare regardless of physical order. Fields
   named in only one file drop from that segment's comparison (the
   same equivalence relation as ADR-029's `FieldNormalizer`).
3. **Per-segment `size` required and validated at load.** Invariant:
   `size == header_bytes + sum(field.length for field in fields)`
   where `header_bytes = segment_name_bytes + size_field_bytes`.
   Catches field-length typos the moment the JSON is saved.
4. **Exactly one segment with `role: "key"` and exactly one with
   `role: "end"`** — role-marked so reordering the `segments` array
   for readability cannot accidentally re-frame the record.
5. **Exactly one field with `key: true`**, which must live inside the
   role:key segment. That field's value (extracted by accumulating the
   preceding field lengths) becomes the record key. **The global
   `key_range` config disappears**; the key location is per-file by
   construction.
6. **`repeats` is intentionally absent.** Segment cardinality is
   discovered at parse time and handled by multiset comparison; any
   declared cardinality would be informational only and risks
   misleading readers into thinking it's enforced. Segment list order
   in `segments[]` is also documentation-only — the parser is
   order-agnostic for non-role segments.
7. **`strip_leading_bytes` is per-record** (same lifecycle as RDW),
   not per-file. Order on the wire becomes
   `[strip_leading_bytes][rdw][TU4R]…[ENDS][delimiter]`. `encoding`
   is informational (skip is byte-count-driven); `null` = no skip.
8. **`sort` is per-file** so File A can be pre-sorted while File B
   needs the external-sort pass.

### Load-time invariants (all raise ConfigError)

- File-level: `file_format`, `sort`, and `segments` present;
  `segments` non-empty.
- Roles: exactly one `role: "key"`, exactly one `role: "end"`,
  key segment not equal to end segment.
- Key field: exactly one `key: true` across all fields; it sits inside
  the role:key segment.
- Per-segment: `size > 0`, `size == header_bytes + sum(field.length)`,
  every `field.length > 0`, field names unique within the segment.
- `strip_leading_bytes` (if present): `size > 0`, encoding in
  `{binary, ascii}`.
- `rdw` (if present): both `rdw{1,2}_bytes > 0`, encoding in
  `{ascii_int, binary_le_uint}`.
- `sort`: `input_sorted` boolean; `order` in `{ascending, descending}`;
  `key_type` in `{alphanumeric, numeric}`.

### Migration intent

This ADR is split across three stages so the human-facing artifact
lands before any engine churn:

- **Stage 1 (this commit):** sample `config/layout_file_A.json` and
  `config/layout_file_B.json` describe the existing
  `examples/sample_a.dat` / `sample_b.dat` realistic fixture exactly.
  ADR captures the design. **No engine code changes; the suite still
  passes via the existing loader.**
- **Stage 2:** add `FileLayout` / `SegmentLayout` / `FieldLayout` /
  `StripConfig` dataclasses and `load_file_layout()`. Old loader
  untouched. New loader gets its own test file.
- **Stage 3:** engine cuts over. Pipeline, normalizer, worker,
  external_sort consume `FileLayout`. `config/segments.json` and
  `config/normalization.json` are deleted. Position-based
  normalization (`file_a_strip`, `exclude_positions`,
  `PositionNormalizer`, `NormalizationRule`) is removed entirely —
  field-based is the only form. All 212 tests migrated. CLI
  `--config-dir` semantics: directory now expected to contain
  `layout_file_A.json` + `layout_file_B.json` + `runtime.json`.

**Consequences:**

- Operators describe a feed by walking its actual byte layout once.
  Future feeds = one new layout file, no normalization-rule
  cross-reference.
- The position-based form goes away. The two integrations the engine
  exercises today (`test_field_integration.py` for field-based;
  the position-based path for everything else) collapse into one
  field-based code path under `FileLayout`.
- ADR-016's "pluggable parser knobs from day one" idea matures into
  per-file parser blocks. Most knobs become per-file (real feeds may
  diverge); the run-wide ones (hash method, worker count, sort temp
  dir, chunk size, partition strategy) stay in `runtime.json`.
- ADRs 007, 008, and 029 are superseded once Stage 3 lands. ADR-031
  (per-file RDW) is absorbed: the `rdw` block now sits inside each
  layout file rather than as a sibling under `segments.json::parser`,
  but the on-wire contract is unchanged.
- The audit hash (ADR-017) continues to identify the config bundle;
  in Stage 3 it covers `layout_file_A.json`, `layout_file_B.json`,
  and `runtime.json`.
- A future "validate cardinality" feature (e.g., enforce that every
  record has exactly one NM01) would slot in as a per-segment
  optional `cardinality` field, not by re-introducing `repeats`.

---

## ADR-039 — Segment aliases in the Web UI + demo fixture

**Status:** accepted; extends ADR-034 (engine) and ADR-033 (per-file layout)

**Context:** ADR-034 shipped the engine capability (`segment_aliases`
on a layout file) but only the CLI / hand-written layouts could use it.
The Phase 3 Web UI — the API wire schema (`api/models.py`), the
UI→engine projection (`api/storage.py::_build_engine_layout`), and the
Vue segment editor — ignored `segment_aliases` entirely. Operators had
no way to declare "treat AD01-after-EM01 as EMAD" from the browser, and
the committed demo fixture (`examples/sample_*.dat`) contained no
`AD01`/`EM01` segments to exercise it.

**Decision:**

1. **UI surface.** The operator declares an alias segment in the segment
   list: a logical name (e.g. `EMAD`), the wire segment whose layout it
   mirrors (e.g. `AD01`), and the trigger it follows (e.g. `EM01`). It
   renders as a read-only mirror card labelled "EMAD (AD01 segment) ·
   after EM01". Aliases also baked into a template layout render the same
   way (the template carries `alias_of`/`alias_after` metadata per
   logical segment).
   - `FileSideConfig.alias_segments: list[AliasSegmentDecl]` carries
     operator-declared rules; `TemplateLayout.segment_aliases` +
     `TemplateSegment.alias_of/alias_after` carry template-baked ones.
   - The projection clones the **wire** segment's resolved fields into
     the logical segment (guaranteeing the equal-size invariant the
     engine validator enforces) and emits a top-level `segment_aliases`
     array. Rules are deduped by `wire_name` (one rule per wire); a
     template rule wins over an operator rule for the same wire.

2. **Demo fixture.** `config/layout_file_*.json` now declare `AD01`,
   `EM01`, `EMAD` + the `AD01→EMAD after EM01` rule, and
   `examples/sample_*.dat` carry, after each record's `NM01`, an
   `AD01` (postal) + `EM01` + `AD01` (email) trio. The trailing `AD01`
   buckets as `EMAD`. The inserted bytes are **identical on both sides**,
   so every aggregate count (matched / mismatched / orphan / dup) is
   unchanged — only `summary.json::per_segment` gains `AD01` / `EM01` /
   `EMAD` entries. The fixture was rebuilt with
   `scripts/inject_alias_segments.py` (idempotent), not by hand.

**Consequences:**

- `AD01`'s field layout (`street`/`city`/`state`/`zip5` = 52 data, 59
  total) is aligned with the pre-existing `AD01` in
  `tests/synthetic_data.py`, so the 3M/50K benchmark generator validates
  cleanly against the committed config now that `AD01` is declared.
- Output `.dat` files still carry the on-wire `AD01` (the engine emits
  `record.raw`); only `summary.json::per_segment` and `report.csv` show
  `EMAD`. Unchanged from ADR-034.
- The UI prevents declaring two aliases for one wire (the engine
  validator rejects it); the projection enforces the same dedupe.
- A standalone, fully-commented example layout lives at
  `config/layout_example_segment_alias.json`.

---

## ADR-040 — Sample records in the HTML report + Results view; nav trim

**Status:** accepted; extends ADR-035/036 (reports) and the Phase 3 UI

**Context:** The HTML report already showed aggregate counts, the
per-segment breakdown, and a per-key Y/N mismatch matrix, but no actual
*record* examples. Operators wanted to eyeball a few concrete rows per
category without opening the raw `.dat` files: matched records, the
side-by-side File A / File B for mismatches, which keys duplicated (and
how many times), and which keys were orphaned. The Phase 3 dashboard
also carried speculative, non-functional nav tiles (Datasets, Settings,
About) and a stubbed Results tile.

**Decision:**

1. **Samples live in the report only.** A new ``RunSamples`` bundle
   (``RecordSample`` / ``MismatchSample`` / ``DupCount``) rides on
   ``CompareReports``; ``write_compare_reports_html`` renders a "Sample
   records" section from it. Caps: matches 5, mismatches 10, dups 10,
   orphans 10 (``*_SAMPLE_SIZE`` constants). These are illustrative —
   ``summary.json`` aggregates remain the source of truth.
2. **Population is mode-specific but identical in shape.** dups/orphans
   come from the master's in-memory dicts/sets in both pipeline paths.
   Match/mismatch samples are captured inline in the single-process loop
   and read back from the merged ``matches.dat`` / ``mismatches.dat`` in
   the parallel path (the master has no in-memory copy of worker output;
   the merged files are complete + closed by report time). Mirrors how
   ``key_matrix_entries`` are already handled per mode. Read-back is
   cheap because the caps are tiny.
3. **Per-key dup-count CSVs.** Two new full (not sampled) outputs —
   ``dups_A_count_report.csv`` / ``dups_B_count_report.csv`` (``key,count``,
   one row per duplicate key with its occurrence count in that file, sorted by
   key) — are written by ``write_dups_count_report`` in both pipeline paths and
   linked from the report's dups subsection alongside the ``dups_*.dat`` links.
   Output file count: 11 → 13.

4. **Segment aliases are NOT shown in the report.** The Layouts meta block no
   longer renders the alias rules (e.g. ``AD01→EMAD after EM01``) — aliasing is
   an internal backend concern; the report stays operator-facing.

5. **Results view.** The previously-stubbed "Results" tile becomes a real
   view: metric cards (reusing ``RunResultPanel``) + a per-segment table
   fetched from the run's ``summary.json`` + a prominent "Open report"
   link. The sample *tables* are not duplicated in the UI — they live in
   the report, keeping one source of truth. The last run is shared via a
   small ``composables/run.js`` singleton so navigating to Results
   doesn't lose it.
6. **Nav trim.** The non-functional "Datasets" tile and the entire
   bottom group ("Settings", "About") are removed from the sidebar.
   "Run History" stays as a disabled "soon" tile (deferred — it needs
   run persistence, out of scope here).

**Consequences:**

- The output *file* set is unchanged (still 11 files); only the HTML
  report gained a section. No new endpoint — Results fetches the
  existing ``/api/runs/{token}/summary.json``.
- Parallel and single-process reports show equivalent sample sections
  (asserted in ``tests/test_parallel.py``).
- A future "Run History" view (newest-N runs, capped at 10) would add a
  ``GET /api/runs`` list endpoint that scans the output dir or a
  manifest — recorded here as the deferred next step.

---

## ADR-041 — Run History is directory-driven; nav toggles via .env

**Status:** accepted; builds on ADR-037 (per-run subdirs) and ADR-040

**Context:** ADR-040 deferred Run History. When picked up, the first cut used a
small capped JSON manifest (``run_history.json``) updated on each run. Operator
feedback reframed it: history should reflect *"the latest 5 runs based on what
it sees in the [selected output] directory"* — i.e., driven by the directory,
not a side-channel manifest. Each run already lands in a
``report-YYYY-MM-DD-HH-MM-SS/`` subdir with a ``summary.json`` (ADR-037), so the
directory IS the history.

**Decision:**

1. **Directory-driven, zero stored state.** ``storage.scan_run_history(
   output_dir, limit=5)`` lists ``report-*`` subdirs (their timestamp names sort
   chronologically), takes the newest 5, and reads each ``summary.json`` for the
   headline metrics + file names. No manifest, no ``record_run`` — the manifest
   approach was removed before it shipped. ``GET /api/runs?output_dir=<path>``
   exposes it; missing/non-dir path → empty list.
2. **The 405 fix.** ``/api/runs`` previously had only ``POST``, so a ``GET`` to
   it returned *405 Method Not Allowed*. Adding the ``GET`` handler resolves it.
3. **UI.** The Run History view has an output-directory input + the existing
   dir Browse dialog; it lists the newest 5 there, with per-row "Results"
   (loads the run into the Results view via the shared ``lastRun``) and
   "Report" (opens that run's HTML). Cap = 5.
4. **Nav visibility via .env.** ``ui/.env`` exposes ``VITE_SHOW_RESULTS`` /
   ``VITE_SHOW_RUN_HISTORY`` (Vite build-time env). Both default **shown**
   (hidden only when explicitly ``=false``); ``AppSidebar`` filters the nav
   accordingly. Field Config is always shown.
5. **Report polish.** The HTML report's Layouts meta drops the "Layout file"
   row, and the "Config provenance" section is renamed "Run configs". (Segment
   aliases were already removed from the report per ADR-040.) The Run panel's
   "Pick files + a key field" hint tag was removed from the UI.

**Consequences:**

- Run History needs no cleanup/pruning logic and can never drift from disk —
  delete a ``report-*`` folder and it simply stops appearing. "Don't store more
  unnecessarily" is satisfied by storing *nothing*.
- ``config_name`` isn't in ``summary.json`` (the engine doesn't know the UI's
  config name), so history shows the input file names instead.
- Pointing the view at a different output directory shows that directory's
  runs — naturally multi-folder without any registry.

---

## ADR-042 — Sample records render as raw, copy-pasteable code blocks

**Status:** accepted; refines ADR-040

**Context:** ADR-040's "Sample records" section rendered matched/mismatched
records in HTML tables with wrapping cells. Operators wanted to *copy* a record
verbatim into a text editor to eyeball differences — wrapping table cells make
that painful (line breaks get pulled in, columns interleave).

**Decision:** Render the matched and mismatched samples as raw monospace code
blocks (``<pre class="sample-block">``), one record per line, **no wrapping**
(``white-space: pre`` + ``overflow-x: auto`` for horizontal scroll):

- Matched: ``<key>  <full record>`` — one line each.
- Mismatched: two lines per key — ``<key> | A | <record>`` then
  ``<key> | B | <record>`` — so copying the adjacent pair diffs cleanly.

The dups (key + count) and orphans (keys) subsections keep their compact table /
chip rendering — they're not full-record data. Styling is a `.sample-block`
class in the report's inline stylesheet (not an inline ``style`` attribute,
which broke on the quoted ``"JetBrains Mono"`` font name).

**Consequences:** select-all + copy from the block yields clean, one-record-
per-line text. Long records scroll horizontally instead of reflowing. The
full, untruncated data still lives in ``mismatches.dat`` (linked).

## ADR-043 — SQLite index for configs + full run history (alongside ADR-041)

**Status:** accepted; extends ADR-041 (directory-driven run history)

**Context:** ADR-041 made run history *directory-driven* with zero stored
state — `GET /api/runs?output_dir=` scans the newest five `report-*` subdirs of
one chosen directory and reads each `summary.json`. That is perfect for the Vue
`ui/` "what's in this folder" view, but the second UI (`ui2/`, ADR-044) needs a
**dashboard and full, searchable history** spanning *all* runs and aggregating
per-segment mismatch totals. Re-scanning arbitrary directories per request
doesn't give us cross-run aggregates or pagination, and the operator explicitly
asked for "some database within it."

**Decision:**

1. **SQLite as a queryable index, not a new source of truth.** A new
   `api/db.py` (stdlib `sqlite3`, **no new dependency**) maintains three tables:
   `runs` (headline metrics per run), `run_segments` (per-segment match/mismatch
   rollup), and `configs` (mirror of saved configs). The filesystem
   (`user_configs/`, `report-*/summary.json`) stays authoritative; the DB can be
   rebuilt from disk via `backfill_from_disk`.
2. **Dual-write, best-effort.** `POST /api/configs` and `POST /api/runs` call
   `db.record_config` / `db.record_run` after the existing filesystem work.
   Every DB write is wrapped so failures are logged and swallowed — the index
   being unavailable or corrupt must never break the core flow or the Vue `ui/`.
   `record_run` reads the run's `summary.json` (decoupled from the engine).
3. **New endpoints for `ui2`** (additive, no collision with existing routes):
   `GET /api/dashboard` (last run, recent runs, totals, mismatches-by-segment),
   `GET /api/history?limit=&offset=&q=` (paginated, searchable), and
   `GET /api/history/{id}` (run + per-segment detail). `GET /api/runs` (ADR-041)
   is untouched.
4. **Location + lifecycle.** DB path = `SEGCMP_DB_PATH` env or
   `./segment_compare.db`; WAL mode; schema created on app startup via the
   FastAPI lifespan. `*.db*` is gitignored.

**Consequences:** `ui2` gets cross-run aggregates and search without per-request
directory scans; the Vue `ui/` and all existing endpoints are unchanged; and
because the index is derived, a lost/corrupt DB is a non-event — delete it and it
re-seeds on the next run (or via backfill).

## ADR-044 — Second UI (`ui2/`, Next.js) alongside the Vue `ui/`

**Status:** accepted

**Context:** The Phase-3 Vue `ui/` (PrimeVue) covers config + run + results. The
operator asked for a *more visual* second UI: a dashboard with charts, an
easy-to-run comparator that shows every segment and its size with per-field
exclude checkboxes, plus history and config screens — without disturbing the
existing UI.

**Decision:**

1. **A new, parallel front-end in `ui2/`** (Next.js App Router + TypeScript +
   Tailwind + shadcn/ui, dark/light via `next-themes`, charts via Recharts). It
   is a *sibling* of `ui/`; both consume the same FastAPI `/api`. `ui/` is left
   entirely untouched.
2. **No browser CORS in dev:** `ui2` proxies `/api/*` to `:8000` via Next.js
   rewrites (mirroring Vite's proxy). `:3000` is also added to the backend CORS
   allow-list as a fallback for direct calls.
3. **Sidebar:** Dashboard · Field Comparator · History · Config.
4. **Field Comparator contract:** render **every** segment with its size, key
   segment (TU4R) first; each field gets an **exclude** checkbox that
   **defaults to false** (everything compared by default) — `ui2` sends explicit
   `exclude_overrides[seg.field]=false` for unchecked fields so template
   `exclude:true` defaults don't silently drop fields. The **key field shows no
   exclude control** (always included). "Add field" appends to the key segment
   (`added_fields`); the chosen `key_field_name` is promoted on save. Runs go
   through the existing `POST /api/configs` + `POST /api/runs`; dashboard/history
   are read from the ADR-043 index.

**Consequences:** two UIs to keep working against one API contract; `ui2` is the
visual/dashboard surface, `ui/` remains the reference implementation. Long runs
still block (no SSE) — `ui2` shows a spinner, matching current behavior.

## ADR-045 — Multi-user auth, admin user management, and per-user isolation (Phase 7)

**Status:** accepted (design); implementation deferred to Phase 7

**Context:** The operator wants the tool usable by **multiple concurrent users
on a single Linux host**, each running their own compares. Today the FastAPI
backend has no auth, saved configs are a flat global `user_configs/` namespace,
and the SQLite `runs`/`configs` tables (ADR-043) are global — so any caller sees
and overwrites everyone's work. The ask: per-user login, a *single* admin-only
page to create users + issue/reset passwords, a forced password change on first
login, and **no RBAC beyond user/admin**. Kept intentionally light.

**Decision:**

1. **Cookie-based server sessions, not JWT.** A `sessions` table in `api/db.py`
   holds opaque session ids → user; the browser gets an `httpOnly`+`Secure`+
   `SameSite=Lax` cookie. Simpler to revoke, nothing sensitive in the browser,
   and — stored in SQLite — it is **shared across gunicorn workers** (in-memory
   sessions would not be). Passwords are **bcrypt** hashes (`passlib[bcrypt]`,
   the one new dependency; the engine stays stdlib-only). JWT was rejected:
   refresh/revocation/XSS overhead for no benefit at this scale.
2. **Login lives only in `ui2/` (Next.js).** The Vue `ui/` is legacy (ADR-044)
   and is **not** updated for auth; it stops working against the authed API.
3. **A single admin-only page.** Admin endpoints (`/api/admin/users…`) let an
   admin create a user (server **generates** the initial password, shown once),
   reset/regenerate it (sets `must_change_password`), and enable/disable. The
   `ui2` `/admin` route + sidebar entry are gated on `is_admin`; non-admins get
   403 and never see it. The **first admin is env-seeded**
   (`SEGCMP_ADMIN_USER`/`SEGCMP_ADMIN_PASSWORD`) on startup with
   `must_change_password=true`. No heavier admin console.
4. **Forced first-login password change.** `must_change_password` blocks every
   endpoint except `auth/me` / `auth/change-password` / `auth/logout` until the
   user sets their own password.
5. **Per-user isolation (login alone is insufficient).** Configs are namespaced
   `user_configs/<username>/<config>/`; `runs`/`configs` gain a `user_id`; every
   read filters by the logged-in user and every write stamps the owner.
   `backfill_from_disk` assigns legacy rows to a default owner.
6. **Typed server paths are kept (trusted-user model).** No upload/sandbox work
   this phase — `file_a`/`file_b`/`output_dir` and `/api/browse` remain operator-
   typed absolute paths (now auth-gated). This assumes **all logged-in users are
   trusted on the host**; a future phase can add per-user upload + a managed
   results root if untrusted users are introduced.

**Consequences:** the API gains an auth layer and the DB gains `users`/`sessions`
tables + ownership columns; `ui2` gains login, change-password, and a one-page
admin screen, while `ui/` is left behind. Concurrency at the hosting layer is
gunicorn-workers-behind-nginx with TLS (required for `Secure` cookies); runs stay
synchronous and block a worker, so worker count must be sized for expected
concurrent runs. The trusted-path model is a deliberate, documented limitation,
not an oversight. Full plan in [docs/phase-7.md](phase-7.md).
