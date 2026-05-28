# Phase 1 — Core engine (POC) — **COMPLETE**

**Goal:** prove the comparison technique end-to-end on a realistic
multi-segment fixture (10 records in File A, 11 in File B). Single
process. No performance tuning. Architectural seams already in place so
Phase 2 parallelism slots in without rewrites.

> **Note (closure):** The original plan called for a synthetic 10K
> generator + integration test. During phase-1 closure the user
> elected to substitute a hand-built production-shaped fixture
> (`examples/sample_a.dat` / `sample_b.dat`, 10 + 11 records) that
> covers all ten scenarios in one pair. See **ADR-026**. The 10K
> generator is deferred to a Phase 2 benchmarking deliverable.

## Acceptance criteria

1. ✅ CLI runs end-to-end against the sample files and produces all
   eight outputs:
   ```bash
   python -m segment_compare \
       --file-a examples/sample_a.dat \
       --file-b examples/sample_b.dat \
       --config-dir config/ \
       --output-dir results/
   ```
   Output files are timestamped `<base>_YYYYMMDDHHMM.<ext>` (UTC) per
   **ADR-027**.
2. ✅ The sample-data run matches the counts predicted in
   `examples/README.md` (4 matches, 3 mismatches, 1 only-A, 2 only-B,
   2 dups-A, 2 dups-B, 3 report.csv rows).
3. ✅ **(superseded)** A realistic fixture pair covers every scenario
   in §"Synthetic test scenarios" below in 10 + 11 records.
4. ✅ Integration test
   `tests/test_pipeline.py::test_run_against_sample_files_matches_oracle`
   runs that pair through `pipeline.run` and asserts the expected
   per-segment match/mismatch totals.
5. ✅ `black`, `flake8`, `mypy --strict` clean on `src/` and `tests/`
   under pyenv 3.12.7.
6. ✅ `pytest` green: 137 tests pass. Coverage threshold not measured
   this phase (deferred to Phase 2 benchmarking work).

## Module-by-module breakdown

### `src/segment_compare/parser.py`

- Public functions:
  - `iter_segments(stream, parser_cfg) -> Iterator[Segment]`
  - `iter_records(stream, parser_cfg, segments_cfg) -> Iterator[Record]`
- `Segment` and `Record` are `@dataclass(frozen=True, slots=True)`.
- `iter_segments` reads `segment_name_bytes + size_field_bytes` header,
  parses size per `size_encoding`, reads remaining data bytes, yields.
- `iter_records` groups segments between `TU4R` and `ENDS`, consumes the
  trailing `record_delimiter`, yields a record with its raw byte slice
  preserved (for output writers that copy bytes verbatim).
- Validates: starting segment is `TU4R`, terminator is `ENDS`, declared
  size doesn't exceed remaining stream.
- Raises `ParseError(offset, message)` on corruption.

Tests:
- Single-segment round-trip.
- Single-record round-trip.
- Multi-record stream with delimiters.
- Corruption: truncated header, size beyond EOF, missing `ENDS`, wrong
  starter, bad ASCII in size field.

### `src/segment_compare/normalizer.py`

- `class PositionNormalizer`:
  - `__init__(rules: dict[str, NormalizationRule])`
  - `normalize(segment_name: str, raw_data: bytes, source: Literal['A','B']) -> bytes`
- Applies file-specific strip first (drop the listed `[start, end)` byte
  ranges from `raw_data`), then applies exclude ranges (drop those byte
  ranges from the post-strip bytes), returns the resulting `bytes`.
- Range operations are implemented by building a list of `[start, end)`
  to **keep** (the complement of the union of drop ranges) and
  concatenating those slices. This handles overlapping/unsorted drops
  cleanly and runs in O(n) where n is the data length.

Tests:
- No-op rules → identity.
- Single strip range.
- Multiple non-contiguous strips.
- Exclude on top of strip.
- Overlapping ranges (must not crash; must dedupe).

### `src/segment_compare/hasher.py`

- `class Blake2bHasher(Hasher)` — `hash(data: bytes) -> bytes`.
- `class BuiltinHasher(Hasher)` — `hash(data: bytes) -> int`.
- Both implement a `Hasher` Protocol so the comparator is agnostic.

Tests: deterministic output, digest size, type-correct return.

### `src/segment_compare/comparator.py`

- `compare_records(record_a: Record, record_b: Record, normalizer, hasher) -> RecordVerdict`
- For each segment type that appears in either record:
  - Build `Counter[hash]` for A and for B.
  - If Counters are equal → segment type matches.
  - Else → segment type mismatches; record `(a_count, b_count)`.
- Record matches overall iff every segment type's Counter matches.

Tests:
- Identical records → match.
- Reordered repeating segments → match.
- One differing segment → mismatch on that type only.
- Count differs (3 vs 2 of same type) → mismatch.

