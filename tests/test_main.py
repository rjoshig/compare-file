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

    # Sample has 1 mismatch → exit 1 (mismatch beats orphans/dups for exit code).
    assert code == EXIT_MISMATCHES

    # All eight outputs exist
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
        assert (out / name).exists(), f"missing output: {name}"

    summary = json.loads((out / "summary.json").read_text())
    assert summary["records_matched"] == 2
    assert summary["records_mismatched"] == 1
    assert summary["keys_in_a_only"] == 1
    assert summary["keys_in_b_only"] == 1


def test_main_all_matches_returns_ok(tmp_path: Path) -> None:
    """When A and B are byte-identical, exit code is 0."""
    a_copy = tmp_path / "a.dat"
    b_copy = tmp_path / "b.dat"
    a_copy.write_bytes(SAMPLE_A.read_bytes())
    b_copy.write_bytes(SAMPLE_A.read_bytes())  # identical to A
    out = tmp_path / "results"
    code = main(
        [
            "--file-a",
            str(a_copy),
            "--file-b",
            str(b_copy),
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
    # Both records are different keys → all orphans, zero joined records.
    a_file.write_bytes(b"TU4R019KEY000000001NM01017NAME_ALICEENDS007\n")
    b_file.write_bytes(b"TU4R019KEY000000999NM01017NAME_ZZZZZENDS007\n")
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
