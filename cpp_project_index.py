from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import time

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, AbstractSet, Iterable
from fnmatch import fnmatchcase
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

from cpp_file_index import build_file_index
from cpp_index_sqlite import (
    ThreadLocalIndexConnections,
    build_sqlite_index,
    row_json,
    sqlite_index_path,
)
from cpp_index_utils import save_json
from cpp_lexer import find_matching_token, tokenize_lines, token_values
from cpp_structural_scan import extract_function_name
from cpp_comment_context import extract_file_header_comment, extract_leading_comment
from cpp_comment_context import format_source_lines


DEFAULT_SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".ixx",
    ".cppm",
    ".mm",
}

DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".mcp-cpp-project-indexer",
    ".vs",
    ".vscode",
    "build",
    "out",
    "bin",
    "obj",
    "x64",
    "x86",
    "arm64",
    "Debug",
    "Release",
    "RelWithDebInfo",
    "MinSizeRel",
    "node_modules",
    "__pycache__",
}
INDEXER_CONFIG_FILE_NAME = "indexer_config.json"


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    extensions: frozenset[str]
    excluded_dir_names: frozenset[str]
    include_extensionless_headers: bool
    use_git_ignore: bool


def normalize_extension_item(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    item = value.strip()
    if not item:
        return None

    if not item.startswith("."):
        item = "." + item

    return item.casefold()


def normalize_extension_values(value: Any) -> set[str] | None:
    if value is None:
        return None

    raw_items: list[Any]
    if isinstance(value, str):
        raw_items = [
            part
            for part in value.split(",")
        ]
    elif isinstance(value, list):
        raw_items = value
    else:
        return None

    result: set[str] = set()
    for item in raw_items:
        normalized = normalize_extension_item(item)
        if normalized is not None:
            result.add(normalized)

    return result


def normalize_name_values(value: Any) -> set[str] | None:
    if value is None:
        return None

    raw_items: list[Any]
    if isinstance(value, str):
        raw_items = [
            part
            for part in value.split(",")
        ]
    elif isinstance(value, list):
        raw_items = value
    else:
        return None

    result: set[str] = set()
    for item in raw_items:
        if not isinstance(item, str):
            continue
        normalized = item.strip().casefold()
        if normalized:
            result.add(normalized)

    return result

PROJECT_INDEX_SCHEMA = "cpp.project_index.v1"
UPDATE_STATE_SCHEMA = "cpp.project_index.update_state.v1"

SYMBOL_COMPACT_FIELDS = {
    "symbolId",
    "type",
    "shortName",
    "qualifiedName",
    "container",
    "relativePath",
    "startLine",
    "endLine",
    "signature",
    "matchKind",
}


def symbol_matches_type_filter(symbol: dict[str, Any], symbol_types: set[str] | None) -> bool:
    if not symbol_types:
        return True

    return str(symbol.get("type") or "") in symbol_types


def symbol_matches_namespace_filter(symbol: dict[str, Any], hide_namespaces: bool) -> bool:
    if not hide_namespaces:
        return True

    return str(symbol.get("type") or "") != "namespace"


def symbol_matches_container_filter(symbol: dict[str, Any], container: str | None) -> bool:
    if not container:
        return True

    item_container = str(symbol.get("container") or "")
    qualified_name = str(symbol.get("qualifiedName") or "")
    container_folded = container.casefold()

    return (
        item_container.casefold() == container_folded
        or item_container.casefold().endswith("::" + container_folded)
        or qualified_name.casefold().startswith(container_folded + "::")
    )

def compact_symbol_ref(symbol: dict[str, Any]) -> dict[str, Any]:
    return {
        key: symbol.get(key)
        for key in SYMBOL_COMPACT_FIELDS
        if key in symbol
    }


FILE_STRUCTURE_SYMBOL_COMPACT_FIELDS = {
    "kind",
    "symbolId",
    "type",
    "name",
    "shortName",
    "container",
    "relativePath",
    "startLine",
    "endLine",
    "signature",
}

FILE_STRUCTURE_DATA_COMPACT_FIELDS = {
    "kind",
    "dataId",
    "declarationKind",
    "scopeKind",
    "name",
    "shortName",
    "container",
    "typeText",
    "relativePath",
    "startLine",
    "endLine",
    "signature",
}


def compact_file_structure_item(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("kind") == "data":
        fields = FILE_STRUCTURE_DATA_COMPACT_FIELDS
    else:
        fields = FILE_STRUCTURE_SYMBOL_COMPACT_FIELDS

    return {
        key: item.get(key)
        for key in fields
        if key in item
    }


FILE_DEBUG_COMPACT_FIELDS = {
    "kind",
    "name",
    "qualifiedName",
    "startLine",
    "endLine",
    "startCol0",
    "endCol0Exclusive",
    "signature",
    "fragment",
    "scopeKind",
    "parent",
    "bodyStartLine",
    "bodyEndLine",
    "message",
    "severity",
    "code",
}

FILE_DEBUG_KINDS = {
    "diagnostics",
    "structuralEvents",
    "scopeIntervals",
    "functionBodyRanges",
}


def compact_file_debug_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in FILE_DEBUG_COMPACT_FIELDS
        if key in item
    }


def item_line_range(item: dict[str, Any]) -> tuple[int, int] | None:
    range_item = item.get("range")

    if isinstance(range_item, dict):
        start_line = int(range_item.get("startLine") or 0)
        end_line = int(range_item.get("endLine") or start_line or 0)
    else:
        start_line = int(
            item.get("startLine")
            or item.get("line")
            or item.get("bodyStartLine")
            or 0
        )
        end_line = int(
            item.get("endLine")
            or item.get("line")
            or item.get("bodyEndLine")
            or start_line
            or 0
        )

    if start_line <= 0 and end_line <= 0:
        return None

    if end_line <= 0:
        end_line = start_line

    return start_line, end_line


def item_overlaps_line_filter(
    item: dict[str, Any],
    start_line: int | None,
    end_line: int | None,
) -> bool:
    if start_line is None and end_line is None:
        return True

    item_range = item_line_range(item)

    if item_range is None:
        return True

    item_start, item_end = item_range
    filter_start = start_line if start_line is not None else item_start
    filter_end = end_line if end_line is not None else item_end
    return item_start <= filter_end and filter_start <= item_end


def normalize_debug_item(item: dict[str, Any]) -> dict[str, Any]:
    range_item = item.get("range")

    if isinstance(range_item, dict):
        result = {
            key: value
            for key, value in item.items()
            if key != "range"
        }
        result.setdefault("startLine", range_item.get("startLine"))
        result.setdefault("endLine", range_item.get("endLine"))
        result.setdefault("startCol0", range_item.get("startCol0"))
        result.setdefault("endCol0Exclusive", range_item.get("endCol0Exclusive"))
        return result

    return dict(item)


def data_matches_kind_filter(item: dict[str, Any], data_kinds: set[str] | None) -> bool:
    if not data_kinds:
        return True

    return str(item.get("declarationKind") or "") in data_kinds

def symbol_match_kind(symbol: dict[str, Any], query: str) -> str:
    query_folded = query.casefold()
    short_name = str(symbol.get("shortName") or "")
    qualified_name = str(symbol.get("qualifiedName") or "")
    signature = str(symbol.get("signature") or "")

    if qualified_name and qualified_name == query:
        return "exact_qualified_name"

    if short_name and short_name == query:
        return "exact_short_name"

    if qualified_name and qualified_name.casefold() == query_folded:
        return "case_insensitive_qualified_name"

    if short_name and short_name.casefold() == query_folded:
        return "case_insensitive_short_name"

    if qualified_name and query_folded in qualified_name.casefold():
        return "qualified_name_substring"

    if short_name and query_folded in short_name.casefold():
        return "short_name_substring"

    if signature and query_folded in signature.casefold():
        return "signature_substring"

    return "metadata_match"


def symbol_match_rank(match_kind: str) -> int:
    ranks = {
        "exact_qualified_name": 0,
        "exact_short_name": 1,
        "case_insensitive_qualified_name": 2,
        "case_insensitive_short_name": 3,
        "qualified_name_substring": 4,
        "short_name_substring": 5,
        "signature_substring": 6,
        "metadata_match": 7,
    }

    return ranks.get(match_kind, 100)


def is_exact_symbol_match(symbol: dict[str, Any], query: str) -> bool:
    match_kind = symbol_match_kind(symbol, query)
    return match_kind in {
        "exact_qualified_name",
        "exact_short_name",
        "case_insensitive_qualified_name",
        "case_insensitive_short_name",
    }


def project_stats_from_manifest_files(files: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "totalCodeLines": sum(int(item.get("lineCount") or 0) for item in files),
        "totalTokens": sum(int(item.get("tokenCount") or 0) for item in files),
    }


def project_stats_from_manifest(manifest: dict[str, Any]) -> dict[str, int]:
    stats = manifest.get("stats")

    if isinstance(stats, dict):
        return {
            "totalCodeLines": int(stats.get("totalCodeLines") or 0),
            "totalTokens": int(stats.get("totalTokens") or 0),
        }

    files = manifest.get("files")

    if isinstance(files, list):
        return project_stats_from_manifest_files(files)

    return {
        "totalCodeLines": 0,
        "totalTokens": 0,
    }


@dataclass(slots=True)
class ProjectIndexBuildResult:
    root: Path
    output_root: Path
    files_count: int
    symbols_count: int
    names_count: int
    data_count: int
    data_names_count: int
    modules_count: int
    diagnostics_count: int
    total_code_lines: int
    total_tokens: int
    timings: list[dict[str, Any]]


BuildPhaseCallback = Callable[[str, str, float | None], None]


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def should_skip_dir(path: Path, excluded_dir_names: AbstractSet[str]) -> bool:
    return any(part in excluded_dir_names for part in path.parts)


def _is_excluded_dir_name(name: str, excluded_dir_names: AbstractSet[str]) -> bool:
    return name.startswith(".") or name.casefold() in excluded_dir_names


def git_ignored_source_files(root: Path, files: list[Path]) -> set[Path]:
    if not files:
        return set()

    root = root.resolve()
    git_executable = shutil.which("git")

    if git_executable is None:
        return set()

    try:
        inside_worktree = subprocess.run(
            [git_executable, "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    if inside_worktree.returncode != 0 or inside_worktree.stdout.strip() != "true":
        return set()

    relative_paths = [
        path.resolve().relative_to(root).as_posix()
        for path in files
    ]

    try:
        ignored = subprocess.run(
            [git_executable, "-C", str(root), "check-ignore", "--stdin"],
            input="\n".join(relative_paths),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    if ignored.returncode not in {0, 1}:
        return set()

    ignored_paths = {
        item.strip()
        for item in ignored.stdout.splitlines()
        if item.strip()
    }

    return {
        root / relative_path
        for relative_path in ignored_paths
    }


def load_discovery_config(directory: Path, parent: DiscoveryConfig) -> DiscoveryConfig:
    config_path = directory / INDEXER_CONFIG_FILE_NAME

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return parent

    if not isinstance(raw, dict):
        return parent

    extensions = set(parent.extensions)
    excluded_dir_names = set(parent.excluded_dir_names)
    include_extensionless_headers = parent.include_extensionless_headers

    replacement_extensions = normalize_extension_values(raw.get("extensions"))
    if replacement_extensions is not None:
        extensions = replacement_extensions

    add_extensions = normalize_extension_values(raw.get("addExtensions"))
    if add_extensions is not None:
        extensions.update(add_extensions)

    remove_extensions = normalize_extension_values(raw.get("removeExtensions"))
    if remove_extensions is not None:
        extensions.difference_update(remove_extensions)

    replacement_excluded_dirs = normalize_name_values(raw.get("excludeDirs"))
    if replacement_excluded_dirs is not None:
        excluded_dir_names = replacement_excluded_dirs

    add_excluded_dirs = normalize_name_values(raw.get("addExcludeDirs"))
    if add_excluded_dirs is not None:
        excluded_dir_names.update(add_excluded_dirs)

    remove_excluded_dirs = normalize_name_values(raw.get("removeExcludeDirs"))
    if remove_excluded_dirs is not None:
        excluded_dir_names.difference_update(remove_excluded_dirs)

    include_extensionless_value = raw.get("includeExtensionlessHeaders")
    if isinstance(include_extensionless_value, bool):
        include_extensionless_headers = include_extensionless_value

    use_git_ignore = parent.use_git_ignore
    use_git_ignore_value = raw.get("useGitIgnore")
    if isinstance(use_git_ignore_value, bool):
        use_git_ignore = use_git_ignore_value

    return DiscoveryConfig(
        extensions=frozenset(extensions),
        excluded_dir_names=frozenset(excluded_dir_names),
        include_extensionless_headers=include_extensionless_headers,
        use_git_ignore=use_git_ignore,
    )


def discover_source_files(
    root: Path,
    *,
    extensions: set[str] | None = None,
    excluded_dir_names: set[str] | None = None,
    include_extensionless_headers: bool = False,
    use_git_ignore: bool = True,
    progress_callback: Callable[[int, Path], None] | None = None,
) -> list[Path]:
    base_config = DiscoveryConfig(
        extensions=frozenset(
            item.casefold()
            for item in (extensions or DEFAULT_SOURCE_EXTENSIONS)
        ),
        excluded_dir_names=frozenset(
            item.casefold()
            for item in (excluded_dir_names or DEFAULT_EXCLUDED_DIR_NAMES)
        ),
        include_extensionless_headers=include_extensionless_headers,
        use_git_ignore=use_git_ignore,
    )

    files: list[Path] = []
    git_ignore_enabled = False
    visited = 0

    def walk(directory: Path, inherited_config: DiscoveryConfig) -> None:
        nonlocal visited, git_ignore_enabled
        config = load_discovery_config(directory, inherited_config)
        git_ignore_enabled = git_ignore_enabled or config.use_git_ignore

        try:
            entries = list(os.scandir(directory))
        except OSError:
            return

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not _is_excluded_dir_name(entry.name, config.excluded_dir_names):
                        walk(Path(entry.path), config)

                    continue

                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue

            visited += 1
            suffix = os.path.splitext(entry.name)[1].casefold()

            if suffix not in config.extensions:
                if suffix or not config.include_extensionless_headers:
                    continue

                path = Path(entry.path)
                if not looks_like_extensionless_cpp_header(path):
                    continue
            else:
                path = Path(entry.path)

            if progress_callback is not None:
                progress_callback(visited, path)

            files.append(path)

    walk(root, base_config)

    ignored_files = git_ignored_source_files(root, files) if git_ignore_enabled else set()

    if ignored_files:
        ignored_resolved = {
            path.resolve()
            for path in ignored_files
        }
        files = [
            path
            for path in files
            if path.resolve() not in ignored_resolved
        ]

    files.sort(key=lambda item: item.as_posix().casefold())
    return files


def looks_like_extensionless_cpp_header(path: Path) -> bool:
    try:
        data = path.read_bytes()[:32768]
    except OSError:
        return False

    if b"\0" in data:
        return False

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except UnicodeDecodeError:
            return False

    lines = text.splitlines()[:80]
    if not lines:
        return False

    joined = "\n".join(lines)
    stripped_lines = [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith(("//", "/*", "*"))
    ]
    if not stripped_lines:
        return False

    positive_patterns = [
        r"^\s*#\s*pragma\s+once\b",
        r"^\s*#\s*ifndef\s+\w+",
        r"^\s*#\s*define\s+\w+",
        r"^\s*#\s*include\s+[<\"]",
        r"^\s*(export\s+)?module\s+[\w.:]+",
        r"^\s*(export\s+)?import\s+[\w.:<\"]",
        r"^\s*namespace\s+[\w:]+",
        r"^\s*(template\s*<|class\s+\w+|struct\s+\w+|enum\s+(class\s+)?\w+)",
        r"^\s*(using|typedef)\s+.+[;=]",
    ]
    negative_patterns = [
        r"^\s*#!",
        r"^\s*<\?xml\b",
        r"^\s*[{[]",
        r"^\s*(FROM|SELECT|INSERT|UPDATE|DELETE)\b",
    ]

    for pattern in negative_patterns:
        if re.search(pattern, joined, re.IGNORECASE | re.MULTILINE):
            return False

    score = 0
    for pattern in positive_patterns:
        if re.search(pattern, joined, re.MULTILINE):
            score += 1

    first_code_line = stripped_lines[0]
    if first_code_line.startswith("#") and score >= 1:
        return True

    return score >= 2


def update_state_path(index_root: Path) -> Path:
    return index_root / "update_state.json"


def save_update_state_from_file_indexes(
    *,
    index_root: Path,
    root: Path,
    file_indexes: list[dict[str, Any]],
    case_insensitive_paths: bool,
) -> None:
    files: dict[str, dict[str, Any]] = {}

    for file_index in file_indexes:
        relative_path = str(file_index["relativePath"])
        key = relative_path.casefold() if case_insensitive_paths else relative_path
        path = root / relative_path

        try:
            stat = path.stat()
            mtime_ns = stat.st_mtime_ns
            size = stat.st_size
        except OSError:
            mtime_ns = None
            size = None

        files[key] = {
            "relativePath": relative_path,
            "fileId": file_index["fileId"],
            "rawContentHash": file_index["contentHash"],
            "mtimeNs": mtime_ns,
            "size": size,
        }

    save_json(
        update_state_path(index_root),
        {
            "schema": UPDATE_STATE_SCHEMA,
            "root": root.resolve().as_posix(),
            "files": dict(sorted(files.items(), key=lambda item: item[0].casefold())),
        },
    )


# ---------------------------------------------------------------------------
# Name extraction from minimal runtime symbols
# ---------------------------------------------------------------------------

def _signature_tokens(signature: str):
    return tokenize_lines([signature])


def _extract_type_name_from_signature(signature: str, keyword: str) -> str | None:
    tokens = _signature_tokens(signature)
    values = token_values(tokens)

    if keyword not in values:
        return None

    index = values.index(keyword) + 1

    # enum class / enum struct
    if keyword == "enum" and index < len(tokens) and tokens[index].value in {"class", "struct"}:
        index += 1

    if index < len(tokens) and tokens[index].kind == "identifier":
        return tokens[index].value

    return None


def _extract_function_name_from_signature(signature: str) -> str | None:
    tokens = _signature_tokens(signature)
    candidates: list[int] = []

    for index, token in enumerate(tokens):
        if token.value != "(":
            continue

        if index == 0:
            continue

        close = find_matching_token(tokens, index, "(", ")")

        if close is None:
            continue

        previous = tokens[index - 1]

        if previous.value in {"decltype", "sizeof", "alignof", "noexcept", "requires"}:
            continue

        candidates.append(index)

    if not candidates:
        return None

    paren_index = candidates[-1]
    short_name, visible_name = extract_function_name(tokens, paren_index)

    return visible_name or short_name or None


def derive_short_name(symbol: dict[str, Any]) -> str | None:
    existing = symbol.get("shortName") or symbol.get("name")

    if isinstance(existing, str) and existing:
        return existing

    symbol_type = symbol.get("type", "")
    signature = str(symbol.get("signature", ""))

    if symbol_type in {"class", "class_declaration"}:
        return _extract_type_name_from_signature(signature, "class")

    if symbol_type in {"struct", "struct_declaration"}:
        return _extract_type_name_from_signature(signature, "struct")

    if symbol_type == "enum":
        return _extract_type_name_from_signature(signature, "enum")

    if symbol_type == "namespace":
        name = _extract_type_name_from_signature(signature, "namespace")
        return name or "<anonymous>"

    if symbol_type in {
        "function",
        "function_declaration",
        "method",
        "method_declaration",
        "constructor",
        "constructor_declaration",
        "destructor",
        "destructor_declaration",
        "operator",
        "operator_declaration",
    }:
        return _extract_function_name_from_signature(signature)

    return None


def qualify_name(container: str | None, short_name: str | None) -> str | None:
    if not short_name:
        return None

    if "::" in short_name:
        return short_name

    if container:
        return f"{container}::{short_name}"

    return short_name


def search_aliases_for_symbol(symbol: dict[str, Any]) -> list[str]:
    short_name = derive_short_name(symbol)
    container = symbol.get("container")
    qualified_name = qualify_name(container, short_name)

    aliases: list[str] = []

    if short_name:
        aliases.append(short_name)

    if qualified_name and qualified_name not in aliases:
        aliases.append(qualified_name)

    # Anonymous namespace convenience alias:
    #   UI::<anonymous@25>::Helper -> UI::Helper
    # This is for search only. The runtime symbol keeps the real container.
    if qualified_name and "::<anonymous" in qualified_name:
        simplified = qualified_name
        while "::<anonymous" in simplified:
            prefix, rest = simplified.split("::<anonymous", 1)
            after = rest.split(">", 1)
            if len(after) != 2:
                break
            simplified = prefix + after[1]

        if simplified and simplified not in aliases:
            aliases.append(simplified)

    return aliases


def data_ref_from_file_data(
    *,
    file_index: dict[str, Any],
    data_item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "dataId": data_item["dataId"],
        "fileId": file_index["fileId"],
        "relativePath": file_index["relativePath"],
        "declarationKind": data_item["declarationKind"],
        "scopeKind": data_item["scopeKind"],
        "name": data_item["name"],
        "qualifiedName": data_item.get("qualifiedName"),
        "container": data_item.get("container"),
        "startLine": data_item["startLine"],
        "endLine": data_item["endLine"],
        "signature": data_item["signature"],
        "typeText": data_item.get("typeText", ""),
        "storage": data_item.get("storage", []),
        "initializerKind": data_item.get("initializerKind", "unknown"),
    }


def search_aliases_for_data(data_item: dict[str, Any]) -> list[str]:
    aliases: list[str] = []

    name = str(data_item.get("name") or "")
    qualified_name = str(data_item.get("qualifiedName") or "")

    if name:
        aliases.append(name)

    if qualified_name and qualified_name not in aliases:
        aliases.append(qualified_name)

    # Anonymous namespace convenience alias, same idea as symbol aliases.
    if qualified_name and "::<anonymous" in qualified_name:
        simplified = qualified_name
        while "::<anonymous" in simplified:
            prefix, rest = simplified.split("::<anonymous", 1)
            after = rest.split(">", 1)
            if len(after) != 2:
                break
            simplified = prefix + after[1]

        if simplified and simplified not in aliases:
            aliases.append(simplified)

    return aliases


def normalize_jobs(jobs: int | None) -> int:
    if jobs is None:
        return 1

    if jobs < 0:
        return 1

    if jobs == 0:
        # Auto mode. Keep this conservative because each worker has to import
        # the scanner modules and can create substantial temporary Python data.
        return min(8, max(1, (os.cpu_count() or 2) - 1))

    return max(1, jobs)


_IDENTIFIER_CHAR_PATTERN = r"[A-Za-z0-9_]"


def compile_source_search_pattern(
    *,
    query: str,
    case_sensitive: bool,
    whole_word: bool,
    use_regex: bool,
) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE

    if use_regex:
        return re.compile(query, flags)

    escaped = re.escape(query)

    if whole_word:
        escaped = rf"(?<!{_IDENTIFIER_CHAR_PATTERN}){escaped}(?!{_IDENTIFIER_CHAR_PATTERN})"

    return re.compile(escaped, flags)


def source_search_worker_count(file_count: int) -> int:
    if file_count <= 1:
        return 1

    cpu_count = os.cpu_count() or 4
    return max(1, min(32, cpu_count, file_count))


def chunked_items(
    items: list[tuple[int, dict[str, Any]]],
    chunk_size: int,
) -> list[list[tuple[int, dict[str, Any]]]]:
    return [items[index:index + chunk_size] for index in range(0, len(items), chunk_size)]


def strip_internal_search_order(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    for match in matches:
        item = dict(match)
        item.pop("_fileOrder", None)
        result.append(item)

    return result


# ---------------------------------------------------------------------------
# Project index build
# ---------------------------------------------------------------------------

def build_file_indexes_for_project(
    *,
    source_files: list[Path],
    root: Path,
    output_root: Path,
    emit_debug_file_indexes: bool,
    case_insensitive_paths: bool,
    blank_comments: bool,
    jobs: int = 1,
    progress_callback: Callable[[int, int, Path], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    files_dir = output_root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    jobs = normalize_jobs(jobs)
    total = len(source_files)
    completed = 0
    file_indexes: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    if total == 0:
        return file_indexes, diagnostics

    if jobs <= 1 or total <= 1:
        for path in source_files:
            completed += 1

            if progress_callback is not None:
                progress_callback(completed, total, path)

            try:
                file_index = build_file_index(
                    path=path,
                    project_root=root,
                    case_insensitive_paths=case_insensitive_paths,
                    blank_comments=blank_comments,
                    emit_debug=emit_debug_file_indexes,
                )
            except Exception as exc:  # noqa: BLE001 - build must continue.
                diagnostics.append(
                    {
                        "severity": "error",
                        "code": "file_index_failed",
                        "message": str(exc),
                        "relativePath": path.relative_to(root).as_posix()
                        if path.is_relative_to(root)
                        else path.as_posix(),
                    }
                )
                continue

            save_json(file_index_output_path(files_dir, file_index["fileId"]), file_index)
            file_indexes.append(file_index)

        file_indexes.sort(key=lambda item: item["relativePath"].casefold())
        return file_indexes, diagnostics

    worker_args = [
        {
            "path": path.as_posix(),
            "root": root.as_posix(),
            "emit_debug_file_indexes": emit_debug_file_indexes,
            "case_insensitive_paths": case_insensitive_paths,
            "blank_comments": blank_comments,
        }
        for path in source_files
    ]

    with ProcessPoolExecutor(max_workers=jobs) as executor:
        future_to_path = {
            executor.submit(build_file_index_worker, payload): Path(payload["path"])
            for payload in worker_args
        }

        for future in as_completed(future_to_path):
            completed += 1
            path = future_to_path[future]

            if progress_callback is not None:
                progress_callback(completed, total, path)

            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - defensive, worker should return errors.
                diagnostics.append(
                    {
                        "severity": "error",
                        "code": "file_index_failed",
                        "message": str(exc),
                        "relativePath": path.relative_to(root).as_posix()
                        if path.is_relative_to(root)
                        else path.as_posix(),
                    }
                )
                continue

            if not result.get("ok"):
                failed_path = Path(str(result.get("path") or path.as_posix()))
                diagnostics.append(
                    {
                        "severity": "error",
                        "code": "file_index_failed",
                        "message": str(result.get("error") or "unknown error"),
                        "relativePath": failed_path.relative_to(root).as_posix()
                        if failed_path.is_relative_to(root)
                        else failed_path.as_posix(),
                    }
                )
                continue

            file_index = result["fileIndex"]
            save_json(file_index_output_path(files_dir, file_index["fileId"]), file_index)
            file_indexes.append(file_index)

    file_indexes.sort(key=lambda item: item["relativePath"].casefold())
    return file_indexes, diagnostics

def build_file_index_worker(payload: dict[str, Any]) -> dict[str, Any]:
    path = Path(payload["path"])
    root = Path(payload["root"])

    try:
        file_index = build_file_index(
            path=path,
            project_root=root,
            case_insensitive_paths=bool(payload["case_insensitive_paths"]),
            blank_comments=bool(payload["blank_comments"]),
            emit_debug=bool(payload["emit_debug_file_indexes"]),
        )

        return {
            "ok": True,
            "path": path.as_posix(),
            "fileIndex": file_index,
        }
    except Exception as exc:  # noqa: BLE001 - parent keeps indexing other files.
        return {
            "ok": False,
            "path": path.as_posix(),
            "error": str(exc),
        }

def file_index_output_path(files_dir: Path, file_id: str) -> Path:
    return files_dir / f"{file_id}.json"


def symbol_ref_from_file_symbol(
    *,
    file_index: dict[str, Any],
    symbol: dict[str, Any],
) -> dict[str, Any]:
    short_name = symbol.get("shortName") or derive_short_name(symbol)
    qualified_name = symbol.get("qualifiedName") or qualify_name(symbol.get("container"), short_name)

    return {
        "symbolId": symbol["symbolId"],
        "fileId": file_index["fileId"],
        "relativePath": file_index["relativePath"],
        "type": symbol["type"],
        "shortName": short_name,
        "qualifiedName": qualified_name,
        "container": symbol.get("container"),
        "startLine": symbol["startLine"],
        "endLine": symbol["endLine"],
        "signature": symbol["signature"],
    }


def build_project_index(
    *,
    root: Path,
    output_root: Path,
    extensions: set[str] | None = None,
    excluded_dir_names: set[str] | None = None,
    include_extensionless_headers: bool = False,
    use_git_ignore: bool = True,
    emit_debug_file_indexes: bool = False,
    case_insensitive_paths: bool = True,
    blank_comments: bool = True,
    progress_callback: Callable[[int, int, Path], None] | None = None,
    discovery_progress_callback: Callable[[int, Path], None] | None = None,
    discovery_complete_callback: Callable[[int], None] | None = None,
    phase_callback: BuildPhaseCallback | None = None,
    jobs: int = 1,
) -> ProjectIndexBuildResult:
    timings: list[dict[str, Any]] = []

    def start_phase(name: str) -> float:
        if phase_callback is not None:
            phase_callback("start", name, None)
        return time.perf_counter()

    def finish_phase(name: str, started_at: float) -> None:
        seconds = time.perf_counter() - started_at
        timings.append({"phase": name, "seconds": round(seconds, 3)})
        if phase_callback is not None:
            phase_callback("complete", name, seconds)

    output_root.mkdir(parents=True, exist_ok=True)
    files_dir = output_root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    phase_started = start_phase("discover files")
    source_files = discover_source_files(
        root,
        extensions=extensions,
        excluded_dir_names=excluded_dir_names,
        include_extensionless_headers=include_extensionless_headers,
        use_git_ignore=use_git_ignore,
        progress_callback=discovery_progress_callback,
    )
    if discovery_complete_callback is not None:
        discovery_complete_callback(len(source_files))
    finish_phase("discover files", phase_started)

    phase_started = start_phase("index source files")
    file_indexes, file_index_failed_diagnostics = build_file_indexes_for_project(
        source_files=source_files,
        root=root,
        output_root=output_root,
        emit_debug_file_indexes=emit_debug_file_indexes,
        case_insensitive_paths=case_insensitive_paths,
        blank_comments=blank_comments,
        jobs=jobs,
        progress_callback=progress_callback,
    )
    finish_phase("index source files", phase_started)

    phase_started = start_phase("aggregate file indexes")
    manifest_files: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    names: dict[str, list[str]] = defaultdict(list)
    modules: dict[str, list[str]] = defaultdict(list)
    data_items: list[dict[str, Any]] = []
    data_names: dict[str, list[str]] = defaultdict(list)
    diagnostics: list[dict[str, Any]] = [*file_index_failed_diagnostics]

    for file_index in file_indexes:
        file_id = file_index["fileId"]

        manifest_files.append(
            {
                "fileId": file_id,
                "relativePath": file_index["relativePath"],
                "contentHash": file_index["contentHash"],
                "lineCount": file_index["lineCount"],
                "tokenCount": file_index.get("tokenCount", 0),
                "unitKind": file_index["module"]["unitKind"],
                "fullModuleName": file_index["module"].get("fullModuleName"),
                "symbols": len(file_index.get("symbols", [])),
                "data": len(file_index.get("data", [])),
                "diagnostics": len(file_index.get("diagnostics", [])),
            }
        )

        full_module_name = file_index["module"].get("fullModuleName")

        if full_module_name:
            modules[full_module_name].append(file_id)

        for diagnostic in file_index.get("diagnostics", []):
            diagnostics.append(
                {
                    "fileId": file_id,
                    "relativePath": file_index["relativePath"],
                    **diagnostic,
                }
            )

        for symbol in file_index.get("symbols", []):
            ref = symbol_ref_from_file_symbol(
                file_index=file_index,
                symbol=symbol,
            )
            symbols.append(ref)

            for alias in search_aliases_for_symbol(symbol):
                if ref["symbolId"] not in names[alias]:
                    names[alias].append(ref["symbolId"])

        for data_item in file_index.get("data", []):
            ref = data_ref_from_file_data(
                file_index=file_index,
                data_item=data_item,
            )
            data_items.append(ref)

            for alias in search_aliases_for_data(data_item):
                if ref["dataId"] not in data_names[alias]:
                    data_names[alias].append(ref["dataId"])
    finish_phase("aggregate file indexes", phase_started)

    phase_started = start_phase("sort manifest files")
    manifest_files.sort(key=lambda item: item["relativePath"].casefold())
    finish_phase("sort manifest files", phase_started)

    phase_started = start_phase("write manifest and maps")
    manifest = {
        "schema": PROJECT_INDEX_SCHEMA,
        "root": root.resolve().as_posix(),
        "filesDir": "files",
        "files": manifest_files,
        "stats": project_stats_from_manifest_files(manifest_files),
        "counts": {
            "files": len(manifest_files),
            "symbols": len(symbols),
            "names": len(names),
            "data": len(data_items),
            "dataNames": len(data_names),
            "modules": len(modules),
            "diagnostics": len(diagnostics),
        },
    }

    save_json(output_root / "manifest.json", manifest)
    save_json(output_root / "modules.json", dict(sorted(modules.items(), key=lambda item: item[0].casefold())))
    save_json(output_root / "diagnostics.json", diagnostics)
    save_update_state_from_file_indexes(
        index_root=output_root,
        root=root,
        file_indexes=file_indexes,
        case_insensitive_paths=case_insensitive_paths,
    )
    finish_phase("write manifest and maps", phase_started)

    phase_started = start_phase("write sqlite lookup index")
    build_sqlite_index(
        index_root=output_root,
        symbols=symbols,
        names=names,
        data_items=data_items,
        data_names=data_names,
        counts=manifest["counts"],
    )
    for legacy_name in ("symbols.jsonl", "names.json", "data.jsonl", "data_names.json"):
        legacy_path = output_root / legacy_name

        if legacy_path.exists():
            legacy_path.unlink()
    finish_phase("write sqlite lookup index", phase_started)

    return ProjectIndexBuildResult(
        root=root,
        output_root=output_root,
        files_count=len(manifest_files),
        symbols_count=len(symbols),
        names_count=len(names),
        data_count=len(data_items),
        data_names_count=len(data_names),
        modules_count=len(modules),
        diagnostics_count=len(diagnostics),
        total_code_lines=manifest["stats"]["totalCodeLines"],
        total_tokens=manifest["stats"]["totalTokens"],
        timings=timings,
    )


def normalize_glob_pattern(pattern: str) -> str:
    pattern = pattern.strip()

    if not pattern:
        return "*"

    return pattern


# ---------------------------------------------------------------------------
# Runtime loader/query helpers used by the server
# ---------------------------------------------------------------------------

class SqliteItemLookup:
    def __init__(self, index: LoadedProjectIndex, table: str, id_column: str) -> None:
        self.index = index
        self.table = table
        self.id_column = id_column

    def get(self, item_id: str, default: Any = None) -> dict[str, Any] | Any:
        item = self.index.sqlite_get_json(self.table, self.id_column, item_id)
        return default if item is None else item


class LoadedProjectIndex:
    def __init__(self, index_root: Path) -> None:
        self.index_root = index_root
        self.files_dir = index_root / "files"
        self.manifest = json.loads((index_root / "manifest.json").read_text(encoding="utf-8"))
        self.modules: dict[str, list[str]] = json.loads((index_root / "modules.json").read_text(encoding="utf-8"))
        self.file_by_id = {item["fileId"]: item for item in self.manifest["files"]}
        self.file_id_by_relative_path = {item["relativePath"]: item["fileId"] for item in self.manifest["files"]}
        self.sqlite_path = sqlite_index_path(index_root)
        self.sqlite_connections = (
            ThreadLocalIndexConnections(self.sqlite_path)
            if self.sqlite_path.exists()
            else None
        )
        self.uses_sqlite = self.sqlite_connections is not None

        if self.uses_sqlite:
            self.names: dict[str, list[str]] = {}
            self.symbols: list[dict[str, Any]] = []
            self.symbol_by_id = SqliteItemLookup(self, "symbols", "symbolId")
            self.data: list[dict[str, Any]] = []
            self.data_names: dict[str, list[str]] = {}
            self.data_by_id = SqliteItemLookup(self, "data", "dataId")
        else:
            self.names = json.loads((index_root / "names.json").read_text(encoding="utf-8"))
            self.symbols = self._load_symbols(index_root / "symbols.jsonl")
            self.symbol_by_id = {symbol["symbolId"]: symbol for symbol in self.symbols}
            self.data = self._load_jsonl_if_exists(index_root / "data.jsonl")
            self.data_names = self._load_json_if_exists(index_root / "data_names.json", {})
            self.data_by_id = {item["dataId"]: item for item in self.data}

    def close(self) -> None:
        if self.sqlite_connections is not None:
            self.sqlite_connections.close()

    def sqlite_connection(self):
        if self.sqlite_connections is None:
            return None

        return self.sqlite_connections.get()

    @staticmethod
    def _load_symbols(path: Path) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()

                if not line:
                    continue

                result.append(json.loads(line))

        return result

    @staticmethod
    def _load_jsonl_if_exists(path: Path) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        if not path.exists():
            return result

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()

                if not line:
                    continue

                result.append(json.loads(line))

        return result


    @staticmethod
    def _load_json_if_exists(path: Path, default: Any) -> Any:
        if not path.exists():
            return default

        return json.loads(path.read_text(encoding="utf-8"))

    def sqlite_get_json(self, table: str, id_column: str, item_id: str) -> dict[str, Any] | None:
        connection = self.sqlite_connection()

        if connection is None:
            return None

        if table not in {"symbols", "data"} or id_column not in {"symbolId", "dataId"}:
            raise ValueError("Invalid SQLite lookup table.")

        row = connection.execute(
            f"SELECT * FROM {table} WHERE {id_column} = ?",
            (item_id,),
        ).fetchone()
        return row_json(row)

    def sqlite_symbol_ids_for_name(self, query: str) -> list[str]:
        connection = self.sqlite_connection()

        if connection is None:
            return []

        rows = connection.execute(
            "SELECT symbolId FROM symbol_names WHERE name = ? ORDER BY ordinal",
            (query,),
        ).fetchall()
        return [str(row["symbolId"]) for row in rows]

    def sqlite_data_ids_for_name(self, query: str) -> list[str]:
        connection = self.sqlite_connection()

        if connection is None:
            return []

        rows = connection.execute(
            "SELECT dataId FROM data_names WHERE name = ? ORDER BY ordinal",
            (query,),
        ).fetchall()
        return [str(row["dataId"]) for row in rows]

    def sqlite_iter_symbols(self) -> Iterable[dict[str, Any]]:
        connection = self.sqlite_connection()

        if connection is None:
            return iter(())

        rows = connection.execute(
            """
            SELECT * FROM symbols
            ORDER BY COALESCE(qualifiedName, shortName, ''), relativePath, startLine, endLine
            """
        )
        return (item for row in rows if (item := row_json(row)) is not None)

    def sqlite_iter_data(self) -> Iterable[dict[str, Any]]:
        connection = self.sqlite_connection()

        if connection is None:
            return iter(())

        rows = connection.execute(
            """
            SELECT * FROM data
            ORDER BY COALESCE(qualifiedName, name, ''), relativePath, startLine, endLine
            """
        )
        return (item for row in rows if (item := row_json(row)) is not None)

    def sqlite_symbols_for_file(self, file_id: str) -> list[dict[str, Any]]:
        connection = self.sqlite_connection()

        if connection is None:
            return []

        rows = connection.execute(
            """
            SELECT * FROM symbols
            WHERE fileId = ?
            ORDER BY startLine, endLine, COALESCE(qualifiedName, shortName, '')
            """,
            (file_id,),
        ).fetchall()
        return [item for row in rows if (item := row_json(row)) is not None]

    def sqlite_data_for_file(self, file_id: str) -> list[dict[str, Any]]:
        connection = self.sqlite_connection()

        if connection is None:
            return []

        rows = connection.execute(
            """
            SELECT * FROM data
            WHERE fileId = ?
            ORDER BY startLine, endLine, COALESCE(qualifiedName, name, '')
            """,
            (file_id,),
        ).fetchall()
        return [item for row in rows if (item := row_json(row)) is not None]

    def load_file_index(self, file_id: str) -> dict[str, Any]:
        path = self.files_dir / f"{file_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def find_symbol(
        self,
        query: str,
        *,
        limit: int = 20,
        symbol_types: set[str] | None = None,
        container: str | None = None,
        file: str | None = None,
        file_pattern: str | None = None,
        exact_only: bool = False,
        hide_namespaces: bool = False,
        compact: bool = False,
    ) -> list[dict[str, Any]]:
        file_id_filter: str | None = None

        if file:
            file_item = self.get_file_item(file)

            if file_item is None:
                return []

            file_id_filter = str(file_item["fileId"])

        normalized_file_pattern = normalize_glob_pattern(file_pattern).casefold() if file_pattern else None
        direct_ids = self.sqlite_symbol_ids_for_name(query) if self.uses_sqlite else self.names.get(query, [])
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        def maybe_add(symbol: dict[str, Any]) -> bool:
            symbol_id = symbol["symbolId"]

            if symbol_id in seen:
                return False

            if not symbol_matches_type_filter(symbol, symbol_types):
                return False

            if not symbol_matches_namespace_filter(symbol, hide_namespaces):
                return False

            if not symbol_matches_container_filter(symbol, container):
                return False

            if file_id_filter is not None and symbol.get("fileId") != file_id_filter:
                return False

            if normalized_file_pattern is not None:
                relative_path = str(symbol.get("relativePath") or "").casefold()

                if not fnmatchcase(relative_path, normalized_file_pattern):
                    return False

            match_kind = symbol_match_kind(symbol, query)

            if exact_only and not is_exact_symbol_match(symbol, query):
                return False

            item = dict(symbol)
            item["matchKind"] = match_kind

            seen.add(symbol_id)
            results.append(compact_symbol_ref(item) if compact else item)
            return len(results) >= limit

        for symbol_id in direct_ids:
            symbol = self.symbol_by_id.get(symbol_id)

            if symbol is None:
                continue

            if maybe_add(symbol):
                break

        if len(results) < limit:
            query_folded = query.casefold()

            symbol_iter = self.sqlite_iter_symbols() if self.uses_sqlite else iter(self.symbols)

            for symbol in symbol_iter:
                symbol_id = symbol["symbolId"]

                if symbol_id in seen:
                    continue

                if not symbol_matches_type_filter(symbol, symbol_types):
                    continue

                if not symbol_matches_namespace_filter(symbol, hide_namespaces):
                    continue

                if not symbol_matches_container_filter(symbol, container):
                    continue

                if file_id_filter is not None and symbol.get("fileId") != file_id_filter:
                    continue

                if normalized_file_pattern is not None:
                    relative_path = str(symbol.get("relativePath") or "").casefold()

                    if not fnmatchcase(relative_path, normalized_file_pattern):
                        continue

                haystacks = [
                    str(symbol.get("shortName") or ""),
                    str(symbol.get("qualifiedName") or ""),
                    str(symbol.get("signature") or ""),
                ]

                if not any(query_folded in value.casefold() for value in haystacks):
                    continue

                if maybe_add(symbol):
                    break

        results.sort(
            key=lambda item: (
                symbol_match_rank(str(item.get("matchKind") or "metadata_match")),
                str(item.get("qualifiedName") or item.get("shortName") or ""),
                str(item.get("relativePath") or ""),
                int(item.get("startLine") or 0),
            )
        )

        return results[:limit]

    def list_file_symbols(
        self,
        file: str,
        *,
        limit: int = 500,
        symbol_types: set[str] | None = None,
        container: str | None = None,
        hide_namespaces: bool = False,
        compact: bool = False,
    ) -> list[dict[str, Any]]:
        file_id = file

        if file_id not in self.file_by_id:
            file_id = self.file_id_by_relative_path.get(file, "")

        if not file_id:
            return []

        results: list[dict[str, Any]] = []

        file_symbols = self.sqlite_symbols_for_file(file_id) if self.uses_sqlite else self.symbols

        for symbol in file_symbols:
            if symbol.get("fileId") != file_id:
                continue

            if not symbol_matches_type_filter(symbol, symbol_types):
                continue

            if not symbol_matches_namespace_filter(symbol, hide_namespaces):
                continue

            if not symbol_matches_container_filter(symbol, container):
                continue

            results.append(compact_symbol_ref(symbol) if compact else symbol)

            if len(results) >= limit:
                break

        results.sort(
            key=lambda item: (
                int(item.get("startLine") or 0),
                int(item.get("endLine") or 0),
                str(item.get("qualifiedName") or item.get("shortName") or ""),
            )
        )

        return results

    def find_module(self, module_name: str) -> list[dict[str, Any]]:
        file_ids = self.modules.get(module_name, [])

        return [
            self.file_by_id[file_id]
            for file_id in file_ids
            if file_id in self.file_by_id
        ]

    def read_range(self, *, project_root: Path, file: str, start_line: int, end_line: int) -> str:
        file_id = file

        if file_id in self.file_by_id:
            relative_path = self.file_by_id[file_id]["relativePath"]
        else:
            relative_path = file

        path = project_root / relative_path
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start_line = max(1, start_line)
        end_line = min(len(lines), end_line)

        if start_line > end_line:
            return ""

        return "\n".join(
            f"{line_no:04d}: {lines[line_no - 1]}"
            for line_no in range(start_line, end_line + 1)
        )

    def read_symbol(self, *, project_root: Path, symbol_id: str) -> str:
        symbol = self.symbol_by_id.get(symbol_id)

        if symbol is None:
            return ""

        return self.read_range(
            project_root=project_root,
            file=symbol["fileId"],
            start_line=symbol["startLine"],
            end_line=symbol["endLine"],
        )

    def find_files(self, pattern: str, *, limit: int = 100) -> list[dict[str, Any]]:
        pattern = normalize_glob_pattern(pattern).casefold()
        results: list[dict[str, Any]] = []

        for file_item in self.manifest["files"]:
            relative_path = str(file_item.get("relativePath") or "")
            haystack = relative_path.casefold()

            if fnmatchcase(haystack, pattern):
                results.append(file_item)

                if len(results) >= limit:
                    break

        return results

    def find_symbols_glob(
        self,
        pattern: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        pattern = normalize_glob_pattern(pattern).casefold()
        results: list[dict[str, Any]] = []

        symbol_iter = self.sqlite_iter_symbols() if self.uses_sqlite else iter(self.symbols)

        for symbol in symbol_iter:
            haystacks = [
                str(symbol.get("shortName") or ""),
                str(symbol.get("qualifiedName") or ""),
                str(symbol.get("container") or ""),
                str(symbol.get("signature") or ""),
                str(symbol.get("relativePath") or ""),
            ]

            if any(fnmatchcase(value.casefold(), pattern) for value in haystacks):
                results.append(symbol)

                if len(results) >= limit:
                    break

        return results

    def search_modules(self, pattern: str, *, limit: int = 100) -> list[dict[str, Any]]:
        pattern = normalize_glob_pattern(pattern).casefold()
        results: list[dict[str, Any]] = []
        seen_file_ids: set[str] = set()

        for module_name, file_ids in self.modules.items():
            if not fnmatchcase(module_name.casefold(), pattern):
                continue

            for file_id in file_ids:
                if file_id in seen_file_ids:
                    continue

                file_item = self.file_by_id.get(file_id)

                if file_item is None:
                    continue

                seen_file_ids.add(file_id)

                results.append(
                    {
                        "moduleName": module_name,
                        **file_item,
                    }
                )

                if len(results) >= limit:
                    return results

        return results

    def find_data(
        self,
        query: str,
        *,
        container: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        direct_ids = self.sqlite_data_ids_for_name(query) if self.uses_sqlite else self.data_names.get(query, [])
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        def matches_container(item: dict[str, Any]) -> bool:
            if not container:
                return True

            item_container = str(item.get("container") or "")
            item_qualified_name = str(item.get("qualifiedName") or "")
            container_folded = container.casefold()

            return (
                item_container.casefold() == container_folded
                or item_container.casefold().endswith("::" + container_folded)
                or item_qualified_name.casefold().startswith(container_folded + "::")
            )

        for data_id in direct_ids:
            item = self.data_by_id.get(data_id)

            if item is None or data_id in seen:
                continue

            if not matches_container(item):
                continue

            seen.add(data_id)
            results.append(item)

            if len(results) >= limit:
                return results

        query_folded = query.casefold()

        data_iter = self.sqlite_iter_data() if self.uses_sqlite else iter(self.data)

        for item in data_iter:
            data_id = item["dataId"]

            if data_id in seen:
                continue

            if not matches_container(item):
                continue

            haystacks = [
                str(item.get("name") or ""),
                str(item.get("qualifiedName") or ""),
                str(item.get("container") or ""),
                str(item.get("signature") or ""),
                str(item.get("typeText") or ""),
            ]

            if any(query_folded in value.casefold() for value in haystacks):
                seen.add(data_id)
                results.append(item)

                if len(results) >= limit:
                    break

        return results


    def list_type_members(self, container: str, *, limit: int = 500) -> list[dict[str, Any]]:
        container_folded = container.casefold()
        results: list[dict[str, Any]] = []

        data_iter = self.sqlite_iter_data() if self.uses_sqlite else iter(self.data)

        for item in data_iter:
            item_container = str(item.get("container") or "")

            if (
                item_container.casefold() == container_folded
                or item_container.casefold().endswith("::" + container_folded)
            ):
                results.append(item)

                if len(results) >= limit:
                    break

        results.sort(
            key=lambda item: (
                int(item.get("startLine") or 0),
                str(item.get("name") or ""),
            )
        )
        return results


    def read_data(self, *, project_root: Path, data_id: str) -> str:
        item = self.data_by_id.get(data_id)

        if item is None:
            return ""

        return self.read_range(
            project_root=project_root,
            file=item["fileId"],
            start_line=int(item["startLine"]),
            end_line=int(item["endLine"]),
        )

    def resolve_relative_path(self, file: str) -> str:
        if file in self.file_by_id:
            return str(self.file_by_id[file]["relativePath"])

        return file


    def read_file_lines(self, *, project_root: Path, file: str) -> tuple[str, list[str]]:
        relative_path = self.resolve_relative_path(file)
        path = project_root / relative_path
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return relative_path, lines


    def get_symbol_leading_comment(
        self,
        *,
        project_root: Path,
        symbol_id: str,
        max_lines: int = 20,
        allow_blank_gap: bool = True,
    ) -> dict[str, Any] | None:
        symbol = self.symbol_by_id.get(symbol_id)

        if symbol is None:
            return None

        relative_path, lines = self.read_file_lines(
            project_root=project_root,
            file=symbol["fileId"],
        )

        result = extract_leading_comment(
            lines=lines,
            relative_path=relative_path,
            target_start_line=int(symbol["startLine"]),
            target_end_line=int(symbol["endLine"]),
            max_lines=max_lines,
            allow_blank_gap=allow_blank_gap,
        )
        result["symbolId"] = symbol_id
        result["symbolType"] = symbol.get("type")
        result["qualifiedName"] = symbol.get("qualifiedName")
        result["signature"] = symbol.get("signature")
        return result


    def get_data_leading_comment(
        self,
        *,
        project_root: Path,
        data_id: str,
        max_lines: int = 20,
        allow_blank_gap: bool = True,
    ) -> dict[str, Any] | None:
        data_item = self.data_by_id.get(data_id)

        if data_item is None:
            return None

        relative_path, lines = self.read_file_lines(
            project_root=project_root,
            file=data_item["fileId"],
        )

        result = extract_leading_comment(
            lines=lines,
            relative_path=relative_path,
            target_start_line=int(data_item["startLine"]),
            target_end_line=int(data_item["endLine"]),
            max_lines=max_lines,
            allow_blank_gap=allow_blank_gap,
        )
        result["dataId"] = data_id
        result["declarationKind"] = data_item.get("declarationKind")
        result["qualifiedName"] = data_item.get("qualifiedName")
        result["signature"] = data_item.get("signature")
        result["typeText"] = data_item.get("typeText")
        return result


    def get_file_header_comment(
        self,
        *,
        project_root: Path,
        file: str,
        max_lines: int = 120,
    ) -> dict[str, Any]:
        relative_path, lines = self.read_file_lines(
            project_root=project_root,
            file=file,
        )

        return extract_file_header_comment(
            lines=lines,
            relative_path=relative_path,
            max_lines=max_lines,
        )


    def get_module_header_comment(
        self,
        *,
        project_root: Path,
        module_name: str,
        max_lines: int = 120,
    ) -> dict[str, Any]:
        files = self.find_module(module_name)
        results: list[dict[str, Any]] = []

        for file_item in files:
            result = self.get_file_header_comment(
                project_root=project_root,
                file=str(file_item["fileId"]),
                max_lines=max_lines,
            )
            result["fileId"] = file_item["fileId"]
            result["unitKind"] = file_item.get("unitKind")
            result["fullModuleName"] = file_item.get("fullModuleName")
            results.append(result)

        return {
            "moduleName": module_name,
            "results": results,
        }


    def get_file_item(self, file: str) -> dict[str, Any] | None:
        file_id = file

        if file_id in self.file_by_id:
            return self.file_by_id[file_id]

        file_id = self.file_id_by_relative_path.get(file, "")

        if not file_id:
            return None

        return self.file_by_id.get(file_id)


    def get_file_structure(
        self,
        file: str,
        *,
        include_outline: bool = True,
        outline_limit: int = 500,
        compact_outline: bool = True,
        symbol_types: set[str] | None = None,
        data_kinds: set[str] | None = None,
        include_data: bool = True,
        include_diagnostics: bool = True,
        hide_namespaces: bool = False,
        include_debug: bool = False,
        debug_kinds: set[str] | None = None,
        debug_start_line: int | None = None,
        debug_end_line: int | None = None,
        debug_limit: int = 200,
        compact_debug: bool = True,
    ) -> dict[str, Any] | None:
        file_item = self.get_file_item(file)

        if file_item is None:
            return None

        file_id = file_item["fileId"]
        relative_path = file_item["relativePath"]
        outline_limit = max(1, outline_limit)
        debug_limit = max(1, debug_limit)

        if self.uses_sqlite:
            all_file_symbols = self.sqlite_symbols_for_file(file_id)
            all_file_data = self.sqlite_data_for_file(file_id)
        else:
            all_file_symbols = [
                symbol
                for symbol in self.symbols
                if symbol.get("fileId") == file_id
            ]
            all_file_data = [
                item
                for item in self.data
                if item.get("fileId") == file_id
            ]

        file_symbols = [
            symbol
            for symbol in all_file_symbols
            if symbol_matches_type_filter(symbol, symbol_types)
            and symbol_matches_namespace_filter(symbol, hide_namespaces)
        ]

        if include_data:
            file_data = [
                item
                for item in all_file_data
                if data_matches_kind_filter(item, data_kinds)
            ]
        else:
            file_data = []

        file_symbols.sort(
            key=lambda item: (
                int(item.get("startLine") or 0),
                int(item.get("endLine") or 0),
                str(item.get("qualifiedName") or item.get("shortName") or ""),
            )
        )
        file_data.sort(
            key=lambda item: (
                int(item.get("startLine") or 0),
                int(item.get("endLine") or 0),
                str(item.get("qualifiedName") or item.get("name") or ""),
            )
        )

        diagnostics: list[dict[str, Any]] = []

        if include_diagnostics:
            diagnostics = [
                diagnostic
                for diagnostic in getattr(
                    self,
                    "diagnostics",
                    self._load_json_if_exists(self.index_root / "diagnostics.json", []),
                )
                if diagnostic.get("fileId") == file_id or diagnostic.get("relativePath") == relative_path
            ]

        symbol_counts_all: dict[str, int] = {}
        data_counts_all: dict[str, int] = {}
        symbol_counts_filtered: dict[str, int] = {}
        data_counts_filtered: dict[str, int] = {}

        for symbol in all_file_symbols:
            symbol_type = str(symbol.get("type") or "unknown")
            symbol_counts_all[symbol_type] = symbol_counts_all.get(symbol_type, 0) + 1

        for item in all_file_data:
            declaration_kind = str(item.get("declarationKind") or "unknown")
            data_counts_all[declaration_kind] = data_counts_all.get(declaration_kind, 0) + 1

        for symbol in file_symbols:
            symbol_type = str(symbol.get("type") or "unknown")
            symbol_counts_filtered[symbol_type] = symbol_counts_filtered.get(symbol_type, 0) + 1

        for item in file_data:
            declaration_kind = str(item.get("declarationKind") or "unknown")
            data_counts_filtered[declaration_kind] = data_counts_filtered.get(declaration_kind, 0) + 1

        outline: list[dict[str, Any]] = []

        if include_outline:
            for symbol in file_symbols:
                outline.append(
                    {
                        "kind": "symbol",
                        "symbolId": symbol.get("symbolId"),
                        "type": symbol.get("type"),
                        "name": symbol.get("qualifiedName") or symbol.get("shortName"),
                        "shortName": symbol.get("shortName"),
                        "container": symbol.get("container"),
                        "relativePath": relative_path,
                        "startLine": symbol.get("startLine"),
                        "endLine": symbol.get("endLine"),
                        "signature": symbol.get("signature"),
                    }
                )

            for item in file_data:
                outline.append(
                    {
                        "kind": "data",
                        "dataId": item.get("dataId"),
                        "declarationKind": item.get("declarationKind"),
                        "scopeKind": item.get("scopeKind"),
                        "name": item.get("qualifiedName") or item.get("name"),
                        "shortName": item.get("name"),
                        "container": item.get("container"),
                        "typeText": item.get("typeText"),
                        "relativePath": relative_path,
                        "startLine": item.get("startLine"),
                        "endLine": item.get("endLine"),
                        "signature": item.get("signature"),
                    }
                )

            outline.sort(
                key=lambda item: (
                    int(item.get("startLine") or 0),
                    0 if item.get("kind") == "symbol" else 1,
                    str(item.get("name") or ""),
                )
            )

        outline_truncated = False

        if include_outline and len(outline) > outline_limit:
            outline = outline[:outline_limit]
            outline_truncated = True

        if compact_outline:
            outline = [compact_file_structure_item(item) for item in outline]

        section_source_items: list[dict[str, Any]] = []

        for symbol in file_symbols:
            section_source_items.append(
                {
                    "kind": "symbol",
                    "type": symbol.get("type"),
                    "startLine": symbol.get("startLine"),
                    "endLine": symbol.get("endLine"),
                }
            )

        for item in file_data:
            section_source_items.append(
                {
                    "kind": "data",
                    "declarationKind": item.get("declarationKind"),
                    "startLine": item.get("startLine"),
                    "endLine": item.get("endLine"),
                }
            )

        sections: list[dict[str, Any]] = []

        def append_section(name: str, items: list[dict[str, Any]]) -> None:
            if not items:
                return

            sections.append(
                {
                    "name": name,
                    "startLine": min(int(item.get("startLine") or 0) for item in items),
                    "endLine": max(int(item.get("endLine") or 0) for item in items),
                    "count": len(items),
                }
            )

        append_section(
            "types",
            [
                item
                for item in section_source_items
                if item.get("kind") == "symbol"
                and item.get("type") in {"class", "struct", "enum", "class_declaration", "struct_declaration", "type_alias", "type_alias_template", "typedef_declaration"}
            ],
        )
        append_section(
            "functions",
            [
                item
                for item in section_source_items
                if item.get("kind") == "symbol"
                and item.get("type") in {"function", "method", "constructor", "destructor", "operator", "function_declaration", "method_declaration", "constructor_declaration", "destructor_declaration", "operator_declaration"}
            ],
        )
        append_section(
            "data",
            [item for item in section_source_items if item.get("kind") == "data"],
        )

        result = {
            "fileId": file_id,
            "relativePath": relative_path,
            "lineCount": file_item.get("lineCount"),
            "module": {
                "unitKind": file_item.get("unitKind"),
                "fullModuleName": file_item.get("fullModuleName"),
            },
            "filters": {
                "includeOutline": include_outline,
                "outlineLimit": outline_limit,
                "compactOutline": compact_outline,
                "symbolTypes": sorted(symbol_types) if symbol_types else None,
                "dataKinds": sorted(data_kinds) if data_kinds else None,
                "includeData": include_data,
                "includeDiagnostics": include_diagnostics,
                "hideNamespaces": hide_namespaces,
                "includeDebug": include_debug,
                "debugKinds": sorted(debug_kinds) if debug_kinds else None,
                "debugStartLine": debug_start_line,
                "debugEndLine": debug_end_line,
                "debugLimit": debug_limit,
                "compactDebug": compact_debug,
            },
            "counts": {
                "symbols": len(file_symbols),
                "data": len(file_data),
                "diagnostics": len(diagnostics),
                "allSymbols": len(all_file_symbols),
                "allData": len(all_file_data),
                "symbolsByType": dict(sorted(symbol_counts_filtered.items())),
                "dataByKind": dict(sorted(data_counts_filtered.items())),
                "allSymbolsByType": dict(sorted(symbol_counts_all.items())),
                "allDataByKind": dict(sorted(data_counts_all.items())),
            },
            "sections": sections,
            "outlineTruncated": outline_truncated,
            "diagnostics": diagnostics,
        }

        if include_outline:
            result["outline"] = outline

        if include_debug:
            result["debug"] = self.file_debug_structure(
                file_id=file_id,
                diagnostics=diagnostics,
                debug_kinds=debug_kinds,
                debug_start_line=debug_start_line,
                debug_end_line=debug_end_line,
                debug_limit=debug_limit,
                compact_debug=compact_debug,
            )

        return result

    def file_debug_structure(
        self,
        *,
        file_id: str,
        diagnostics: list[dict[str, Any]],
        debug_kinds: set[str] | None,
        debug_start_line: int | None,
        debug_end_line: int | None,
        debug_limit: int,
        compact_debug: bool,
    ) -> dict[str, Any]:
        requested_kinds = debug_kinds or set(FILE_DEBUG_KINDS)
        requested_kinds = {
            kind
            for kind in requested_kinds
            if kind in FILE_DEBUG_KINDS
        }
        file_index = self.load_file_index(file_id)
        available_kinds = [
            kind
            for kind in sorted(FILE_DEBUG_KINDS)
            if kind == "diagnostics" or kind in file_index
        ]

        if not any(kind in file_index for kind in FILE_DEBUG_KINDS - {"diagnostics"}):
            return {
                "available": False,
                "reason": "File index does not contain indexer diagnostic sections. Rebuild with --emit-diagnostic-file-indexes.",
                "availableKinds": available_kinds,
                "requestedKinds": sorted(requested_kinds),
            }

        result: dict[str, Any] = {
            "available": True,
            "availableKinds": available_kinds,
            "requestedKinds": sorted(requested_kinds),
            "compact": compact_debug,
            "startLine": debug_start_line,
            "endLine": debug_end_line,
            "limit": debug_limit,
            "truncated": False,
            "counts": {},
        }
        remaining = debug_limit

        for kind in sorted(requested_kinds):
            if kind == "diagnostics":
                source_items = diagnostics
            else:
                source_items = file_index.get(kind, [])

            if not isinstance(source_items, list):
                source_items = []

            filtered_items = [
                normalize_debug_item(item)
                for item in source_items
                if isinstance(item, dict)
                and item_overlaps_line_filter(item, debug_start_line, debug_end_line)
            ]
            result["counts"][kind] = len(filtered_items)

            if remaining <= 0:
                result[kind] = []
                result["truncated"] = result["truncated"] or bool(filtered_items)
                continue

            emitted = filtered_items[:remaining]
            remaining -= len(emitted)

            if len(emitted) < len(filtered_items):
                result["truncated"] = True

            if compact_debug:
                result[kind] = [
                    compact_file_debug_item(item)
                    for item in emitted
                ]
            else:
                result[kind] = emitted

        return result

    def iter_search_files(
        self,
        *,
        file: str | None = None,
        file_pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        if file:
            file_item = self.get_file_item(file) if hasattr(self, "get_file_item") else None

            if file_item is None:
                file_id = file

                if file_id not in self.file_by_id:
                    file_id = self.file_id_by_relative_path.get(file, "")

                if not file_id:
                    return []

                file_item = self.file_by_id.get(file_id)

            return [file_item] if file_item is not None else []

        pattern = normalize_glob_pattern(file_pattern or "*").casefold()
        results: list[dict[str, Any]] = []

        for file_item in self.manifest["files"]:
            relative_path = str(file_item.get("relativePath") or "")

            if fnmatchcase(relative_path.casefold(), pattern):
                results.append(file_item)

        return results


    def search_source(
        self,
        *,
        project_root: Path,
        query: str,
        file: str | None = None,
        file_pattern: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        case_sensitive: bool = False,
        whole_word: bool = False,
        use_regex: bool = False,
        limit: int = 100,
        context_lines: int = 0,
    ) -> dict[str, Any]:
        """Search raw source text in indexed files.

        This is a plain line-based source search. It is not semantic C++ reference
        resolution and it intentionally does not ignore comments or strings.
        """

        query = query.strip()

        if not query:
            return {
                "query": query,
                "caseSensitive": case_sensitive,
                "file": file,
                "filePattern": file_pattern,
                "searchedFiles": 0,
                "returnedMatches": 0,
                "truncated": False,
                "matches": [],
            }

        limit = max(1, limit)
        context_lines = max(0, context_lines)
        pattern = compile_source_search_pattern(
            query=query,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            use_regex=use_regex,
        )
        matches: list[dict[str, Any]] = []
        searched_files = 0
        search_files = list(enumerate(self.iter_search_files(file=file, file_pattern=file_pattern)))

        def scan_file(file_order: int, file_item: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
            relative_path = str(file_item.get("relativePath") or "")
            path = project_root / relative_path

            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                return 0, []

            search_start = max(1, start_line or 1)
            search_end = min(len(lines), end_line or len(lines))

            if search_end < search_start:
                return 1, []

            file_matches: list[dict[str, Any]] = []
            for index in range(search_start - 1, search_end):
                line = lines[index]
                if pattern.search(line) is None:
                    continue

                line_no = index + 1
                context_start = max(1, line_no - context_lines)
                context_end = min(len(lines), line_no + context_lines)
                context_source = format_source_lines(lines, context_start, context_end)

                matches.append(
                    {
                        "relativePath": relative_path,
                        "fileId": file_item.get("fileId"),
                        "line": line_no,
                        "source": f"{line_no:04d}: {line}",
                        "contextStartLine": context_start,
                        "contextEndLine": context_end,
                        "context": context_source,
                        "_fileOrder": file_order,
                    }
                )

                if len(file_matches) >= limit:
                    break

            return 1, file_matches

        def scan_chunk(chunk: list[tuple[int, dict[str, Any]]]) -> tuple[int, list[dict[str, Any]]]:
            chunk_searched_files = 0
            chunk_matches: list[dict[str, Any]] = []

            for file_order, file_item in chunk:
                file_searched_files, file_matches = scan_file(file_order, file_item)
                chunk_searched_files += file_searched_files
                chunk_matches.extend(file_matches)

                if len(chunk_matches) >= limit:
                    break

            return chunk_searched_files, chunk_matches

        use_parallel = (
            file is None
            and start_line is None
            and end_line is None
            and len(search_files) > 64
        )
        truncated = False

        if use_parallel:
            workers = source_search_worker_count(len(search_files))
            chunk_size = max(1, len(search_files) // (workers * 8))
            chunks = chunked_items(search_files, chunk_size)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(scan_chunk, chunk) for chunk in chunks]

                for future in futures:
                    chunk_searched_files, chunk_matches = future.result()
                    searched_files += chunk_searched_files
                    matches.extend(chunk_matches)

                    if len(matches) >= limit:
                        truncated = True
                        for pending in futures:
                            pending.cancel()
                        break
        else:
            for file_item in search_files:
                file_searched_files, file_matches = scan_file(*file_item)
                searched_files += file_searched_files
                matches.extend(file_matches)

                if len(matches) >= limit:
                    truncated = True
                    break

        matches.sort(key=lambda item: (item.get("_fileOrder", 0), item["line"]))
        returned_matches = strip_internal_search_order(matches[:limit])

        if len(matches) > limit:
            truncated = True

        return {
            "query": query,
            "caseSensitive": case_sensitive,
            "wholeWord": whole_word,
            "useRegex": use_regex,
            "file": file,
            "filePattern": file_pattern,
            "searchedFiles": searched_files,
            "returnedMatches": len(returned_matches),
            "truncated": truncated,
            "parallel": use_parallel,
            "matches": returned_matches,
        }
