"""Engine config: two per-file layouts + run-wide runtime knobs.

Reads three JSON files from ``config_dir`` — ``layout_file_A.json``,
``layout_file_B.json``, and ``runtime.json`` — and assembles a
fully-validated :class:`EngineConfig` plus an audit hash so
``summary.json`` can prove which config produced a given run
(ADR-017, ADR-033).

Each layout file is validated by
:func:`segment_compare.layout.load_file_layout`; this module then
synthesizes the engine-facing views (per-file :class:`ParserConfig`,
:class:`SegmentsConfig`, RDW, leading-byte strip) and the per-segment
:class:`FieldNormalizationRule` mapping that the comparator consumes.

The position-based normalization form is gone (ADR-007/008/029
superseded by ADR-033). Every segment that appears in both layouts gets
a field-based rule; segments only in one side fall through unchanged
and surface as count differences in the multiset comparator.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from segment_compare.layout import FileLayout, LayoutError, SegmentAlias, load_file_layout
from segment_compare.normalizer import FieldDef, FieldNormalizationRule
from segment_compare.parser import ParserConfig, RdwConfig, SegmentsConfig

LAYOUT_A_FILE = "layout_file_A.json"
LAYOUT_B_FILE = "layout_file_B.json"
RUNTIME_FILE = "runtime.json"

SUPPORTED_HASH_METHODS = ("blake2b", "builtin")
SUPPORTED_PARTITION_STRATEGIES = ("equal_count",)

MIN_BLAKE2B_DIGEST = 1
MAX_BLAKE2B_DIGEST = 64


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or invalid.

    Attributes:
        field: Path-like identifier of the offending field (e.g.,
            ``"runtime.json::parallel_workers"``).
        message: Human-readable description of the problem.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(field, message)
        self.field = field
        self.message = message

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Run-wide knobs from ``runtime.json``.

    Per-file sort metadata (``input_sorted``, ``order``, ``key_type``)
    lives on each layout's ``sort`` block now (ADR-033) — not here.

    Attributes:
        hash_method: ``"blake2b"`` or ``"builtin"``.
        blake2b_digest_size: Bytes of digest produced by blake2b.
            Ignored when ``hash_method == "builtin"``.
        sort_temp_dir: Spill directory for the external chunk-and-merge
            sort.
        parallel_workers: Default worker process count when the CLI
            does not pass ``--workers`` (ADR-028).
        chunk_size: Records buffered per external-sort chunk.
        partition_strategy: Worker partition scheme. ``"equal_count"``
            is the only supported value today.
    """

    hash_method: str
    blake2b_digest_size: int
    sort_temp_dir: Path
    parallel_workers: int
    chunk_size: int
    partition_strategy: str


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Validated engine config: two layouts plus run-wide runtime knobs.

    Provides ``parser_*`` / ``segments_*`` / ``file_*_rdw`` /
    ``file_*_strip_size`` accessors so engine modules can ask for the
    legacy-shaped per-file views without reaching into the layout
    objects.

    Attributes:
        layout_a: Validated layout for File A.
        layout_b: Validated layout for File B.
        runtime: Run-wide runtime knobs.
        normalization: ``segment_name -> FieldNormalizationRule`` built
            from the two layouts at load time. A segment is in this
            map iff it appears in both layouts.
        audit_hash: SHA-256 hex of the canonicalized bundle of the
            three source JSON documents (``$comment`` keys stripped).
        paths: ``kind -> Path`` mapping of source files for inclusion
            in ``summary.json``.
    """

    layout_a: FileLayout
    layout_b: FileLayout
    runtime: RuntimeConfig
    normalization: dict[str, FieldNormalizationRule] = field(default_factory=dict)
    audit_hash: str = ""
    paths: dict[str, Path] = field(default_factory=dict)

    @property
    def parser_a(self) -> ParserConfig:
        """Engine-facing :class:`ParserConfig` derived from File A's layout."""
        return _file_format_to_parser(self.layout_a)

    @property
    def parser_b(self) -> ParserConfig:
        """Engine-facing :class:`ParserConfig` derived from File B's layout."""
        return _file_format_to_parser(self.layout_b)

    @property
    def segments_a(self) -> SegmentsConfig:
        """:class:`SegmentsConfig` for File A (per-file key_range)."""
        return _layout_to_segments(self.layout_a)

    @property
    def segments_b(self) -> SegmentsConfig:
        """:class:`SegmentsConfig` for File B (per-file key_range)."""
        return _layout_to_segments(self.layout_b)

    @property
    def file_a_rdw(self) -> RdwConfig | None:
        """File A's optional RDW prefix, or ``None``."""
        return self.layout_a.rdw

    @property
    def file_b_rdw(self) -> RdwConfig | None:
        """File B's optional RDW prefix, or ``None``."""
        return self.layout_b.rdw

    @property
    def file_a_strip_size(self) -> int:
        """File A's per-record leading-byte strip, or 0 if absent."""
        return self.layout_a.strip_leading_bytes.size if self.layout_a.strip_leading_bytes else 0

    @property
    def file_b_strip_size(self) -> int:
        """File B's per-record leading-byte strip, or 0 if absent."""
        return self.layout_b.strip_leading_bytes.size if self.layout_b.strip_leading_bytes else 0

    @property
    def file_a_aliases(self) -> tuple[SegmentAlias, ...]:
        """File A's context-sensitive segment-rename rules (ADR-034)."""
        return self.layout_a.segment_aliases

    @property
    def file_b_aliases(self) -> tuple[SegmentAlias, ...]:
        """File B's context-sensitive segment-rename rules (ADR-034)."""
        return self.layout_b.segment_aliases

    @property
    def known_segments(self) -> tuple[str, ...]:
        """Union of segment names across both layouts, in stable order.

        File A's order first; B's extras appended in B's declaration
        order. Used by the writer to emit a stable
        ``per_segment`` block in ``summary.json``.
        """
        seen: dict[str, None] = {}
        for seg in self.layout_a.segments:
            seen.setdefault(seg.name, None)
        for seg in self.layout_b.segments:
            seen.setdefault(seg.name, None)
        return tuple(seen)


