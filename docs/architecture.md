# Architecture

> **ADR-033 update**: the config now lives in two per-file layout files
> (``config/layout_file_A.json`` + ``config/layout_file_B.json``) plus
> ``config/runtime.json``. ``segments.json`` and ``normalization.json``
> no longer exist; field-name-based comparison is the only normalization
> form. Diagrams below show the legacy three-file shape and are kept
> for historical context — replace ``segments.json + normalization.json``
> with ``layout_file_A.json + layout_file_B.json`` mentally.

## High-level shape

```
                      +-------------------+
                      |       Configs     |
                      | layout_file_A.json|
                      | layout_file_B.json|
                      | runtime.json      |
                      +---------+---------+
                                |
                                v
   File A ----+         +-------------------+         +---- File B
              |         |                   |         |
              +-------->|     Pipeline      |<--------+
                        |  (engine library) |
                        +---------+---------+
                                  |
                  +---------------+---------------+
                  | matches.dat                    |
                  | mismatches.dat                 |
                  | keymismatch_A.dat              |
                  | keymismatch_B.dat              |
                  | dups_A.dat                     |
                  | dups_B.dat                     |
                  | report.csv                     |
                  | summary.json                   |
                  +--------------------------------+

       Entry points (all wrap the same engine):
         - CLI (Phase 1)        : src/segment_compare/__main__.py
         - FastAPI (Phase 3)    : src/segment_compare/api/
         - Service (Phase 4)    : src/segment_compare/service.py
```

## Modules

| Module | Responsibility |
|---|---|
| `parser.py` | Streaming segment reader. Iterates `(name, size, data)` tuples and groups them into records framed by `TU4R` and `ENDS`. |
| `normalizer.py` | Position-based strip + exclude (Phase 1). Field-based layout (Phase 2). Produces canonical segment bytes for hashing. |
| `hasher.py` | Hash a segment's normalized bytes. Default `blake2b` (16-byte digest), switchable to built-in `hash()`. |
| `comparator.py` | Multiset (`collections.Counter`) comparison per segment type within a key. Emits match/mismatch verdicts. |
| `writer.py` | Writes the 11 output files (matches sampled to 10, mismatches, keymismatch_A/B, dups_A/B, report.csv, summary.json, compare_reports.csv, compare_reports.html, keys_mismatch_matrix.csv). Each run lands in its own `report-…` subdir (ADR-037). |
| `pipeline.py` | Orchestrates: index-build → dup filter → inner-join iteration → compare → write. Single function `run(file_a, file_b, config, output_dir)`. |
| `config.py` | Loads and validates the three JSON config files. Computes a SHA-256 of the merged config for the run audit trail. |
| `__main__.py` | CLI (argparse), wraps `pipeline.run`. |
| `api/` | Phase 3 FastAPI app, wraps `pipeline.run`. |
| `service.py` | Phase 4 directory-watcher entry point, wraps `pipeline.run`. |

## Data flow per comparison

1. **Load + validate configs.** Merge into a single resolved config object;
   compute config SHA-256 (stored in `summary.json`).
2. **Index-build pass (per file).** Stream the file end-to-end; for every
   record record `(key, byte_offset, byte_length)`. While doing so, detect
   duplicate keys and route them to `dups_A.dat` / `dups_B.dat`. Build a
   dict `key → (offset, length)` for non-duplicate keys.
3. **Inner-join iteration.** Walk keys present in both dicts in sorted
   order (cheap because input is sorted by default; if not, sort the key
   list — Phase 2 may external-sort).
4. **For each joined key:**
   - Seek/read the full record bytes from File A and File B.
   - Parse each record into segments.
   - Normalize each segment per the rules for its type and source file.
   - Hash each normalized segment.
   - Group hashes per segment type into a `Counter` for A and for B.
   - Compare counters; emit match or mismatch verdicts per segment type.
5. **Write outputs** as verdicts arrive. Records-fully-matched go to
   `matches.dat`; records-with-mismatches go to `mismatches.dat` in
   side-by-side format; one row per segment-type-mismatch in `report.csv`.
6. **Keys only in A or only in B** (from the dict difference) get written
   to `keymismatch_A.dat` / `keymismatch_B.dat` by seeking back into the
   source files.
7. **Finalize `summary.json`** with counts, timings, throughput, config
   hash, file paths and sizes.

## Pluggability seams

Bake these in from Phase 1 so the engine evolves without rewrites:

1. **Parser knobs in `config/segments.json`** — segment-name length, size-
   field length, size encoding (ASCII int vs binary uint), whether size
   includes the header, data encoding. Phase 1 only honors the ASCII / 4-3 /
   includes-header defaults but the knobs exist in the config schema and
   the parser reads them.
2. **Comparator iterator interface** — `pipeline.run` consumes
   `Iterator[(key, record_bytes_a, record_bytes_b)]`. In Phase 1 a single
   process produces this iterator; in Phase 2 a `multiprocessing.Pool`
   produces it in parallel, with no change to downstream code.
3. **Hash strategy** — `hasher.py` exposes a `Hasher` protocol with two
   implementations (`Blake2bHasher`, `BuiltinHasher`); selection happens
   in `config.py` based on `runtime.json::hash_method`.
4. **Normalizer kind** — Phase 1 has a `PositionNormalizer`; Phase 2 adds
   `FieldNormalizer`. Both implement the same `Normalizer` protocol:
   `(segment_name, raw_data, source: A|B) -> canonical_bytes`.

## Engine-as-library boundary

The CLI (`__main__.py`), FastAPI app (`api/`), and service (`service.py`)
own *only*:

- argument parsing / request decoding
- progress reporting / response shaping
- side effects outside the comparison (email send, HTTP responses,
  filesystem watching)

They must not contain comparison logic. All comparison logic lives in
`pipeline.run` and the modules it calls.

## Concurrency model

- **Phase 1**: single process, single thread. Streaming I/O.
- **Phase 2**: process pool. Equal-count key-range partitioning. Each
  worker reads its own slice of both files (via seek), writes per-worker
  output files, and a final merge step concatenates per-worker outputs in
  key order.
- **Phase 3 (UI)**: the FastAPI process runs comparisons in a worker pool
  or background task queue (TBD in `docs/phase-3.md`).
- **Phase 4 (service)**: lock-file ensures only one service invocation
  scans the pending directory at a time. Within an invocation, comparisons
  run serially.

## Failure modes and how they're surfaced

| Failure | Surfaced as |
|---|---|
| Bad config | `config.py` raises `ConfigError`; CLI exits 10. |
| Missing input file | `pipeline.py` raises `InputFileError`; CLI exits 11. |
| Corrupt segment (size beyond EOF, bad ENDS placement) | `parser.py` raises `ParseError` with file offset; CLI exits 20. |
| Output write failure | `writer.py` raises `WriteError`; CLI exits 12. |
| Duplicate keys | Not a failure — recorded in `dups_A.dat` / `dups_B.dat` and counted in `summary.json`. |
| Unknown segment name | Configurable: log + skip (default) or `ParseError`. Decision recorded in `decisions.md`. |

## Reproducibility

Every run writes `summary.json` containing:

- absolute paths and sizes of File A, File B
- SHA-256 of the merged config bundle
- the resolved config paths used (per file)
- start/end timestamps, elapsed seconds, throughput
- engine version (`segment_compare.__version__`)

Given the same inputs and the same config hash, two runs produce
identical outputs (modulo timestamps).
