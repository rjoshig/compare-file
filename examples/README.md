# Sample files (Phase 1 oracle)

A production-shaped pair of sample files that exercises every Phase 1
comparison scenario in one shot. These are the **canonical Phase 1
test oracle** — the single integration test
`tests/test_pipeline.py::test_run_against_sample_files_matches_oracle`
asserts the counts documented below. Their layout is also the design
template for the Phase 2 synthetic 3M-record benchmark generator.

> An earlier simpler 4-record fixture
> (`TU4R019 + NM01017 + ENDS007`, ~44 bytes per record) was retired
> when this pair landed — see **ADR-026**.

## Record layout

Every record is a sequence of segments framed by `TU4R` (key segment)
and `ENDS` (terminator), with a `\n` record delimiter. All sizes are
ASCII 3-digit decimal in the segment header.

| # | Segment | Size | Data layout |
|---|---|---|---|
| 1 | `TU4R` | 030 | `DATA` (4) + 12-byte key + 7-byte source/branch tag |
| 2 | `SH01` | 035 | `01NYY020305` status block + 17 spaces padding |
| 3 | `NM01` | 057 | first name (20) + middle name (15) + last name (15) — space-padded |
| 4 | `TR01` | 050 | `Annnnnnn  ABCBANK ssss bbbb` 27-byte structured prefix + 10-byte TXNREF + 6 spaces |
| 5 | `TR01` | 050 | (repeating — typical record has 3; may differ between A and B) |
| 6 | `TR01` | 050 | |
| 7 | `SC01` | 034 | `+nnnnnnnnn` 10-byte code + 17 spaces padding |
| 8 | `SC01` | 034 | (repeating) |
| 9 | `CL01` | 067 | `PUBL  ABC. ` (11) + 8-byte timestamp (`YYYYMMDD`) + ` I` (2) + 39 spaces |
| 10 | `ENDS` | 010 | 3-byte ASCII segment count (informational only — excluded from comparison) |

Standard record (3 TR01s) = **417 bytes** + `\n` = 418 on the wire.
Record with 4 TR01s = **467 bytes** + `\n` = 468 on the wire.

### Key location

The 12-byte key lives at **TU4R data offset [4, 16)** — immediately
after the literal `"DATA"` prefix. (This differs from the simple
`examples/sample_*.dat` where the key starts at TU4R data offset 0;
the config will need a `key_range` update before this fixture can be
fed to the engine.)

### Comparison-irrelevant fields

Two pieces of data should be excluded by normalization so genuine
data differences aren't masked by metadata noise:

| Segment | Bytes to exclude | Why |
|---|---|---|
| `ENDS` | data bytes [0, 3) (segment count) | Count is reconstructible from the record; not a data field |
| `CL01` | data bytes [11, 19) (timestamp) | Timestamp differs across runs even when content is identical |

## File contents at a glance

### `sample_a.dat` — 10 records, 4230 bytes

| Line | Key | Name (NM01) | TR01s | CL01 ts | Scenario |
|---|---|---|---|---|---|
| 1 | `KEY000000001` | ALICE MARIE ANDERSON | T1, T2, T3 | 20250709 | match |
| 2 | `KEY000000002` | BOB R SMITH | T1, T2, T3 | 20250709 | multiset reorder match (B has T2, T3, T1) |
| 3 | `KEY000000003` | **ALICE** MARIE ANDERSON | T1, T2, T3 | 20250709 | NM01 mismatch (B has ALICIA) |
| 4 | `KEY000000004` | CAROL L DAVIS | **T1**, T2, T3 | 20250709 | TR01 content mismatch (B has T1_MOD starting "B1...") |
| 5 | `KEY000000005` | DAVID J WILSON | T1, T2, T3, **T4** | 20250709 | TR01 **count** mismatch (B has only 3) |
| 6 | `KEY000000006` | EVELYN K MARTINEZ | T1, T2, T3 | 20250709 | only in A |
| 7 | `KEY000000008` | GRACE E LEE | T1, T2, T3 | 20250709 | dup A (1/2) |
| 8 | `KEY000000008` | GRACE E LEE | T1, T2, T3 | 20250709 | dup A (2/2) |
| 9 | `KEY000000010` | IRENE S TAYLOR | T1, T2, T3 | **20250101** | CL01 timestamp differs vs B — matches after exclude |
| 10 | `KEY000000011` | JAMES T MOORE | T1, T2, T3 | 20250709 | match |

### `sample_b.dat` — 11 records, 4598 bytes

