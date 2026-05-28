"""Configuration loading and validation.

Reads the three JSON config files in ``config_dir`` (``segments.json``,
``normalization.json``, ``runtime.json``), validates every field, and
returns an immutable :class:`ResolvedConfig` plus an audit hash so that
``summary.json`` can prove which config produced a given run
(ADR-017).

Phase 1 honors only a restricted set of values for the forward-
compatible parser knobs and runtime knobs (ADR-016). Anything else
raises :class:`ConfigError` at load time rather than at first use.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from segment_compare.parser import ParserConfig, SegmentsConfig

SEGMENTS_FILE = "segments.json"
NORMALIZATION_FILE = "normalization.json"
RUNTIME_FILE = "runtime.json"

SUPPORTED_HASH_METHODS = ("blake2b", "builtin")
SUPPORTED_PARTITION_STRATEGIES = ("equal_count",)
SUPPORTED_KEY_TYPES = ("alphanumeric", "numeric")
SUPPORTED_KEY_SORT_ORDERS = ("ascending", "descending")
SUPPORTED_SIZE_ENCODINGS_PHASE1 = ("ascii_int",)
SUPPORTED_DATA_ENCODINGS_PHASE1 = ("ascii",)

DEFAULT_SEGMENT_NAME_BYTES = 4
DEFAULT_SIZE_FIELD_BYTES = 3

MIN_BLAKE2B_DIGEST = 1
MAX_BLAKE2B_DIGEST = 64


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or invalid.

    Attributes:
        field: Path-like identifier of the offending field (e.g.,
            ``"segments.json::key_range"``).
        message: Human-readable description of the problem.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(field, message)
        self.field = field
        self.message = message

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


@dataclass(frozen=True, slots=True)
class NormalizationRule:
    """Per-segment normalization rule (Phase 1 position-based form).

    Attributes:
        file_a_strip: Byte ranges to remove from File A's segment data
            before alignment, as ``(start, end)`` end-exclusive pairs.
        file_b_strip: Same as ``file_a_strip``, applied to File B.
        exclude_positions: Byte ranges to remove from both files'
            post-strip data before hashing.
    """

    file_a_strip: tuple[tuple[int, int], ...]
    file_b_strip: tuple[tuple[int, int], ...]
    exclude_positions: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Runtime knobs from ``runtime.json``.

    Phase 1 honors all fields but only single-process behavior. Phase 2
    introduces parallelism that consumes ``parallel_workers`` and
    ``partition_strategy``.
    """

    hash_method: str
    blake2b_digest_size: int
    input_sorted: bool
    sort_temp_dir: Path
    parallel_workers: int
    chunk_size: int
    partition_strategy: str
    key_type: str
    key_sort_order: str


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    """All three configs validated and assembled for engine consumption.

    Attributes:
        parser: Parser knobs (see :class:`ParserConfig`).
        segments: Record-framing config (see :class:`SegmentsConfig`).
        known_segments: All segment names the parser may encounter.
        normalization: Per-segment normalization rules keyed by segment
            name. Segments not present in this mapping have no
            normalization applied.
        runtime: Runtime knobs (see :class:`RuntimeConfig`).
        audit_hash: SHA-256 hex of the canonicalized merged config
            bundle (``$comment`` keys stripped, all keys sorted).
        paths: Mapping of config-file kind to its source ``Path``, for
            inclusion in ``summary.json``.
    """

    parser: ParserConfig
    segments: SegmentsConfig
    known_segments: tuple[str, ...]
    normalization: dict[str, NormalizationRule] = field(default_factory=dict)
    runtime: RuntimeConfig = field(
        default_factory=lambda: RuntimeConfig(
            hash_method="blake2b",
            blake2b_digest_size=16,
            input_sorted=True,
            sort_temp_dir=Path("/tmp/segment_compare"),
            parallel_workers=1,
            chunk_size=10000,
            partition_strategy="equal_count",
            key_type="alphanumeric",
            key_sort_order="ascending",
        )
    )
    audit_hash: str = ""
    paths: dict[str, Path] = field(default_factory=dict)


