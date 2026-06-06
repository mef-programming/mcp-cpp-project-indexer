from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Time / JSON / hashing
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")

    return hashlib.sha256(data).hexdigest()


def canonical_json(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Source loading / path identity
# ---------------------------------------------------------------------------

def detect_newline_kind(raw: bytes) -> str:
    crlf = raw.count(b"\r\n")
    tmp = raw.replace(b"\r\n", b"")
    lf = tmp.count(b"\n")
    cr = tmp.count(b"\r")

    kinds = sum(1 for value in (crlf, lf, cr) if value > 0)

    if kinds > 1:
        return "mixed"

    if crlf:
        return "crlf"

    if lf:
        return "lf"

    if cr:
        return "cr"

    return "unknown"


def decode_source(raw: bytes) -> tuple[str, str]:
    # Most project files should be UTF-8. cp1252 is a pragmatic fallback for
    # legacy Windows source files. The indexer is a routing tool, so preserving
    # line numbers is more important than perfect text recovery for rare bytes.
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def split_source_lines_preserve_count(text: str) -> list[str]:
    # splitlines() avoids keeping newline characters while preserving the line
    # count expected by 1-based source ranges. A trailing final newline does not
    # create an extra logical source line in normal editor line numbering.
    return text.splitlines()


def normalized_relative_path(path: Path, project_root: Path | None) -> str:
    resolved = path.resolve()

    if project_root is not None:
        try:
            relative = resolved.relative_to(project_root.resolve())
        except ValueError:
            relative = Path(path.name)
    else:
        relative = Path(path.name)

    return relative.as_posix()


def normalize_path_for_hash(
    relative_path: str,
    *,
    case_insensitive_paths: bool = True,
) -> str:
    normalized = relative_path.replace("\\", "/")

    while "//" in normalized:
        normalized = normalized.replace("//", "/")

    normalized = normalized.strip("/")

    if case_insensitive_paths:
        normalized = normalized.casefold()

    return normalized


def make_path_hash(
    relative_path: str,
    *,
    case_insensitive_paths: bool = True,
) -> str:
    return sha256_hex(
        normalize_path_for_hash(
            relative_path,
            case_insensitive_paths=case_insensitive_paths,
        )
    )


def make_content_hash(raw: bytes) -> str:
    return sha256_hex(raw)


def make_file_id(path_hash: str, *, length: int = 24) -> str:
    return f"f_{path_hash[:length]}"


def safe_name(text: str) -> str:
    return (
        text.replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("*", "_")
        .replace("?", "_")
        .replace('"', "_")
        .replace("<", "_")
        .replace(">", "_")
        .replace("|", "_")
    )


# ---------------------------------------------------------------------------
# Source text / signature normalization
# ---------------------------------------------------------------------------

def normalize_signature_spacing(signature: str) -> str:
    signature = re.sub(r"\s+", " ", signature).strip()
    signature = re.sub(r"\s+,", ",", signature)
    signature = re.sub(r"\(\s+", "(", signature)
    signature = re.sub(r"\s+\)", ")", signature)
    signature = re.sub(r"\[\s+", "[", signature)
    signature = re.sub(r"\s+\]", "]", signature)
    signature = re.sub(r"\s+;", ";", signature)
    signature = re.sub(r"\s+:", " :", signature)
    signature = re.sub(r"\s+<\s+", " <", signature)
    signature = re.sub(r"\s+>", ">", signature)
    signature = re.sub(r"\s+::\s+", "::", signature)
    signature = re.sub(r"~\s+", "~", signature)
    return signature


def source_text_range(
    lines: list[str],
    start_line: int,
    end_line: int,
    end_col0_exclusive: int | None = None,
) -> str:
    if start_line < 1:
        start_line = 1

    if end_line > len(lines):
        end_line = len(lines)

    if start_line > end_line:
        return ""

    parts: list[str] = []

    for line_no in range(start_line, end_line + 1):
        line = lines[line_no - 1]

        if line_no == end_line and end_col0_exclusive is not None:
            line = line[:end_col0_exclusive]

        parts.append(line)

    return normalize_signature_spacing(" ".join(parts))


def strip_line_prefix(line: str) -> str:
    return re.sub(r"^\d{4,}:\s?", "", line)


# ---------------------------------------------------------------------------
# Symbol identity
# ---------------------------------------------------------------------------

def make_signature_hash(signature_key: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(signature_key))


def make_symbol_id(
    *,
    file_id: str,
    start_line: int,
    end_line: int,
    signature_hash: str,
    hash_length: int = 12,
) -> str:
    short_file_id = file_id

    if short_file_id.startswith("f_"):
        short_file_id = short_file_id[2:]

    return (
        f"s_f_{short_file_id}_"
        f"{start_line:06d}_{end_line:06d}_"
        f"{signature_hash[:hash_length]}"
    )


def short_name_from_qualified_name(name: str) -> str:
    if not name:
        return ""

    # This is intentionally simple. Operator names may contain spaces, but they
    # are still after the final :: in qualified form.
    return name.split("::")[-1]


def container_from_qualified_name(name: str) -> str | None:
    if "::" not in name:
        return None

    return "::".join(name.split("::")[:-1])


# ---------------------------------------------------------------------------
# Range validation helpers
# ---------------------------------------------------------------------------

def is_valid_line_range(
    *,
    start_line: int,
    end_line: int,
    line_count: int,
) -> bool:
    return 1 <= start_line <= end_line <= line_count


def require_valid_line_range(
    *,
    start_line: int,
    end_line: int,
    line_count: int,
    context: str,
) -> None:
    if not is_valid_line_range(
        start_line=start_line,
        end_line=end_line,
        line_count=line_count,
    ):
        raise ValueError(
            f"Invalid source range for {context}: "
            f"{start_line}-{end_line}, line_count={line_count}"
        )


def range_contains_line(
    *,
    start_line: int,
    end_line: int,
    line_no: int,
) -> bool:
    return start_line <= line_no <= end_line