### `src/segment_compare/writer.py`

- One class `OutputWriter(output_dir: Path)` that owns file handles for
  all eight outputs and exposes:
  - `write_match(record_a, record_b)` — writes A's bytes to `matches.dat`.
  - `write_mismatch(key, mismatched_segments, record_a, record_b)` —
    side-by-side block in `mismatches.dat` plus rows in `report.csv`.
  - `write_key_only_a(record_a)` / `write_key_only_b(record_b)`.
  - `write_dup_a(record_a)` / `write_dup_b(record_b)`.
  - `finalize(summary: Summary)` — writes `summary.json` and closes
    handles.

Tests: byte-for-byte output checks against fixtures.

### `src/segment_compare/config.py`

- `load_config(config_dir: Path) -> ResolvedConfig`:
  - Reads the three JSON files.
  - Validates (known segments referenced in normalization exist;
    `key_range` within `TU4R` data length; parser knobs are
    Phase-1-supported values).
  - Computes SHA-256 of the canonicalized (sorted-key) merged JSON.
- Raises `ConfigError(field, message)`.

Tests: missing file, bad JSON, unknown segment in normalization,
invalid key_range, audit-hash determinism.

### `src/segment_compare/pipeline.py`

- `run(file_a: Path, file_b: Path, config: ResolvedConfig, output_dir: Path) -> Summary`
- Sequence:
  1. Index-build pass over File A → `dict[key, (offset, length)]` +
     duplicate keys.
  2. Same for File B.
  3. Route duplicate-key records to `dups_A.dat` / `dups_B.dat`.
  4. Compute key sets: only_a, only_b, both.
  5. For each key in `both` (sorted), read both records, compare, write.
  6. For each key in `only_a` / `only_b`, write the original record.
  7. Compute and write summary.

Tests: small in-memory comparison harness that asserts counts and
output file contents.

### `src/segment_compare/__main__.py`

- `main(argv: list[str] | None = None) -> int` — argparse, calls
  `pipeline.run`, returns one of the published exit codes.
- Options: `--file-a`, `--file-b`, `--config-dir`, `--output-dir`,
  `--log-level`, `--dry-run`, `--validate-config`.

Tests: invoke `main` with sample-data args in a tmpdir; assert exit
code 0 (or 1 if there are mismatches, which there will be for the
sample).

### `tests/synthetic_data.py`

- `generate_pair(num_records: int, seed: int) -> tuple[Path, Path,
   ExpectedCounts]`
- Builds A and B files covering:
  - Perfect match.
  - Match with repeating segments in different order.
  - Mismatch on a single-occurrence segment (NM01).
  - Mismatch on a repeating segment.
  - Count mismatch (3 in A, 2 in B).
  - Key only in A.
  - Key only in B.
  - Duplicate key in A.
  - Duplicate key in B.
  - Record requiring strip/exclude normalization to match.
- Returns expected aggregate counts so the integration test can assert
  exact numbers.

## Synthetic test scenarios

| # | Scenario | Expected output |
|---|---|---|
| 1 | Identical records | matches.dat |
| 2 | Repeating segments reordered | matches.dat (multiset!) |
| 3 | Single-segment mismatch | mismatches.dat + 1 row in report.csv |
| 4 | Repeating-segment content mismatch | mismatches.dat + row |
| 5 | Repeating-segment count mismatch | mismatches.dat + row with a_count != b_count |
| 6 | Key only in A | keymismatch_A.dat |
| 7 | Key only in B | keymismatch_B.dat |
| 8 | Duplicate key in A | dups_A.dat |
| 9 | Duplicate key in B | dups_B.dat |
| 10 | Strip+exclude required to match | matches.dat |

## Ordered task list

Build modules in this order so each step exercises the previous:

1. `parser.py` + `tests/test_parser.py` — must be able to read
   `examples/sample_a.dat`.
2. `config.py` + `tests/test_config.py` — loads `config/*.json`.
3. `normalizer.py` + `tests/test_normalizer.py`.
4. `hasher.py` + `tests/test_hasher.py`.
5. `comparator.py` + `tests/test_comparator.py`.
6. `writer.py` + `tests/test_writer.py`.
7. `pipeline.py` + `tests/test_pipeline.py`.
8. `__main__.py` + integration test against `examples/sample_*.dat`.
9. `tests/synthetic_data.py` + a 10K-record integration test.

Each step lands as its own commit with passing tests, clean
`black`/`flake8`/`mypy`.

## Open questions to resolve during Phase 1

- Unknown segment name policy: log+skip vs raise. Default to log+skip
  and record the decision in `decisions.md` before locking it.
- CSV report granularity for content_diff with matching counts: do we
  emit one row per offending instance or one row per segment type? Pick
  in implementation; document the choice.
- Logging format: choose between stdlib default and a simple JSON-line
  formatter for machine consumption. Record in `decisions.md`.
