# Example sample files

Two hand-crafted sample input files used as the Phase 1 smoke test and
as living documentation of the file format. Both follow the format
described in `../docs/architecture.md`.

## File-level facts

| File | Size | Records |
|---|---|---|
| `sample_a.dat` | 176 bytes | 4 |
| `sample_b.dat` | 176 bytes | 4 |

Every record is exactly **44 bytes**:

```
TU4R 019 KEY000000001 NM01 017 NAME_ALICE ENDS 007 \n
└──┬─┘ └┬┘ └────┬─────┘ └─┬─┘ └┬┘ └────┬───┘ └┬─┘ └┬┘ └┬┘
  4    3      12          4    3     10        4   3   1   = 44
  name size   data        name size  data     name size delim
        (12-byte key)
```

So each record is:

- `TU4R` segment, declared size 019 (= 4-byte name + 3-byte size +
  12 bytes of data). The data is the 12-byte key.
- `NM01` segment, declared size 017 (= 4 + 3 + 10 bytes of data). The
  data is a 10-byte ASCII name field, padded with `_` to fixed width.
- `ENDS` segment, declared size 007 (header only, no data).
- A single `\n` record delimiter.

## Record-level facts

### `sample_a.dat`

```
TU4R019KEY000000001NM01017NAME_ALICEENDS007
TU4R019KEY000000002NM01017NAME_BOB__ENDS007
TU4R019KEY000000003NM01017NAME_CAROLENDS007
TU4R019KEY000000004NM01017NAME_DAVIDENDS007
```

### `sample_b.dat`

```
TU4R019KEY000000001NM01017NAME_ALICEENDS007
TU4R019KEY000000002NM01017NAME_BERT_ENDS007
TU4R019KEY000000004NM01017NAME_DAVIDENDS007
TU4R019KEY000000005NM01017NAME_EVE__ENDS007
```

## Expected comparison outcome

Running the Phase 1 engine against this pair with the stock
`config/` directory must produce these counts:

| Key | Outcome |
|---|---|
| `KEY000000001` | match (identical in both files) |
| `KEY000000002` | mismatch on `NM01` (`NAME_BOB__` vs `NAME_BERT_`) |
| `KEY000000003` | only in File A |
| `KEY000000004` | match (identical in both files) |
| `KEY000000005` | only in File B |

Resulting output files:

| File | Records | Bytes (approx) |
|---|---|---|
| `matches.dat` | 2 (`KEY000000001`, `KEY000000004`) | 88 |
| `mismatches.dat` | 1 record block (`KEY000000002`) | side-by-side, ~150+ |
| `keymismatch_A.dat` | 1 (`KEY000000003`) | 44 |
| `keymismatch_B.dat` | 1 (`KEY000000005`) | 44 |
| `dups_A.dat` | 0 | 0 |
| `dups_B.dat` | 0 | 0 |
| `report.csv` | 1 mismatch row (+ header) | small |
| `summary.json` | aggregates | small |

The Phase 1 integration test asserts these counts exactly.

## Why this design

- Keys are 12-character zero-padded ASCII (`KEY000000001` …) so sort
  order is unambiguous.
- One segment type beyond the key (`NM01`) keeps the example small but
  still exercises segment-type-aware comparison.
- All records have identical structure so byte-counting is easy when
  debugging the parser.
- A future commit can add larger / messier samples (repeating
  segments, multi-segment records, normalization scenarios) alongside
  these without affecting the smoke test.
