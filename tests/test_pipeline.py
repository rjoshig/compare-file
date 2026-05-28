"""Tests for ``segment_compare.pipeline``."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from segment_compare.config import load_config
from segment_compare.parser import iter_records
from segment_compare.pipeline import InputFileError, run
from segment_compare.writer import stamped_filename

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

# Deterministic stamp used by every synthetic test below so file paths are
# predictable. The realistic-sample test uses a different stamp on purpose.
FIXED_TS = datetime(2026, 5, 27, 22, 39, tzinfo=timezone.utc)
FIXED_STAMP = "202605272239"


def _stamped(out: Path, base: str, stamp: str = FIXED_STAMP) -> Path:
    return out / stamped_filename(base, stamp)


def _make_record(key: str, name_data: bytes = b"NAME_XYZ__") -> bytes:
    """One synthetic record matching the realistic config.

    Format: ``TU4R023`` (key at TU4R data [4, 16) per the new config) +
    ``NM01017`` + ``ENDS007``. Total 47 bytes on the wire (+ newline).
    """
    assert len(key) == 12, key
    assert len(name_data) == 10, name_data
    return b"TU4R023DATA" + key.encode() + b"NM01017" + name_data + b"ENDS007"


def _write_file(path: Path, records: list[bytes]) -> None:
    payload = b"\n".join(records) + b"\n"
    path.write_bytes(payload)


# ---------------------------------------------------------------------------
# Smoke test against committed sample files
# ---------------------------------------------------------------------------


def test_run_against_sample_files_matches_oracle(tmp_path: Path) -> None:
    """End-to-end run against examples/sample_*.dat hits the documented counts.

    Expected outcomes per examples/README.md (realistic fixture):
      - 4 matches (KEY...01, 02, 10, 11)
      - 3 mismatches (KEY...03 NM01, KEY...04 TR01 content, KEY...05 TR01 count)
      - 1 only in A (KEY...06)
      - 2 only in B (KEY...07, KEY...12)
      - 2 dups in A (both KEY...08)
      - 2 dups in B (both KEY...09)
      - 3 report.csv rows (NM01, TR01 content, TR01 count)
    """
    config = load_config(CONFIG_DIR)
    out = tmp_path / "results"
    summary = run(
        file_a=EXAMPLES / "sample_a.dat",
        file_b=EXAMPLES / "sample_b.dat",
        config=config,
        output_dir=out,
        run_timestamp=FIXED_TS,
    )

    assert summary.filename_stamp == FIXED_STAMP
    assert summary.file_a_record_count == 10
    assert summary.file_b_record_count == 11
    # A good_index keys: {01, 02, 03, 04, 05, 06, 10, 11} (08 dropped — dup)
    # B good_index keys: {01, 02, 03, 04, 05, 07, 10, 11, 12} (09 dropped — dup)
    # both = {01, 02, 03, 04, 05, 10, 11} = 7
    assert summary.keys_in_both == 7
    assert summary.keys_in_a_only == 1  # KEY...06
    assert summary.keys_in_b_only == 2  # KEY...07, KEY...12
    assert summary.dups_in_a == 2  # both occurrences of KEY...08
    assert summary.dups_in_b == 2  # both occurrences of KEY...09
    assert summary.records_matched == 4  # KEY...01, 02, 10 (after CL01 exclude), 11
    assert summary.records_mismatched == 3  # KEY...03, 04, 05

    matches = _stamped(out, "matches.dat").read_bytes()
    for k in (b"KEY000000001", b"KEY000000002", b"KEY000000010", b"KEY000000011"):
        assert k in matches, f"expected {k!r} in matches.dat"

    keymismatch_a = _stamped(out, "keymismatch_A.dat").read_bytes()
    keymismatch_b = _stamped(out, "keymismatch_B.dat").read_bytes()
    assert b"KEY000000006" in keymismatch_a
    assert b"KEY000000007" in keymismatch_b
    assert b"KEY000000012" in keymismatch_b

    mismatches = _stamped(out, "mismatches.dat").read_bytes()
    for k in (b"KEY000000003", b"KEY000000004", b"KEY000000005"):
        assert k in mismatches

    dups_a = _stamped(out, "dups_A.dat").read_bytes()
    dups_b = _stamped(out, "dups_B.dat").read_bytes()
    assert dups_a.count(b"KEY000000008") == 2
    assert dups_b.count(b"KEY000000009") == 2

    report = _stamped(out, "report.csv").read_text().splitlines()
    assert report[0] == "key,segment_name,status,a_count,b_count"
    # Exactly 3 mismatch rows. Order is by key (the engine processes joined
    # keys in sorted order), and within a record by segment order.
    rows = report[1:]
    assert len(rows) == 3
    assert "KEY000000003,NM01,content_diff" in rows[0]
    assert "KEY000000004,TR01,content_diff" in rows[1]
    assert "KEY000000005,TR01,count_diff,4,3" in rows[2]

    summary_data = json.loads(_stamped(out, "summary.json").read_text())
    assert summary_data["records_matched"] == 4
    assert summary_data["records_mismatched"] == 3
    assert summary_data["config_audit_hash"] == config.audit_hash
    assert summary_data["filename_stamp"] == FIXED_STAMP


# ---------------------------------------------------------------------------
# Synthetic scenarios using a fresh tmp_path
# ---------------------------------------------------------------------------


def test_all_matches_when_files_identical(tmp_path: Path) -> None:
    records = [
        _make_record("KEY000000001"),
        _make_record("KEY000000002"),
    ]
    _write_file(tmp_path / "a.dat", records)
    _write_file(tmp_path / "b.dat", records)

    summary = run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        load_config(CONFIG_DIR),
        tmp_path / "out",
        run_timestamp=FIXED_TS,
    )
    assert summary.records_matched == 2
    assert summary.records_mismatched == 0
    assert summary.keys_in_a_only == 0
    assert summary.keys_in_b_only == 0


def test_dup_keys_segregated_and_excluded_from_join(tmp_path: Path) -> None:
    """Duplicate keys in A go to dups_A and are excluded from the join."""
    a_records = [
        _make_record("KEY000000001"),
        _make_record("KEY000000002"),
        _make_record("KEY000000002"),  # duplicate
    ]
    b_records = [
        _make_record("KEY000000001"),
        _make_record("KEY000000002"),
    ]
    _write_file(tmp_path / "a.dat", a_records)
    _write_file(tmp_path / "b.dat", b_records)

    out = tmp_path / "out"
    summary = run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        load_config(CONFIG_DIR),
        out,
        run_timestamp=FIXED_TS,
    )
    assert summary.dups_in_a == 2  # both copies of KEY000000002 in A
    assert summary.dups_in_b == 0
    assert summary.keys_in_both == 1  # only KEY000000001 joins
    assert summary.records_matched == 1
    assert summary.keys_in_a_only == 0  # KEY000000002 in A is now a dup, not orphan
    # The orphan in B is KEY000000002 (its key did not survive in A's index)
    assert summary.keys_in_b_only == 1

    dups_a = _stamped(out, "dups_A.dat").read_bytes()
    assert dups_a.count(b"KEY000000002") == 2


def test_per_segment_counts_include_full_file_totals(tmp_path: Path) -> None:
    """total_in_a / total_in_b cover all records, even orphans and dups."""
    a_records = [
        _make_record("KEY000000001"),  # 1 TU4R + 1 NM01 + 1 ENDS
        _make_record("KEY000000002"),  # 1 TU4R + 1 NM01 + 1 ENDS
    ]
    b_records = [_make_record("KEY000000001")]
    _write_file(tmp_path / "a.dat", a_records)
    _write_file(tmp_path / "b.dat", b_records)

    summary = run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        load_config(CONFIG_DIR),
        tmp_path / "out",
        run_timestamp=FIXED_TS,
    )
    nm01 = next(s for s in summary.per_segment if s.segment_name == "NM01")
    assert nm01.total_in_a == 2
    assert nm01.total_in_b == 1


def test_summary_audit_hash_matches_config(tmp_path: Path) -> None:
    records = [_make_record("KEY000000001")]
    _write_file(tmp_path / "a.dat", records)
    _write_file(tmp_path / "b.dat", records)

    config = load_config(CONFIG_DIR)
    summary = run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        config,
        tmp_path / "out",
        run_timestamp=FIXED_TS,
    )
    assert summary.config_audit_hash == config.audit_hash
    assert summary.engine_version != ""


def test_missing_input_file_raises(tmp_path: Path) -> None:
    config = load_config(CONFIG_DIR)
    with pytest.raises(InputFileError):
        run(
            tmp_path / "nope_a.dat",
            tmp_path / "nope_b.dat",
            config,
            tmp_path / "out",
        )


def test_empty_files_produce_empty_outputs(tmp_path: Path) -> None:
    (tmp_path / "a.dat").write_bytes(b"")
    (tmp_path / "b.dat").write_bytes(b"")
    out = tmp_path / "out"
    summary = run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        load_config(CONFIG_DIR),
        out,
        run_timestamp=FIXED_TS,
    )
    assert summary.file_a_record_count == 0
    assert summary.file_b_record_count == 0
    assert summary.records_matched == 0
    assert summary.records_mismatched == 0
    assert summary.keys_in_a_only == 0
    assert summary.keys_in_b_only == 0
    assert _stamped(out, "matches.dat").read_bytes() == b""


def test_join_processes_keys_in_sorted_order(tmp_path: Path) -> None:
    """matches.dat reflects key order, not source-file order."""
    a_records = [_make_record(f"KEY00000000{i}") for i in (3, 1, 2)]
    b_records = [_make_record(f"KEY00000000{i}") for i in (3, 1, 2)]
    _write_file(tmp_path / "a.dat", a_records)
    _write_file(tmp_path / "b.dat", b_records)

    out = tmp_path / "out"
    run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        load_config(CONFIG_DIR),
        out,
        run_timestamp=FIXED_TS,
    )
    matches = _stamped(out, "matches.dat").read_bytes()
    # Records should appear in key order: KEY000000001, KEY000000002, KEY000000003
    positions = [matches.find(f"KEY00000000{i}".encode()) for i in (1, 2, 3)]
    assert positions == sorted(positions)
    assert all(p >= 0 for p in positions)


def test_output_records_are_parseable_round_trip(tmp_path: Path) -> None:
    """matches.dat content should re-parse as valid records."""
    config = load_config(CONFIG_DIR)
    records = [_make_record(f"KEY00000000{i}") for i in (1, 2, 3)]
    _write_file(tmp_path / "a.dat", records)
    _write_file(tmp_path / "b.dat", records)

    out = tmp_path / "out"
    run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        config,
        out,
        run_timestamp=FIXED_TS,
    )
    with _stamped(out, "matches.dat").open("rb") as fh:
        reparsed = list(iter_records(fh, config.parser, config.segments))
    assert [r.key for r in reparsed] == [
        "KEY000000001",
        "KEY000000002",
        "KEY000000003",
    ]


def test_single_record_with_multi_segment_mismatch_emits_multiple_report_rows(
    tmp_path: Path,
) -> None:
    """One record mismatching on N segment types → N rows in report.csv.

    Confirms the engine surfaces every mismatched segment type
    independently — important for downstream consumers that filter
    or group by segment_name. Builds a minimal record with TU4R +
    NM01 + TR01 + ENDS in both files, then makes A and B differ on
    BOTH NM01 (first name) AND TR01 (transaction marker). Expected
    output: 1 mismatched record, 2 rows in report.csv (one for NM01,
    one for TR01).
    """
    config = load_config(CONFIG_DIR)

    def _build(key: str, first_name: str, tr01_marker: str) -> bytes:
        # TU4R030 = 7 header + 4 "DATA" + 12 key + 7 trailer = 30
        tu4r = f"TU4R030DATA{key}POSNYC1".encode()
        # NM01057 = 7 header + 50 data (20 + 15 + 15)
        nm01 = f"NM01057{first_name:<20s}M              SMITH          ".encode()
        # TR01050 = 7 header + 27 prefix + 10 txnref + 6 fill = 50
        prefix = f"{tr01_marker}1111111  ABCBANK 2000 4000"
        assert len(prefix) == 27, f"prefix len {len(prefix)}"
        tr01 = f"TR01050{prefix}TXNREF0001      ".encode()
        # ENDS010 = 7 header + 3 data (segment count 004)
        ends = b"ENDS010004"
        record = tu4r + nm01 + tr01 + ends
        assert len(record) == 30 + 57 + 50 + 10, len(record)
        return record

    a = _build("KEY000000001", "ALICE", "A")
    b = _build("KEY000000001", "BOB", "B")
    (tmp_path / "a.dat").write_bytes(a + b"\n")
    (tmp_path / "b.dat").write_bytes(b + b"\n")

    out = tmp_path / "out"
    summary = run(
        file_a=tmp_path / "a.dat",
        file_b=tmp_path / "b.dat",
        config=config,
        output_dir=out,
        run_timestamp=FIXED_TS,
    )

    assert summary.records_matched == 0
    assert summary.records_mismatched == 1

    rows = _stamped(out, "report.csv").read_text().splitlines()
    assert rows[0] == "key,segment_name,status,a_count,b_count"
    body_rows = rows[1:]
    assert len(body_rows) == 2, f"expected 2 report rows, got: {body_rows}"

    # Both rows reference the same key and are content_diff (not count_diff).
    by_segment = {row.split(",")[1]: row for row in body_rows}
    assert set(by_segment) == {"NM01", "TR01"}
    assert "KEY000000001,NM01,content_diff,1,1" in body_rows
    assert "KEY000000001,TR01,content_diff,1,1" in body_rows


def test_default_timestamp_used_when_run_timestamp_omitted(tmp_path: Path) -> None:
    """When run_timestamp is not supplied, pipeline.run uses now() and stamps files."""
    records = [_make_record("KEY000000001")]
    _write_file(tmp_path / "a.dat", records)
    _write_file(tmp_path / "b.dat", records)
    out = tmp_path / "out"
    summary = run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        load_config(CONFIG_DIR),
        out,
    )
    # 12-digit YYYYMMDDHHMM stamp
    assert len(summary.filename_stamp) == 12
    assert summary.filename_stamp.isdigit()
    assert (out / f"matches_{summary.filename_stamp}.dat").exists()