def load_config(config_dir: Path) -> ResolvedConfig:
    """Load and validate the three JSON configs from ``config_dir``.

    Args:
        config_dir: Directory containing ``segments.json``,
            ``normalization.json``, and ``runtime.json``.

    Returns:
        A :class:`ResolvedConfig` with every field validated.

    Raises:
        ConfigError: If any file is missing, malformed, or invalid.
    """
    seg_path = config_dir / SEGMENTS_FILE
    norm_path = config_dir / NORMALIZATION_FILE
    rt_path = config_dir / RUNTIME_FILE

    seg_raw = _read_json(seg_path)
    norm_raw = _read_json(norm_path)
    rt_raw = _read_json(rt_path)

    parser_cfg = _build_parser_config(seg_raw, seg_path)
    known_segments, segments_cfg = _build_segments_config(seg_raw, seg_path)
    normalization = _build_normalization(norm_raw, known_segments, norm_path)
    runtime_cfg = _build_runtime_config(rt_raw, rt_path)

    audit_hash = _compute_audit_hash(seg_raw, norm_raw, rt_raw)

    return ResolvedConfig(
        parser=parser_cfg,
        segments=segments_cfg,
        known_segments=known_segments,
        normalization=normalization,
        runtime=runtime_cfg,
        audit_hash=audit_hash,
        paths={
            "segments": seg_path,
            "normalization": norm_path,
            "runtime": rt_path,
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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


def _build_parser_config(seg_raw: dict[str, Any], seg_path: Path) -> ParserConfig:
    parser_in = seg_raw.get("parser", {})
    _require_type(parser_in, dict, seg_path, "parser")

    snb = parser_in.get("segment_name_bytes", DEFAULT_SEGMENT_NAME_BYTES)
    sfb = parser_in.get("size_field_bytes", DEFAULT_SIZE_FIELD_BYTES)
    enc = parser_in.get("size_encoding", "ascii_int")
    inc = parser_in.get("size_includes_header", True)
    data_enc = parser_in.get("data_encoding", "ascii")

    if snb != DEFAULT_SEGMENT_NAME_BYTES:
        raise ConfigError(
            f"{seg_path.name}::parser.segment_name_bytes",
            f"Phase 1 supports only {DEFAULT_SEGMENT_NAME_BYTES}, got {snb!r}",
        )
    if sfb != DEFAULT_SIZE_FIELD_BYTES:
        raise ConfigError(
            f"{seg_path.name}::parser.size_field_bytes",
            f"Phase 1 supports only {DEFAULT_SIZE_FIELD_BYTES}, got {sfb!r}",
        )
    if enc not in SUPPORTED_SIZE_ENCODINGS_PHASE1:
        raise ConfigError(
            f"{seg_path.name}::parser.size_encoding",
            f"Phase 1 supports only {list(SUPPORTED_SIZE_ENCODINGS_PHASE1)}, got {enc!r}",
        )
    if inc is not True:
        raise ConfigError(
            f"{seg_path.name}::parser.size_includes_header",
            f"Phase 1 supports only True, got {inc!r}",
        )
    if data_enc not in SUPPORTED_DATA_ENCODINGS_PHASE1:
        raise ConfigError(
            f"{seg_path.name}::parser.data_encoding",
            f"Phase 1 supports only {list(SUPPORTED_DATA_ENCODINGS_PHASE1)}, got {data_enc!r}",
        )

    return ParserConfig(
        segment_name_bytes=snb,
        size_field_bytes=sfb,
        size_encoding=enc,
        size_includes_header=inc,
        data_encoding=data_enc,
    )


def _build_segments_config(
    seg_raw: dict[str, Any], seg_path: Path
) -> tuple[tuple[str, ...], SegmentsConfig]:
    known_raw = _require_type(
        _require_field(seg_raw, "known_segments", seg_path),
        list,
        seg_path,
        "known_segments",
    )
    if not known_raw:
        raise ConfigError(f"{seg_path.name}::known_segments", "must not be empty")
    for i, name in enumerate(known_raw):
        if not isinstance(name, str) or not name:
            raise ConfigError(
                f"{seg_path.name}::known_segments[{i}]",
                "entries must be non-empty strings",
            )
    if len(set(known_raw)) != len(known_raw):
        raise ConfigError(f"{seg_path.name}::known_segments", "entries must be unique")
    known_segments = tuple(known_raw)

    key_segment = _require_type(
        _require_field(seg_raw, "key_segment", seg_path), str, seg_path, "key_segment"
    )
    end_segment = _require_type(
        _require_field(seg_raw, "end_segment", seg_path), str, seg_path, "end_segment"
    )
    if key_segment not in known_segments:
        raise ConfigError(
            f"{seg_path.name}::key_segment",
            f"{key_segment!r} not in known_segments",
        )
    if end_segment not in known_segments:
        raise ConfigError(
            f"{seg_path.name}::end_segment",
            f"{end_segment!r} not in known_segments",
        )
    if key_segment == end_segment:
        raise ConfigError(
            f"{seg_path.name}::end_segment",
            "must differ from key_segment",
        )

    key_range_raw = _require_field(seg_raw, "key_range", seg_path)
    if (
        not isinstance(key_range_raw, list)
        or len(key_range_raw) != 2
        or not all(isinstance(v, int) for v in key_range_raw)
    ):
        raise ConfigError(
            f"{seg_path.name}::key_range",
            "must be a list of two integers [start, end]",
        )
    start, end = key_range_raw
    if start < 0 or end <= start:
        raise ConfigError(
            f"{seg_path.name}::key_range",
            f"invalid range [{start}, {end}); require 0 <= start < end",
        )

    delim_raw = _require_type(
        _require_field(seg_raw, "record_delimiter", seg_path),
        str,
        seg_path,
        "record_delimiter",
    )
    try:
        delim_bytes = delim_raw.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ConfigError(
            f"{seg_path.name}::record_delimiter",
            f"must be ASCII-encodable, got {delim_raw!r}",
        ) from exc

    segments_cfg = SegmentsConfig(
        key_segment=key_segment,
        end_segment=end_segment,
        key_range=(start, end),
        record_delimiter=delim_bytes,
    )
    return known_segments, segments_cfg


def _build_normalization(
    norm_raw: dict[str, Any],
    known_segments: tuple[str, ...],
    norm_path: Path,
) -> dict[str, NormalizationRule]:
    out: dict[str, NormalizationRule] = {}
    known = set(known_segments)
    for name, rule_raw in norm_raw.items():
        if name.startswith("$"):
            continue
        if name not in known:
            raise ConfigError(
                f"{norm_path.name}::{name}",
                "segment is not in segments.json::known_segments",
            )
        _require_type(rule_raw, dict, norm_path, name)
        out[name] = NormalizationRule(
            file_a_strip=_parse_ranges(
                rule_raw.get("file_a_strip", []), norm_path, f"{name}.file_a_strip"
            ),
            file_b_strip=_parse_ranges(
                rule_raw.get("file_b_strip", []), norm_path, f"{name}.file_b_strip"
            ),
            exclude_positions=_parse_ranges(
                rule_raw.get("exclude_positions", []),
                norm_path,
                f"{name}.exclude_positions",
            ),
        )
    return out


def _parse_ranges(raw: Any, path: Path, field_path: str) -> tuple[tuple[int, int], ...]:
    if not isinstance(raw, list):
        raise ConfigError(
            f"{path.name}::{field_path}",
            f"must be a list of [start, end] pairs, got {type(raw).__name__}",
        )
    out: list[tuple[int, int]] = []
    for i, item in enumerate(raw):
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(v, int) for v in item)
        ):
            raise ConfigError(
                f"{path.name}::{field_path}[{i}]",
                "must be a list of two integers [start, end]",
            )
        start, end = item
        if start < 0 or end < start:
            raise ConfigError(
                f"{path.name}::{field_path}[{i}]",
                f"invalid range [{start}, {end}); require 0 <= start <= end",
            )
        out.append((start, end))
    return tuple(out)


