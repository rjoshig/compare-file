"""Integration tests for the ``segment_compare.__main__`` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from segment_compare.__main__ import (
    EXIT_CONFIG_ERROR,
    EXIT_INPUT_NOT_FOUND,
    EXIT_MISMATCHES,
    EXIT_OK,
    EXIT_WARNINGS,
    main,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
EXAMPLES = REPO_ROOT / "examples"
SAMPLE_A = EXAMPLES / "sample_a.dat"
SAMPLE_B = EXAMPLES / "sample_b.dat"


def _make_record(key: str, name_data: bytes = b"NAME_XYZ__") -> bytes:
    """Synthetic record matching the realistic config (key at TU4R [4,16))."""
    assert len(key) == 12
    assert len(name_data) == 10
    return b"TU4R023DATA" + key.encode() + b"NM01017" + name_data + b"ENDS007"


def _stamped_path(out_dir: Path, base: str) -> Path:
    """Find the single timestamped output file in ``out_dir`` for ``base``."""
    stem, _, ext = base.rpartition(".")
    matches = sorted(out_dir.glob(f"{stem}_*.{ext}"))
    assert len(matches) == 1, f"expected 1 file for {base}, found: {matches}"
    return matches[0]


def _common_args(output_dir: Path) -> list[str]:
    return [
        "--file-a",
        str(SAMPLE_A),
        "--file-b",
        str(SAMPLE_B),
        "--config-dir",
        str(CONFIG_DIR),
        "--output-dir",
        str(output_dir),
    ]


def test_main_against_samples_produces_all_outputs_and_returns_mismatches(
    tmp_path: Path,
) -> None:
    """Phase 1 acceptance criterion #1: the CLI runs end-to-end on the samples."""
    out = tmp_path / "results"
    code = main(_common_args(out))

    # Samples have mismatches → exit 1 (mismatch beats orphans/dups for exit code).
    assert code == EXIT_MISMATCHES

    # All eight outputs exist with a timestamped suffix.
    for name in (
        "matches.dat",
        "mismatches.dat",
        "keymismatch_A.dat",
        "keymismatch_B.dat",
        "dups_A.dat",
        "dups_B.dat",
        "report.csv",
        "summary.json",
    ):
        path = _stamped_path(out, name)
        assert path.exists(), f"missing output: {name}"
        # YYYYMMDDHHMM = 12 digits between stem and extension
        stem = path.stem
        stamp = stem.rsplit("_", 1)[-1]
        assert len(stamp) == 12 and stamp.isdigit(), f"bad stamp in {path.name}"

    summary = json.loads(_stamped_path(out, "summary.json").read_text())
    # Per examples/README.md
    assert summary["records_matched"] == 4
    assert summary["records_mismatched"] == 3
    assert summary["keys_in_a_only"] == 1
    assert summary["keys_in_b_only"] == 2
    assert summary["dups_in_a"] == 2
    assert summary["dups_in_b"] == 2


def test_main_all_matches_returns_ok(tmp_path: Path) -> None:
    """When A and B are byte-identical (and have no dups), exit code is 0."""
    a = tmp_path / "a.dat"
    b = tmp_path / "b.dat"
    # Fresh synthetic content (no dups, no orphans) → clean match.
    payload = b"\n".join([_make_record("KEY000000001"), _make_record("KEY000000002")]) + b"\n"
    a.write_bytes(payload)
    b.write_bytes(payload)
    out = tmp_path / "results"
    code = main(
        [
            "--file-a",
            str(a),
            "--file-b",
            str(b),
            "--config-dir",
            str(CONFIG_DIR),
            "--output-dir",
            str(out),
        ]
    )
    assert code == EXIT_OK


def test_main_orphans_only_returns_warnings(tmp_path: Path) -> None:
    """No mismatches but orphan keys → exit code 2."""
    a_file = tmp_path / "a.dat"
    b_file = tmp_path / "b.dat"
    # Different keys → all orphans, zero joined records.
    a_file.write_bytes(_make_record("KEY000000001") + b"\n")
    b_file.write_bytes(_make_record("KEY000000999") + b"\n")
    out = tmp_path / "results"
    code = main(
        [
            "--file-a",
            str(a_file),
            "--file-b",
            str(b_file),
            "--config-dir",
            str(CONFIG_DIR),
            "--output-dir",
            str(out),
        ]
    )
    assert code == EXIT_WARNINGS


def test_main_missing_input_returns_input_not_found(tmp_path: Path) -> None:
    out = tmp_path / "results"
    code = main(
        [
            "--file-a",
            str(tmp_path / "nope_a.dat"),
            "--file-b",
            str(tmp_path / "nope_b.dat"),
            "--config-dir",
            str(CONFIG_DIR),
            "--output-dir",
            str(out),
        ]
    )
    assert code == EXIT_INPUT_NOT_FOUND


def test_main_bad_config_returns_config_error(tmp_path: Path) -> None:
    """An empty config dir triggers a ConfigError."""
    bad_cfg = tmp_path / "bad_config"
    bad_cfg.mkdir()
    out = tmp_path / "results"
    code = main(
        [
            "--file-a",
            str(SAMPLE_A),
            "--file-b",
            str(SAMPLE_B),
            "--config-dir",
            str(bad_cfg),
            "--output-dir",
            str(out),
        ]
    )
    assert code == EXIT_CONFIG_ERROR


def test_main_validate_config_only_does_not_touch_inputs(tmp_path: Path) -> None:
    code = main(
        [
            "--config-dir",
            str(CONFIG_DIR),
            "--validate-config",
        ]
    )
    assert code == EXIT_OK


def test_main_dry_run_does_not_create_outputs(tmp_path: Path) -> None:
    out = tmp_path / "results"
    code = main(
        [
            "--file-a",
            str(SAMPLE_A),
            "--file-b",
            str(SAMPLE_B),
            "--config-dir",
            str(CONFIG_DIR),
            "--output-dir",
            str(out),
            "--dry-run",
        ]
    )
    assert code == EXIT_OK
    assert not out.exists()  # dry-run never opens the writer


def test_main_missing_required_args_errors_out() -> None:
    """SystemExit from argparse when --file-a/--file-b/--output-dir are absent."""
    with pytest.raises(SystemExit):
        main(["--config-dir", str(CONFIG_DIR)])
