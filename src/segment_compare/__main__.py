"""CLI entry point for ``segment-compare``.

Thin wrapper around :func:`segment_compare.pipeline.run`. The CLI is
responsible only for argument parsing, logging setup, error-to-exit-code
translation, and printing a short human-readable summary. All
comparison logic lives in :mod:`segment_compare.pipeline` (ADR-012).

Exit codes (also documented in ``docs/phase-4.md``):

- ``0``  success, no mismatches
- ``1``  success, mismatches found
- ``2``  completed with warnings (orphan keys or duplicate keys)
- ``10`` config validation error
- ``11`` input file not found
- ``12`` output write error
- ``20`` parse error (corrupt input)
- ``30`` unexpected runtime error
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from segment_compare import __version__
from segment_compare.config import ConfigError, load_config
from segment_compare.parser import ParseError
from segment_compare.pipeline import InputFileError, dry_run, run, run_parallel

EXIT_OK = 0
EXIT_MISMATCHES = 1
EXIT_WARNINGS = 2
EXIT_CONFIG_ERROR = 10
EXIT_INPUT_NOT_FOUND = 11
EXIT_OUTPUT_ERROR = 12
EXIT_PARSE_ERROR = 20
EXIT_RUNTIME_ERROR = 30

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Argument list (default ``sys.argv[1:]``).

    Returns:
        One of the published exit codes.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    _configure_logging(args.log_level)

    try:
        config = load_config(args.config_dir)
    except ConfigError as exc:
        sys.stderr.write(f"config error: {exc}\n")
        return EXIT_CONFIG_ERROR

    if args.validate_config:
        sys.stdout.write(f"config OK (audit hash: {config.audit_hash})\n")
        return EXIT_OK

    if args.file_a is None or args.file_b is None or args.output_dir is None:
        parser.error("--file-a, --file-b, and --output-dir are required")

    if args.dry_run:
        try:
            report = dry_run(args.file_a, args.file_b, config)
        except InputFileError as exc:
            sys.stderr.write(f"input error: {exc}\n")
            return EXIT_INPUT_NOT_FOUND
        except ParseError as exc:
            sys.stderr.write(f"parse error: {exc}\n")
            return EXIT_PARSE_ERROR
        sys.stdout.write(
            f"dry-run OK: A={report.file_a_records} records "
            f"({report.dups_in_a} dup occurrences), "
            f"B={report.file_b_records} records "
            f"({report.dups_in_b} dup occurrences)\n"
        )
        return EXIT_OK

    try:
        if args.workers > 1:
            summary = run_parallel(
                args.file_a, args.file_b, config, args.output_dir, workers=args.workers
            )
        else:
            summary = run(args.file_a, args.file_b, config, args.output_dir)
    except InputFileError as exc:
        sys.stderr.write(f"input error: {exc}\n")
        return EXIT_INPUT_NOT_FOUND
    except ParseError as exc:
        sys.stderr.write(f"parse error: {exc}\n")
        return EXIT_PARSE_ERROR
    except OSError as exc:
        sys.stderr.write(f"output error: {exc}\n")
        return EXIT_OUTPUT_ERROR

    sys.stdout.write(
        f"done in {summary.elapsed_seconds:.3f}s: "
        f"matched={summary.records_matched}, "
        f"mismatched={summary.records_mismatched}, "
        f"only_a={summary.keys_in_a_only}, only_b={summary.keys_in_b_only}, "
        f"dups_a={summary.dups_in_a}, dups_b={summary.dups_in_b}\n"
    )

    if summary.records_mismatched > 0:
        return EXIT_MISMATCHES
    if summary.keys_in_a_only or summary.keys_in_b_only or summary.dups_in_a or summary.dups_in_b:
        return EXIT_WARNINGS
    return EXIT_OK


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="segment-compare",
        description=(
            "Compare two fixed-format segment-based data files and write "
            "matches, mismatches, orphans, duplicates, a CSV report, and a "
            "summary JSON."
        ),
    )
    parser.add_argument(
        "--file-a",
        type=Path,
        help="Path to File A (the 'left' input).",
    )
    parser.add_argument(
        "--file-b",
        type=Path,
        help="Path to File B (the 'right' input).",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        required=True,
        help=("Directory containing segments.json, normalization.json, and " "runtime.json."),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to write the eight output files into (created if missing).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Number of worker processes (default: 1, single-process Phase 1 "
            "path). Values >1 invoke the Phase 2 parallel pipeline. Output "
            "is byte-identical to the single-process run modulo timings."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate both input files without producing outputs.",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate the config files and exit without touching the inputs.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