def load_config(config_dir: Path) -> EngineConfig:
    """Load and validate the three JSON files in ``config_dir``.

    Args:
        config_dir: Directory containing ``layout_file_A.json``,
            ``layout_file_B.json``, and ``runtime.json``.

    Returns:
        A fully validated :class:`EngineConfig`.

    Raises:
        ConfigError: If any file is missing, malformed, or invalid, or
            if a layout file fails its own load-time invariants. Errors
            from :class:`LayoutError` are re-raised as ``ConfigError``
            so the CLI can map them to a single exit code.
    """
    layout_a_path = config_dir / LAYOUT_A_FILE
    layout_b_path = config_dir / LAYOUT_B_FILE
    runtime_path = config_dir / RUNTIME_FILE

    try:
        layout_a = load_file_layout(layout_a_path)
        layout_b = load_file_layout(layout_b_path)
    except LayoutError as exc:
        raise ConfigError(exc.field, exc.message) from exc

    runtime_raw = _read_json(runtime_path)
    runtime_cfg = _build_runtime_config(runtime_raw, runtime_path)

    normalization = _build_normalization(layout_a, layout_b)

    audit_hash = _compute_audit_hash(layout_a_path, layout_b_path, runtime_raw)

    return EngineConfig(
        layout_a=layout_a,
        layout_b=layout_b,
        runtime=runtime_cfg,
        normalization=normalization,
        audit_hash=audit_hash,
        paths={
            "layout_a": layout_a_path,
            "layout_b": layout_b_path,
            "runtime": runtime_path,
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _file_format_to_parser(layout: FileLayout) -> ParserConfig:
    ff = layout.file_format
    return ParserConfig(
        segment_name_bytes=ff.segment_name_bytes,
        size_field_bytes=ff.size_field_bytes,
        size_encoding=ff.size_encoding,
        size_includes_header=ff.size_includes_header,
        data_encoding=ff.data_encoding,
    )


def _layout_to_segments(layout: FileLayout) -> SegmentsConfig:
    return SegmentsConfig(
        key_segment=layout.key_segment.name,
        end_segment=layout.end_segment.name,
        key_range=layout.key_range,
        record_delimiter=layout.file_format.record_delimiter,
    )


def _build_normalization(
    layout_a: FileLayout, layout_b: FileLayout
) -> dict[str, FieldNormalizationRule]:
    """Pair per-segment layouts from A and B into the normalizer's rule map.

    A segment appears in the rule map iff its name is declared in
    *both* layouts. Segments only in one side fall through the
    normalizer unchanged and surface as count differences in the
    multiset comparator.
    """
    a_segs = {seg.name: seg for seg in layout_a.segments}
    b_segs = {seg.name: seg for seg in layout_b.segments}
    out: dict[str, FieldNormalizationRule] = {}
    for name in a_segs:
        if name in b_segs:
            out[name] = FieldNormalizationRule(
                file_a_layout=tuple(
                    FieldDef(name=f.name, length=f.length, exclude=f.exclude)
                    for f in a_segs[name].fields
                ),
                file_b_layout=tuple(
                    FieldDef(name=f.name, length=f.length, exclude=f.exclude)
                    for f in b_segs[name].fields
                ),
            )
    return out


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(str(path), "config file does not exist")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(str(path), f"could not read file: {exc}") from exc
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ConfigError(str(path), f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError(str(path), "top-level JSON value must be an object")
    return parsed


def _require_field(obj: dict[str, Any], key: str, path: Path) -> Any:
    if key not in obj:
        raise ConfigError(f"{path.name}::{key}", "required field is missing")
    return obj[key]


def _require_type(value: Any, expected: type, path: Path, field_path: str) -> Any:
    if not isinstance(value, expected):
        raise ConfigError(
            f"{path.name}::{field_path}",
            f"expected {expected.__name__}, got {type(value).__name__}",
        )
    return value


def _build_runtime_config(rt_raw: dict[str, Any], rt_path: Path) -> RuntimeConfig:
    hash_method = _require_type(
        _require_field(rt_raw, "hash_method", rt_path), str, rt_path, "hash_method"
    )
    if hash_method not in SUPPORTED_HASH_METHODS:
        raise ConfigError(
            f"{rt_path.name}::hash_method",
            f"must be one of {list(SUPPORTED_HASH_METHODS)}, got {hash_method!r}",
        )

    digest_size = _require_type(
        _require_field(rt_raw, "blake2b_digest_size", rt_path),
        int,
        rt_path,
        "blake2b_digest_size",
    )
    if not (MIN_BLAKE2B_DIGEST <= digest_size <= MAX_BLAKE2B_DIGEST):
        raise ConfigError(
            f"{rt_path.name}::blake2b_digest_size",
            f"must be in [{MIN_BLAKE2B_DIGEST}, {MAX_BLAKE2B_DIGEST}], got {digest_size}",
        )

    sort_temp_dir = Path(
        _require_type(
            _require_field(rt_raw, "sort_temp_dir", rt_path), str, rt_path, "sort_temp_dir"
        )
    )

    parallel_workers = _require_type(
        _require_field(rt_raw, "parallel_workers", rt_path), int, rt_path, "parallel_workers"
    )
    if parallel_workers < 1:
        raise ConfigError(
            f"{rt_path.name}::parallel_workers",
            f"must be >= 1, got {parallel_workers}",
        )

    chunk_size = _require_type(
        _require_field(rt_raw, "chunk_size", rt_path), int, rt_path, "chunk_size"
    )
    if chunk_size < 1:
        raise ConfigError(f"{rt_path.name}::chunk_size", f"must be >= 1, got {chunk_size}")

    partition_strategy = _require_type(
        _require_field(rt_raw, "partition_strategy", rt_path),
        str,
        rt_path,
        "partition_strategy",
    )
    if partition_strategy not in SUPPORTED_PARTITION_STRATEGIES:
        raise ConfigError(
            f"{rt_path.name}::partition_strategy",
            f"must be one of {list(SUPPORTED_PARTITION_STRATEGIES)}, "
            f"got {partition_strategy!r}",
        )

    return RuntimeConfig(
        hash_method=hash_method,
        blake2b_digest_size=digest_size,
        sort_temp_dir=sort_temp_dir,
        parallel_workers=parallel_workers,
        chunk_size=chunk_size,
        partition_strategy=partition_strategy,
    )


def _strip_comments(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_comments(v) for k, v in value.items() if not k.startswith("$")}
    if isinstance(value, list):
        return [_strip_comments(v) for v in value]
    return value


def _compute_audit_hash(layout_a_path: Path, layout_b_path: Path, rt_raw: dict[str, Any]) -> str:
    """SHA-256 over the canonicalized merged config bundle.

    Layouts are read fresh from disk (rather than re-serializing the
    typed objects) so the hash directly reflects the bytes the engine
    was configured from, $comment fields excluded.
    """
    layout_a_raw = json.loads(layout_a_path.read_text(encoding="utf-8"))
    layout_b_raw = json.loads(layout_b_path.read_text(encoding="utf-8"))
    bundle = {
        "layout_a": _strip_comments(layout_a_raw),
        "layout_b": _strip_comments(layout_b_raw),
        "runtime": _strip_comments(rt_raw),
    }
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
