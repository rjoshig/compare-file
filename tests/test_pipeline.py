"""Tests for ``segment_compare.pipeline``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from segment_compare.config import load_config
from segment_compare.parser import iter_records
from segment_compare.pipeline import InputFileError, run

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _make_record(key: str, name_data: bytes = b"NAME_XYZ__") -> bytes:
    """One 44-byte record: TU4R019 + 12-byte key + NM01017 + 10-byte data + ENDS007."""
    assert len(key) == 12, key
    assert len(name_data) == 10, name_data
    return b"TU4R019" + key.encode() + b"NM01017" + name_data + b"ENDS007"


def _write_file(path: Path, records: list[bytes]) -> None:
    payload = b"\n".join(records) + b"\n"
    path.write_bytes(payload)


# ---------------------------------------------------------------------------
# Smoke test against committed sample files
# ---------------------------------------------------------------------------


def test_run_against_sample_files_matches_oracle(tmp_path: Path) -> None:
    """End-to-end run against examples/sample_*.dat hits the documented counts."""
    config = load_config(CONFIG_DIR)
    out = tmp_path / "results"
    summary = run(
        file_a=EXAMPLES / "sample_a.dat",
        file_b=EXAMPLES / "sample_b.dat",
        config=config,
        output_dir=out,
    )

    # Per examples/README.md
    assert summary.file_a_record_count == 4
    assert summary.file_b_record_count == 4
    assert summary.keys_in_both == 3
    assert summary.keys_in_a_only == 1
    assert summary.keys_in_b_only == 1
    assert summary.dups_in_a == 0
    assert summary.dups_in_b == 0
    assert summary.records_matched == 2
    assert summary.records_mismatched == 1

    matches = (out / "matches.dat").read_bytes()
    # 2 matched records × 44 bytes each
    assert len(matches) == 88
    assert b"KEY000000001" in matches
    assert b"KEY000000004" in matches

    keymismatch_a = (out / "keymismatch_A.dat").read_bytes()
    keymismatch_b = (out / "keymismatch_B.dat").read_bytes()
    assert b"KEY000000003" in keymismatch_a
    assert b"KEY000000005" in keymismatch_b

    mismatches = (out / "mismatches.dat").read_bytes()
    assert b"KEY000000002" in mismatches
    assert b"NM01" in mismatches

    assert (out / "dups_A.dat").read_bytes() == b""
    assert (out / "dups_B.dat").read_bytes() == b""

    report = (out / "report.csv").read_text().splitlines()
    assert report[0] == "key,segment_name,status,a_count,b_count"
    assert report[1] == "KEY000000002,NM01,content_diff,1,1"
    assert len(report) == 2

    summary_data = json.loads((out / "summary.json").read_text())
    assert summary_data["records_matched"] == 2
    assert summary_data["records_mismatched"] == 1
    assert summary_data["config_audit_hash"] == config.audit_hash


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

    summary = run(tmp_path / "a.dat", tmp_path / "b.dat", load_config(CONFIG_DIR), tmp_path / "out")
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
    summary = run(tmp_path / "a.dat", tmp_path / "b.dat", load_config(CONFIG_DIR), out)
    assert summary.dups_in_a == 2  # both copies of KEY000000002 in A
    assert summary.dups_in_b == 0
    assert summary.keys_in_both == 1  # only KEY000000001 joins
    assert summary.records_matched == 1
    assert summary.keys_in_a_only == 0  # KEY000000002 in A is now a dup, not orphan
    # The orphan in B is KEY000000002 (its key did not survive in A's index)
    assert summary.keys_in_b_only == 1

    dups_a = (out / "dups_A.dat").read_bytes()
    # 2 copies × 44 bytes per record + 2 trailing delimiters
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

    summary = run(tmp_path / "a.dat", tmp_path / "b.dat", load_config(CONFIG_DIR), tmp_path / "out")
    nm01 = next(s for s in summary.per_segment if s.segment_name == "NM01")
    assert nm01.total_in_a == 2
    assert nm01.total_in_b == 1


def test_summary_audit_hash_matches_config(tmp_path: Path) -> None:
    records = [_make_record("KEY000000001")]
    _write_file(tmp_path / "a.dat", records)
    _write_file(tmp_path / "b.dat", records)

    config = load_config(CONFIG_DIR)
    summary = run(tmp_path / "a.dat", tmp_path / "b.dat", config, tmp_path / "out")
    assert summary.config_audit_hash == config.audit_hash
    assert summary.engine_version != ""


def test_missing_input_file_raises(tmp_path: Path) -> None:
    config = load_config(CONFIG_DIR)
    with pytest.raises(InputFileError):
        run(tmp_path / "nope_a.dat", tmp_path / "nope_b.dat", config, tmp_path / "out")


def test_empty_files_produce_empty_outputs(tmp_path: Path) -> None:
    (tmp_path / "a.dat").write_bytes(b"")
    (tmp_path / "b.dat").write_bytes(b"")
    summary = run(tmp_path / "a.dat", tmp_path / "b.dat", load_config(CONFIG_DIR), tmp_path / "out")
    assert summary.file_a_record_count == 0
    assert summary.file_b_record_count == 0
    assert summary.records_matched == 0
    assert summary.records_mismatched == 0
    assert summary.keys_in_a_only == 0
    assert summary.keys_in_b_only == 0
    assert (tmp_path / "out" / "matches.dat").read_bytes() == b""


def test_join_processes_keys_in_sorted_order(tmp_path: Path) -> None:
    """matches.dat reflects key order, not source-file order."""
    # Write A with reversed order so join order would differ from input order
    a_records = [_make_record(f"KEY00000000{i}") for i in (3, 1, 2)]
    b_records = [_make_record(f"KEY00000000{i}") for i in (3, 1, 2)]
    _write_file(tmp_path / "a.dat", a_records)
    _write_file(tmp_path / "b.dat", b_records)

    out = tmp_path / "out"
    run(tmp_path / "a.dat", tmp_path / "b.dat", load_config(CONFIG_DIR), out)
    matches = (out / "matches.dat").read_bytes()
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
    run(tmp_path / "a.dat", tmp_path / "b.dat", config, out)
    with (out / "matches.dat").open("rb") as fh:
        reparsed = list(iter_records(fh, config.parser, config.segments))
    assert [r.key for r in reparsed] == [
        "KEY000000001",
        "KEY000000002",
        "KEY000000003",
    ]