def _build_runtime_config(rt_raw: dict[str, Any], rt_path: Path) -> RuntimeConfig:
    hash_method = _require_type(
        _require_field(rt_raw, "hash_method", rt_path),
        str,
        rt_path,
        "hash_method",
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

    input_sorted = _require_type(
        _require_field(rt_raw, "input_sorted", rt_path),
        bool,
        rt_path,
        "input_sorted",
    )

    sort_temp_dir = Path(
        _require_type(
            _require_field(rt_raw, "sort_temp_dir", rt_path),
            str,
            rt_path,
            "sort_temp_dir",
        )
    )

    parallel_workers = _require_type(
        _require_field(rt_raw, "parallel_workers", rt_path),
        int,
        rt_path,
        "parallel_workers",
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
            f"must be one of {list(SUPPORTED_PARTITION_STRATEGIES)}, got {partition_strategy!r}",
        )

    key_type = _require_type(_require_field(rt_raw, "key_type", rt_path), str, rt_path, "key_type")
    if key_type not in SUPPORTED_KEY_TYPES:
        raise ConfigError(
            f"{rt_path.name}::key_type",
            f"must be one of {list(SUPPORTED_KEY_TYPES)}, got {key_type!r}",
        )

    key_sort_order = _require_type(
        _require_field(rt_raw, "key_sort_order", rt_path),
        str,
        rt_path,
        "key_sort_order",
    )
    if key_sort_order not in SUPPORTED_KEY_SORT_ORDERS:
        raise ConfigError(
            f"{rt_path.name}::key_sort_order",
            f"must be one of {list(SUPPORTED_KEY_SORT_ORDERS)}, got {key_sort_order!r}",
        )

    return RuntimeConfig(
        hash_method=hash_method,
        blake2b_digest_size=digest_size,
        input_sorted=input_sorted,
        sort_temp_dir=sort_temp_dir,
        parallel_workers=parallel_workers,
        chunk_size=chunk_size,
        partition_strategy=partition_strategy,
        key_type=key_type,
        key_sort_order=key_sort_order,
    )


def _strip_comments(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_comments(v) for k, v in value.items() if not k.startswith("$")}
    if isinstance(value, list):
        return [_strip_comments(v) for v in value]
    return value


def _compute_audit_hash(
    seg_raw: dict[str, Any],
    norm_raw: dict[str, Any],
    rt_raw: dict[str, Any],
) -> str:
    bundle = {
        "segments": _strip_comments(seg_raw),
        "normalization": _strip_comments(norm_raw),
        "runtime": _strip_comments(rt_raw),
    }
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