| Line | Key | Name (NM01) | TR01s | CL01 ts | Scenario |
|---|---|---|---|---|---|
| 1 | `KEY000000001` | ALICE MARIE ANDERSON | T1, T2, T3 | 20250709 | match |
| 2 | `KEY000000002` | BOB R SMITH | **T2, T3, T1** | 20250709 | multiset reorder match |
| 3 | `KEY000000003` | **ALICIA** MARIE ANDERSON | T1, T2, T3 | 20250709 | NM01 mismatch |
| 4 | `KEY000000004` | CAROL L DAVIS | **T1_MOD**, T2, T3 | 20250709 | TR01 content mismatch |
| 5 | `KEY000000005` | DAVID J WILSON | T1, T2, T3 | 20250709 | TR01 count mismatch |
| 6 | `KEY000000007` | FRANK D JOHNSON | T1, T2, T3 | 20250709 | only in B |
| 7 | `KEY000000009` | HENRY P BROWN | T1, T2, T3 | 20250709 | dup B (1/2) |
| 8 | `KEY000000009` | HENRY P BROWN | T1, T2, T3 | 20250709 | dup B (2/2) |
| 9 | `KEY000000010` | IRENE S TAYLOR | T1, T2, T3 | **20250709** | timestamp differs vs A — matches after exclude |
| 10 | `KEY000000011` | JAMES T MOORE | T1, T2, T3 | 20250709 | match |
| 11 | `KEY000000012` | KATHY U NELSON | T1, T2, T3 | 20250709 | extra only-in-B |

### TR01 instance reference

The same TR01 byte-strings recur across records, so multiset comparison
is meaningful. Each is exactly 50 bytes.

| Label | Prefix (27) | TXNREF (10) | Filler |
|---|---|---|---|
| `T1` | `A1111111  ABCBANK 2000 4000` | `TXNREF0001` | 6 spaces |
| `T2` | `A2222222  ABCBANK 2100 4100` | `TXNREF0002` | 6 spaces |
| `T3` | `A3333333  ABCBANK 2200 4200` | `TXNREF0003` | 6 spaces |
| `T4` | `A4444444  ABCBANK 2300 4300` | `TXNREF0004` | 6 spaces |
| `T1_MOD` | `B1111111  ABCBANK 2000 4000` | `TXNREF0001` | 6 spaces |

## Expected comparison outcome

Running the engine against this pair with the stock `config/`
directory must produce:

| Key | Outcome | Notes |
|---|---|---|
| `KEY000000001` | match | identical |
| `KEY000000002` | match | TR01s reordered; equal as multisets |
| `KEY000000003` | mismatch — NM01 content | ALICE vs ALICIA |
| `KEY000000004` | mismatch — TR01 content | one TR01 instance differs |
| `KEY000000005` | mismatch — TR01 count | 4 in A, 3 in B |
| `KEY000000006` | only in A | |
| `KEY000000007` | only in B | |
| `KEY000000008` | dup in A (2 rows) | excluded from join |
| `KEY000000009` | dup in B (2 rows) | excluded from join |
| `KEY000000010` | match after exclude | CL01 timestamp normalized out |
| `KEY000000011` | match | identical |
| `KEY000000012` | only in B | |

| Output file | Records | Notes |
|---|---|---|
| `matches.dat` | 4 | KEY...01, 02, 10, 11 |
| `mismatches.dat` | 3 blocks | KEY...03, 04, 05 |
| `keymismatch_A.dat` | 1 | KEY...06 |
| `keymismatch_B.dat` | 2 | KEY...07, 12 |
| `dups_A.dat` | 2 | both rows of KEY...08 |
| `dups_B.dat` | 2 | both rows of KEY...09 |
| `report.csv` | 3 rows + header | NM01 mismatch + TR01 content mismatch + TR01 count mismatch |
| `summary.json` | aggregates | as usual |

Output filenames are stamped with the run start time in UTC, e.g.
`matches_202605280358.dat`. See **ADR-027**.

## How the engine is wired for this fixture

The stock `config/` directory already targets this layout:

- `config/segments.json`
  - `known_segments` includes `SH01`, `CL01`, `TR01` along with
    `TU4R`, `NM01`, `SC01`, `ENDS` and the other reserved entries.
  - TU4R `key_range = [4, 16]` (the key starts after the literal
    4-byte `"DATA"` prefix in the TU4R data area).
- `config/normalization.json` excludes the two comparison-irrelevant
  fields:
  - `ENDS`: data bytes `[0, 3)` (the 3-byte segment count).
  - `CL01`: data bytes `[11, 19)` (the 8-byte `YYYYMMDD` timestamp).
- The parser handles `ENDS` with non-zero data without special-casing —
  it reads size from the header and treats `ENDS` as a terminator
  regardless of payload. Covered by
  `tests/test_parser.py::test_ends_with_non_zero_data_payload_is_parsed_correctly`.

These config changes are tracked separately — see the next session log
entry once they land.

## Why this design

- **Production-shaped**: every segment carries data that resembles
  what real fixed-format extracts look like (jurisdictions, names,
  trade lines, score codes, classifier metadata, timestamps).
- **One pair covers every scenario**: the same fixture exercises
  match, multiset-reorder match, single-segment content mismatch,
  repeating-segment content mismatch, repeating-segment count
  mismatch, key-only-A, key-only-B, dup A, dup B, and exclude-required
  match. No need to maintain ten separate fixture pairs.
- **Deterministic byte layout**: every record's segments end at fixed
  byte offsets, which keeps debugging easy when something doesn't
  parse.
- **Variable record length**: K005 in File A has an extra TR01, so the
  fixture proves the engine handles variable-length records (it must
  via the key→offset index design, not via fixed record size).
