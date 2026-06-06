from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpp_file_index import build_file_index
from cpp_index_lock import IndexLockError, index_update_lock
from cpp_index_sqlite import build_sqlite_index, replace_file_lookup_rows, replace_orientation_nodes, sqlite_index_path
from cpp_index_utils import save_json
from cpp_orientation_index import build_orientation_index
from cpp_project_index import (
    DEFAULT_EXCLUDED_DIR_NAMES,
    DEFAULT_SOURCE_EXTENSIONS,
    PROJECT_INDEX_SCHEMA,
    UPDATE_STATE_SCHEMA,
    discover_source_files,
    file_index_output_path,
    search_aliases_for_symbol,
    symbol_ref_from_file_symbol,
    data_ref_from_file_data,
    search_aliases_for_data,
    build_file_indexes_for_project,
    normalize_jobs,
    project_stats_from_manifest,
    project_stats_from_manifest_files,
    update_state_path,
)


DEFAULT_INDEX_DIR_NAME = ".mcp-cpp-project-indexer"


def trim_progress_line(prefix: str, relative: str) -> str:
    width = max(40, shutil.get_terminal_size(fallback=(120, 20)).columns - 1)
    available = max(10, width - len(prefix))

    if len(relative) > available:
        relative = "..." + relative[-max(0, available - 3):]

    return prefix + relative


class ProgressLineWriter:
    def __init__(self) -> None:
        self.last_width = 0

    def write(self, text: str) -> None:
        width = max(40, shutil.get_terminal_size(fallback=(120, 20)).columns - 1)
        text = text[:width]
        clear_width = max(self.last_width, len(text), width)
        sys.stderr.write("\r" + (" " * clear_width) + "\r" + text)
        sys.stderr.flush()
        self.last_width = len(text)

    def finish(self) -> None:
        sys.stderr.write("\n")
        sys.stderr.flush()
        self.last_width = 0

    def clear(self) -> None:
        self.write("")


_PROGRESS_LINE = ProgressLineWriter()


def write_progress_line(text: str) -> None:
    _PROGRESS_LINE.write(text)


def finish_progress_line() -> None:
    _PROGRESS_LINE.finish()


def clear_progress_line() -> None:
    _PROGRESS_LINE.clear()


@dataclass(slots=True)
class UpdatePlan:
    added: list[Path]
    modified: list[Path]
    deleted_relative_paths: list[str]
    unchanged: list[Path]
    state_initialized: bool


@dataclass(slots=True)
class UpdateResult:
    added: int
    modified: int
    deleted: int
    unchanged: int
    files: int
    symbols: int
    names: int
    data: int
    data_names: int
    modules: int
    diagnostics: int
    state_initialized: bool
    incremental_aggregation_timings: list[dict[str, Any]]
    structural_unchanged: bool


class UpdateProgress:
    def __init__(self, *, root: Path, enabled: bool) -> None:
        self.root = root
        self.enabled = enabled
        self.frames = "|/-\\"
        self.started = time.monotonic()
        self.last_update = 0.0
        self.tick = 0
        self.active = False

    def _relative(self, path: Path) -> str:
        try:
            relative = path.relative_to(self.root).as_posix()
        except ValueError:
            relative = path.as_posix()

        return relative

    def status(self, text: str) -> None:
        if not self.enabled:
            return

        now = time.monotonic()
        self.last_update = now
        self.tick += 1
        self.active = True
        frame = self.frames[self.tick % len(self.frames)]
        elapsed = time.monotonic() - self.started
        write_progress_line(f"{frame} {text} {elapsed:6.1f}s")

    def file(self, text: str, completed: int, total: int, path: Path) -> None:
        if not self.enabled:
            return

        now = time.monotonic()

        if now - self.last_update < 0.05 and completed != total:
            return

        self.last_update = now
        self.tick += 1
        self.active = True
        frame = self.frames[self.tick % len(self.frames)]
        percent = completed * 100.0 / max(1, total)
        elapsed = now - self.started
        relative = self._relative(path)
        prefix = (
            f"{frame} {text} {completed}/{total} "
            f"({percent:5.1f}%) {elapsed:6.1f}s  "
        )
        write_progress_line(trim_progress_line(prefix, relative))

    def discovered(self, visited: int, path: Path) -> None:
        if not self.enabled:
            return

        now = time.monotonic()

        if now - self.last_update < 0.1:
            return

        self.last_update = now
        self.tick += 1
        self.active = True
        frame = self.frames[self.tick % len(self.frames)]
        elapsed = now - self.started
        relative = self._relative(path)
        prefix = (
            f"{frame} Discovering files {visited} scanned "
            f"{elapsed:6.1f}s  "
        )
        write_progress_line(trim_progress_line(prefix, relative))

    def done(self, text: str) -> None:
        if not self.enabled:
            return

        elapsed = time.monotonic() - self.started
        write_progress_line(f"| {text} {elapsed:6.1f}s")
        finish_progress_line()
        self.active = False

    def clear_line(self) -> None:
        if not self.enabled or not self.active:
            return

        clear_progress_line()
        self.active = False


# ---------------------------------------------------------------------------
# Path/hash helpers
# ---------------------------------------------------------------------------

def normalize_relative_path(path: Path, root: Path, *, case_insensitive: bool) -> str:
    relative = path.relative_to(root).as_posix()

    if case_insensitive:
        return relative.casefold()

    return relative


def raw_content_hash(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def file_mtime_size(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None

    return stat.st_mtime_ns, stat.st_size


def make_state_file_entry(
    *,
    path: Path,
    root: Path,
    file_id: str,
    raw_content_hash: str,
) -> dict[str, Any]:
    stamp = file_mtime_size(path)
    relative_path = path.relative_to(root).as_posix()
    return {
        "relativePath": relative_path,
        "fileId": file_id,
        "rawContentHash": raw_content_hash,
        "mtimeNs": stamp[0] if stamp is not None else None,
        "size": stamp[1] if stamp is not None else None,
    }


def load_json_or_none(path: Path) -> Any | None:
    if not path.exists():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def load_update_state(index_root: Path) -> dict[str, Any] | None:
    path = update_state_path(index_root)
    data = load_json_or_none(path)

    if not isinstance(data, dict):
        return None

    if data.get("schema") != UPDATE_STATE_SCHEMA:
        return None

    return data


def save_update_state(
    *,
    index_root: Path,
    root: Path,
    files: dict[str, dict[str, Any]],
) -> None:
    state = {
        "schema": UPDATE_STATE_SCHEMA,
        "root": root.resolve().as_posix(),
        "files": dict(sorted(files.items(), key=lambda item: item[0].casefold())),
    }
    save_json(update_state_path(index_root), state)


# ---------------------------------------------------------------------------
# Existing index loading
# ---------------------------------------------------------------------------

def load_manifest(index_root: Path) -> dict[str, Any] | None:
    return load_json_or_none(index_root / "manifest.json")


def existing_manifest_by_relative_path(
    manifest: dict[str, Any] | None,
    *,
    case_insensitive: bool,
) -> dict[str, dict[str, Any]]:
    if not manifest:
        return {}

    result: dict[str, dict[str, Any]] = {}

    for file_item in manifest.get("files", []):
        relative_path = str(file_item.get("relativePath") or "")

        if not relative_path:
            continue

        key = relative_path.casefold() if case_insensitive else relative_path
        result[key] = file_item

    return result


def existing_state_files(
    state: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not state:
        return {}

    files = state.get("files", {})

    if not isinstance(files, dict):
        return {}

    return files


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def make_update_plan(
    *,
    root: Path,
    index_root: Path,
    extensions: set[str] | None,
    excluded_dir_names: set[str] | None,
    include_extensionless_headers: bool,
    use_git_ignore: bool,
    case_insensitive_paths: bool,
    force: bool,
    known_files_only: bool,
    changed_files: list[Path] | None = None,
    progress: UpdateProgress | None = None,
) -> tuple[UpdatePlan, dict[str, Path], dict[str, str], dict[str, dict[str, Any]]]:
    manifest = load_manifest(index_root)
    manifest_by_path = existing_manifest_by_relative_path(
        manifest,
        case_insensitive=case_insensitive_paths,
    )
    state = load_update_state(index_root)
    state_files = existing_state_files(state)
    state_initialized = state is None
    explicit_changed_keys: set[str] | None = None

    if changed_files:
        explicit_changed_keys = {
            normalize_relative_path(path if path.is_absolute() else root / path, root, case_insensitive=case_insensitive_paths)
            for path in changed_files
        }

    if known_files_only:
        source_files = []

        for file_item in manifest_by_path.values():
            relative_path = str(file_item.get("relativePath") or "")

            if not relative_path:
                continue

            path = root / relative_path

            if path.exists():
                source_files.append(path)

        source_files.sort(key=lambda item: item.as_posix().casefold())
    else:
        source_files = discover_source_files(
            root,
            extensions=extensions,
            excluded_dir_names=excluded_dir_names,
            include_extensionless_headers=include_extensionless_headers,
            use_git_ignore=use_git_ignore,
            progress_callback=progress.discovered if progress is not None else None,
        )

    if progress is not None:
        progress.done(f"Discovery complete: {len(source_files)} current files")

    current_by_key: dict[str, Path] = {}
    current_hashes: dict[str, str] = {}
    hash_candidates: list[tuple[str, Path]] = []

    for path in source_files:
        key = normalize_relative_path(path, root, case_insensitive=case_insensitive_paths)
        current_by_key[key] = path
        manifest_item = manifest_by_path.get(key)
        state_item = state_files.get(key)
        stamp = file_mtime_size(path)

        if explicit_changed_keys is not None and key not in explicit_changed_keys:
            if state_item is not None and isinstance(state_item.get("rawContentHash"), str):
                current_hashes[key] = str(state_item["rawContentHash"])
            elif manifest_item is not None and isinstance(manifest_item.get("contentHash"), str):
                current_hashes[key] = str(manifest_item["contentHash"])
            continue

        if (
            state_item is not None
            and stamp is not None
            and state_item.get("mtimeNs") == stamp[0]
            and state_item.get("size") == stamp[1]
            and isinstance(state_item.get("rawContentHash"), str)
        ):
            current_hashes[key] = str(state_item["rawContentHash"])
            continue

        hash_candidates.append((key, path))

    total_hash_candidates = len(hash_candidates)

    for index, (key, path) in enumerate(hash_candidates, start=1):
        if progress is not None:
            progress.file("Hashing changed candidates", index, total_hash_candidates, path)

        current_hashes[key] = raw_content_hash(path)

    if progress is not None:
        progress.done(f"Hashing complete: {total_hash_candidates} candidate files")

    added: list[Path] = []
    modified: list[Path] = []
    unchanged: list[Path] = []

    for key, path in current_by_key.items():
        manifest_item = manifest_by_path.get(key)
        state_item = state_files.get(key)

        if manifest_item is None:
            added.append(path)
            continue

        if force:
            modified.append(path)
            continue

        if explicit_changed_keys is not None and key not in explicit_changed_keys:
            unchanged.append(path)
            continue

        if state_item is None:
            # Without a previous raw-content hash we cannot prove the existing
            # per-file index still matches the current source text. Reindex the
            # file once, then persist rawContentHash for later fast updates.
            modified.append(path)
            continue

        if state_item.get("rawContentHash") != current_hashes[key]:
            modified.append(path)
        else:
            unchanged.append(path)

    deleted_relative_paths: list[str] = []

    for key, manifest_item in manifest_by_path.items():
        if key not in current_by_key:
            deleted_relative_paths.append(str(manifest_item["relativePath"]))

    added.sort(key=lambda path: path.as_posix().casefold())
    modified.sort(key=lambda path: path.as_posix().casefold())
    unchanged.sort(key=lambda path: path.as_posix().casefold())
    deleted_relative_paths.sort(key=str.casefold)

    return (
        UpdatePlan(
            added=added,
            modified=modified,
            deleted_relative_paths=deleted_relative_paths,
            unchanged=unchanged,
            state_initialized=state_initialized,
        ),
        current_by_key,
        current_hashes,
        manifest_by_path,
    )


# ---------------------------------------------------------------------------
# Aggregation from per-file indexes
# ---------------------------------------------------------------------------

def load_file_index_by_id(index_root: Path, file_id: str) -> dict[str, Any]:
    return json.loads((index_root / "files" / f"{file_id}.json").read_text(encoding="utf-8"))


def structural_file_index_view(file_index: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": file_index.get("schema"),
        "fileId": file_index.get("fileId"),
        "relativePath": file_index.get("relativePath"),
        "displayName": file_index.get("displayName"),
        "extension": file_index.get("extension"),
        "language": file_index.get("language"),
        "pathHash": file_index.get("pathHash"),
        "lineCount": file_index.get("lineCount"),
        "module": file_index.get("module"),
        "imports": file_index.get("imports", []),
        "includes": file_index.get("includes", []),
        "exports": file_index.get("exports", []),
        "symbols": file_index.get("symbols", []),
        "data": file_index.get("data", []),
        "diagnostics": file_index.get("diagnostics", []),
    }


def structurally_equal_file_indexes(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    return structural_file_index_view(left) == structural_file_index_view(right)


def save_index_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def manifest_ref_from_file_index(file_index: dict[str, Any]) -> dict[str, Any]:
    return {
        "fileId": file_index["fileId"],
        "relativePath": file_index["relativePath"],
        "contentHash": file_index["contentHash"],
        "lineCount": file_index["lineCount"],
        "tokenCount": file_index.get("tokenCount", 0),
        "unitKind": file_index["module"]["unitKind"],
        "fullModuleName": file_index["module"].get("fullModuleName"),
        "includes": len(file_index.get("includes", [])),
        "symbols": len(file_index.get("symbols", [])),
        "data": len(file_index.get("data", [])),
        "diagnostics": len(file_index.get("diagnostics", [])),
    }


def rebuild_names(symbols: list[dict[str, Any]]) -> dict[str, list[str]]:
    names: dict[str, list[str]] = defaultdict(list)

    for symbol in symbols:
        for alias in search_aliases_for_symbol(symbol):
            if symbol["symbolId"] not in names[alias]:
                names[alias].append(symbol["symbolId"])

    return dict(sorted(names.items(), key=lambda item: item[0].casefold()))


def rebuild_data_names(data_items: list[dict[str, Any]]) -> dict[str, list[str]]:
    data_names: dict[str, list[str]] = defaultdict(list)

    for data_item in data_items:
        for alias in search_aliases_for_data(data_item):
            if data_item["dataId"] not in data_names[alias]:
                data_names[alias].append(data_item["dataId"])

    return dict(sorted(data_names.items(), key=lambda item: item[0].casefold()))


def rebuild_modules(manifest_files: list[dict[str, Any]]) -> dict[str, list[str]]:
    modules: dict[str, list[str]] = defaultdict(list)

    for file_item in manifest_files:
        full_module_name = file_item.get("fullModuleName")

        if full_module_name:
            modules[str(full_module_name)].append(file_item["fileId"])

    return dict(sorted(modules.items(), key=lambda item: item[0].casefold()))


def record_phase(
    timings: list[dict[str, Any]] | None,
    phase: str,
    started: float,
) -> float:
    now = time.perf_counter()

    if timings is not None:
        timings.append(
            {
                "phase": phase,
                "seconds": now - started,
            }
        )

    return now


def print_phase_timings(title: str, timings: list[dict[str, Any]]) -> None:
    if not timings:
        return

    total = sum(float(item.get("seconds") or 0.0) for item in timings)
    width = max(len(str(item.get("phase") or "")) for item in timings)
    print(title, file=sys.stderr)
    print("=" * len(title), file=sys.stderr)

    for item in timings:
        phase = str(item.get("phase") or "")
        seconds = float(item.get("seconds") or 0.0)
        print(f"{phase:<{width}}  {seconds:6.2f}s", file=sys.stderr)

    print(f"{'total':<{width}}  {total:6.2f}s", file=sys.stderr)


def aggregate_project_index(
    *,
    root: Path,
    index_root: Path,
    current_file_indexes: list[dict[str, Any]],
    extra_diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    manifest_files: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    names: dict[str, list[str]] = defaultdict(list)
    data_items: list[dict[str, Any]] = []
    data_names: dict[str, list[str]] = defaultdict(list)
    modules: dict[str, list[str]] = defaultdict(list)
    diagnostics: list[dict[str, Any]] = [*(extra_diagnostics or [])]

    for file_index in current_file_indexes:
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
                "includes": len(file_index.get("includes", [])),
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

    manifest_files.sort(key=lambda item: item["relativePath"].casefold())
    orientation = build_orientation_index(root)

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
            "orientationNodes": len(orientation.get("nodes", [])),
            "diagnostics": len(diagnostics),
        },
    }

    save_json(index_root / "manifest.json", manifest)
    save_json(index_root / "modules.json", dict(sorted(modules.items(), key=lambda item: item[0].casefold())))
    save_json(index_root / "diagnostics.json", diagnostics)
    save_json(index_root / "orientation.json", orientation)
    build_sqlite_index(
        index_root=index_root,
        symbols=symbols,
        names=names,
        data_items=data_items,
        data_names=data_names,
        counts=manifest["counts"],
        orientation_nodes=orientation.get("nodes", []),
    )
    for legacy_name in ("symbols.jsonl", "names.json", "data.jsonl", "data_names.json"):
        legacy_path = index_root / legacy_name

        if legacy_path.exists():
            legacy_path.unlink()

    return manifest


def aggregate_project_index_incremental(
    *,
    root: Path,
    index_root: Path,
    changed_file_indexes: list[dict[str, Any]],
    extra_diagnostics: list[dict[str, Any]] | None = None,
    timings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    phase_started = time.perf_counter()
    manifest = load_manifest(index_root)

    if manifest is None:
        raise SystemExit("Manifest missing during incremental aggregation.")

    phase_started = record_phase(timings, "load manifest", phase_started)

    changed_file_ids = {
        file_index["fileId"]
        for file_index in changed_file_indexes
    }
    changed_manifest_by_id = {
        file_index["fileId"]: manifest_ref_from_file_index(file_index)
        for file_index in changed_file_indexes
    }

    manifest_files: list[dict[str, Any]] = []
    replaced_file_ids: set[str] = set()

    for file_item in manifest.get("files", []):
        file_id = str(file_item.get("fileId") or "")
        replacement = changed_manifest_by_id.get(file_id)

        if replacement is not None:
            manifest_files.append(replacement)
            replaced_file_ids.add(file_id)
        else:
            manifest_files.append(file_item)

    for file_id, file_item in changed_manifest_by_id.items():
        if file_id not in replaced_file_ids:
            manifest_files.append(file_item)

    phase_started = record_phase(timings, "update manifest entries", phase_started)

    changed_symbols: list[dict[str, Any]] = []
    changed_data_items: list[dict[str, Any]] = []

    diagnostics = [
        item
        for item in load_json_or_none(index_root / "diagnostics.json") or []
        if item.get("fileId") not in changed_file_ids
    ]
    diagnostics.extend(extra_diagnostics or [])
    phase_started = record_phase(timings, "load/filter diagnostics", phase_started)

    for file_index in changed_file_indexes:
        file_id = file_index["fileId"]

        for diagnostic in file_index.get("diagnostics", []):
            diagnostics.append(
                {
                    "fileId": file_id,
                    "relativePath": file_index["relativePath"],
                    **diagnostic,
                }
            )

        for symbol in file_index.get("symbols", []):
            symbol_ref = symbol_ref_from_file_symbol(
                file_index=file_index,
                symbol=symbol,
            )
            changed_symbols.append(symbol_ref)

        for data_item in file_index.get("data", []):
            data_ref = data_ref_from_file_data(
                file_index=file_index,
                data_item=data_item,
            )
            changed_data_items.append(data_ref)

    phase_started = record_phase(timings, "merge changed file refs", phase_started)

    manifest_files.sort(key=lambda item: item["relativePath"].casefold())
    phase_started = record_phase(timings, "sort manifest entries", phase_started)

    modules = rebuild_modules(manifest_files)
    phase_started = record_phase(timings, "rebuild modules", phase_started)
    orientation = build_orientation_index(root)
    phase_started = record_phase(timings, "rebuild orientation docs", phase_started)

    if not sqlite_index_path(index_root).exists():
        raise SystemExit("SQLite lookup index missing during incremental aggregation. Rebuild the index.")

    changed_names = rebuild_names(changed_symbols)
    changed_data_names = rebuild_data_names(changed_data_items)
    lookup_counts = replace_file_lookup_rows(
        index_root=index_root,
        changed_file_ids=changed_file_ids,
        symbols=changed_symbols,
        names=changed_names,
        data_items=changed_data_items,
        data_names=changed_data_names,
    )
    replace_orientation_nodes(index_root=index_root, orientation_nodes=orientation.get("nodes", []))
    phase_started = record_phase(timings, "update sqlite lookup index", phase_started)

    manifest = {
        "schema": PROJECT_INDEX_SCHEMA,
        "root": root.resolve().as_posix(),
        "filesDir": "files",
        "files": manifest_files,
        "stats": project_stats_from_manifest_files(manifest_files),
        "counts": {
            "files": len(manifest_files),
            "symbols": lookup_counts["symbols"],
            "names": lookup_counts["names"],
            "data": lookup_counts["data"],
            "dataNames": lookup_counts["dataNames"],
            "modules": len(modules),
            "orientationNodes": len(orientation.get("nodes", [])),
            "diagnostics": len(diagnostics),
        },
    }
    phase_started = record_phase(timings, "build manifest", phase_started)

    save_index_json(index_root / "manifest.json", manifest)
    phase_started = record_phase(timings, "write manifest", phase_started)

    save_index_json(index_root / "modules.json", modules)
    phase_started = record_phase(timings, "write modules", phase_started)

    save_index_json(index_root / "diagnostics.json", diagnostics)
    phase_started = record_phase(timings, "write diagnostics", phase_started)

    save_index_json(index_root / "orientation.json", orientation)
    record_phase(timings, "write orientation docs", phase_started)

    return manifest


# ---------------------------------------------------------------------------
# Update execution
# ---------------------------------------------------------------------------

def remove_deleted_file_indexes(
    *,
    index_root: Path,
    deleted_relative_paths: list[str],
    manifest_by_path: dict[str, dict[str, Any]],
    case_insensitive_paths: bool,
) -> None:
    for relative_path in deleted_relative_paths:
        key = relative_path.casefold() if case_insensitive_paths else relative_path
        manifest_item = manifest_by_path.get(key)

        if not manifest_item:
            continue

        file_id = manifest_item.get("fileId")

        if not file_id:
            continue

        path = index_root / "files" / f"{file_id}.json"

        if path.exists():
            path.unlink()


def run_update(
    *,
    root: Path,
    index_root: Path,
    extensions: set[str] | None,
    excluded_dir_names: set[str] | None,
    include_extensionless_headers: bool,
    use_git_ignore: bool,
    emit_debug_file_indexes: bool,
    case_insensitive_paths: bool,
    blank_comments: bool,
    dry_run: bool,
    force: bool,
    known_files_only: bool,
    changed_files: list[Path] | None,
    progress_enabled: bool,
    jobs: int,
) -> UpdateResult:
    if not (index_root / "manifest.json").exists():
        raise SystemExit(
            "No existing project index found. Run build_project_index.py first."
        )

    progress = UpdateProgress(root=root, enabled=progress_enabled)
    progress.status(
        "Planning update from known indexed files"
        if known_files_only
        else "Planning update with full discovery"
    )

    plan, current_by_key, current_hashes, manifest_by_path = make_update_plan(
        root=root,
        index_root=index_root,
        extensions=extensions,
        excluded_dir_names=excluded_dir_names,
        include_extensionless_headers=include_extensionless_headers,
        use_git_ignore=use_git_ignore,
        case_insensitive_paths=case_insensitive_paths,
        force=force,
        known_files_only=known_files_only,
        changed_files=changed_files,
        progress=progress,
    )

    progress.clear_line()
    print("Update plan")
    print("===========")
    print("Added:    ", len(plan.added))
    print("Modified: ", len(plan.modified))
    print("Deleted:  ", len(plan.deleted_relative_paths))
    print("Unchanged:", len(plan.unchanged))
    print("State initialized:", plan.state_initialized)
    print("Force reindex:", force)
    print("Known files only:", known_files_only)

    if plan.added:
        print("\nAdded files:")
        for path in plan.added[:50]:
            print("  +", path.relative_to(root).as_posix())
        if len(plan.added) > 50:
            print(f"  ... +{len(plan.added) - 50} more")

    if plan.modified:
        print("\nModified files:")
        for path in plan.modified[:50]:
            print("  *", path.relative_to(root).as_posix())
        if len(plan.modified) > 50:
            print(f"  ... +{len(plan.modified) - 50} more")

    if plan.deleted_relative_paths:
        print("\nDeleted files:")
        for relative_path in plan.deleted_relative_paths[:50]:
            print("  -", relative_path)
        if len(plan.deleted_relative_paths) > 50:
            print(f"  ... +{len(plan.deleted_relative_paths) - 50} more")

    if dry_run:
        return UpdateResult(
            added=len(plan.added),
            modified=len(plan.modified),
            deleted=len(plan.deleted_relative_paths),
            unchanged=len(plan.unchanged),
            files=0,
            symbols=0,
            names=0,
            data=0,
            data_names=0,
            modules=0,
            diagnostics=0,
            state_initialized=plan.state_initialized,
            incremental_aggregation_timings=[],
            structural_unchanged=False,
        )

    if not plan.added and not plan.modified and not plan.deleted_relative_paths:
        manifest = load_manifest(index_root) or {"counts": {}}
        counts = manifest.get("counts", {})
        return UpdateResult(
            added=0,
            modified=0,
            deleted=0,
            unchanged=len(plan.unchanged),
            files=counts.get("files", 0),
            symbols=counts.get("symbols", 0),
            names=counts.get("names", 0),
            data=counts.get("data", 0),
            data_names=counts.get("dataNames", 0),
            modules=counts.get("modules", 0),
            diagnostics=counts.get("diagnostics", 0),
            state_initialized=plan.state_initialized,
            incremental_aggregation_timings=[],
            structural_unchanged=False,
        )

    index_root.mkdir(parents=True, exist_ok=True)
    files_dir = index_root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    remove_deleted_file_indexes(
        index_root=index_root,
        deleted_relative_paths=plan.deleted_relative_paths,
        manifest_by_path=manifest_by_path,
        case_insensitive_paths=case_insensitive_paths,
    )

    changed_paths = [*plan.added, *plan.modified]
    updated_by_key: dict[str, dict[str, Any]] = {}
    old_modified_file_indexes: dict[str, dict[str, Any]] = {}

    for path in plan.modified:
        key = normalize_relative_path(path, root, case_insensitive=case_insensitive_paths)
        manifest_item = manifest_by_path.get(key)

        if manifest_item is None:
            continue

        old_modified_file_indexes[key] = load_file_index_by_id(
            index_root,
            str(manifest_item["fileId"]),
        )

    def update_progress(completed: int, total: int, path: Path) -> None:
        progress.file("Reindexing", completed, total, path)

    changed_file_indexes, changed_file_diagnostics = build_file_indexes_for_project(
        source_files=changed_paths,
        root=root,
        output_root=index_root,
        emit_debug_file_indexes=emit_debug_file_indexes,
        case_insensitive_paths=case_insensitive_paths,
        blank_comments=blank_comments,
        jobs=jobs,
        progress_callback=update_progress,
    )

    if changed_paths:
        progress.done(f"Reindexing complete: {len(changed_paths)} files")

    updated_by_key: dict[str, dict[str, Any]] = {}

    for file_index in changed_file_indexes:
        relative_path = file_index["relativePath"]
        key = relative_path.casefold() if case_insensitive_paths else relative_path
        updated_by_key[key] = file_index

    state_files = existing_state_files(load_update_state(index_root))
    structural_unchanged = False

    if (
        known_files_only
        and plan.modified
        and not plan.added
        and not plan.deleted_relative_paths
        and len(changed_file_indexes) == len(plan.modified)
    ):
        structural_unchanged = True

        for file_index in changed_file_indexes:
            relative_path = file_index["relativePath"]
            key = relative_path.casefold() if case_insensitive_paths else relative_path
            old_file_index = old_modified_file_indexes.get(key)

            if old_file_index is None or not structurally_equal_file_indexes(old_file_index, file_index):
                structural_unchanged = False
                break

    if structural_unchanged:
        new_state_files: dict[str, dict[str, Any]] = {}

        for key, path in sorted(current_by_key.items(), key=lambda item: item[0].casefold()):
            file_index = updated_by_key.get(key)

            if file_index is not None:
                new_state_files[key] = make_state_file_entry(
                    path=path,
                    root=root,
                    file_id=file_index["fileId"],
                    raw_content_hash=file_index["contentHash"],
                )
                continue

            state_item = state_files.get(key)

            if state_item is not None:
                new_state_files[key] = dict(state_item)
                continue

            manifest_item = manifest_by_path.get(key)

            if manifest_item is not None:
                new_state_files[key] = make_state_file_entry(
                    path=path,
                    root=root,
                    file_id=str(manifest_item["fileId"]),
                    raw_content_hash=current_hashes[key],
                )

        save_update_state(
            index_root=index_root,
            root=root,
            files=new_state_files,
        )
        progress.done("Structural index unchanged; skipped aggregate rewrite")
        counts = (load_manifest(index_root) or {"counts": {}}).get("counts", {})

        return UpdateResult(
            added=len(plan.added),
            modified=len(plan.modified),
            deleted=len(plan.deleted_relative_paths),
            unchanged=len(plan.unchanged),
            files=counts.get("files", 0),
            symbols=counts.get("symbols", 0),
            names=counts.get("names", 0),
            data=counts.get("data", 0),
            data_names=counts.get("dataNames", 0),
            modules=counts.get("modules", 0),
            diagnostics=counts.get("diagnostics", 0),
            state_initialized=plan.state_initialized,
            incremental_aggregation_timings=[],
            structural_unchanged=True,
        )

    if (
        known_files_only
        and not plan.added
        and not plan.deleted_relative_paths
    ):
        progress.status("Aggregating changed file indexes")
        incremental_aggregation_timings: list[dict[str, Any]] = []
        manifest = aggregate_project_index_incremental(
            root=root,
            index_root=index_root,
            changed_file_indexes=changed_file_indexes,
            extra_diagnostics=changed_file_diagnostics,
            timings=incremental_aggregation_timings,
        )
        new_state_files: dict[str, dict[str, Any]] = {}

        for key, path in sorted(current_by_key.items(), key=lambda item: item[0].casefold()):
            file_index = updated_by_key.get(key)

            if file_index is not None:
                new_state_files[key] = make_state_file_entry(
                    path=path,
                    root=root,
                    file_id=file_index["fileId"],
                    raw_content_hash=file_index["contentHash"],
                )
                continue

            state_item = state_files.get(key)

            if state_item is not None:
                new_state_files[key] = dict(state_item)
                continue

            manifest_item = manifest_by_path.get(key)

            if manifest_item is not None:
                new_state_files[key] = make_state_file_entry(
                    path=path,
                    root=root,
                    file_id=str(manifest_item["fileId"]),
                    raw_content_hash=current_hashes[key],
                )

        save_update_state(
            index_root=index_root,
            root=root,
            files=new_state_files,
        )
        progress.done("Incremental aggregation complete")

        if progress_enabled:
            print_phase_timings(
                "Incremental aggregation timing",
                incremental_aggregation_timings,
            )

        counts = manifest["counts"]

        return UpdateResult(
            added=len(plan.added),
            modified=len(plan.modified),
            deleted=len(plan.deleted_relative_paths),
            unchanged=len(plan.unchanged),
            files=counts["files"],
            symbols=counts["symbols"],
            names=counts["names"],
            data=counts.get("data", 0),
            data_names=counts.get("dataNames", 0),
            modules=counts["modules"],
            diagnostics=counts["diagnostics"],
            state_initialized=plan.state_initialized,
            incremental_aggregation_timings=incremental_aggregation_timings,
            structural_unchanged=False,
        )

    current_file_indexes: list[dict[str, Any]] = []
    new_state_files: dict[str, dict[str, Any]] = {}
    total_current = len(current_by_key)

    for index, (key, path) in enumerate(
        sorted(current_by_key.items(), key=lambda item: item[0].casefold()),
        start=1,
    ):
        progress.file("Loading file indexes", index, total_current, path)
        file_index = updated_by_key.get(key)

        if file_index is None:
            manifest_item = manifest_by_path.get(key)

            if manifest_item is None:
                # Added file should have been handled above. Keep this safe.
                file_index = build_file_index(
                    path=path,
                    project_root=root,
                    case_insensitive_paths=case_insensitive_paths,
                    blank_comments=blank_comments,
                    emit_debug=emit_debug_file_indexes,
                )
                save_json(file_index_output_path(files_dir, file_index["fileId"]), file_index)
            else:
                file_index = load_file_index_by_id(index_root, manifest_item["fileId"])

        current_file_indexes.append(file_index)
        new_state_files[key] = make_state_file_entry(
            path=path,
            root=root,
            file_id=file_index["fileId"],
            raw_content_hash=current_hashes[key],
        )

    progress.done(f"Loaded file indexes: {len(current_file_indexes)} files")
    progress.status("Aggregating project index")

    manifest = aggregate_project_index(
        root=root,
        index_root=index_root,
        current_file_indexes=current_file_indexes,
        extra_diagnostics=changed_file_diagnostics,
    )
    save_update_state(
        index_root=index_root,
        root=root,
        files=new_state_files,
    )
    progress.done("Aggregation complete")

    counts = manifest["counts"]

    return UpdateResult(
        added=len(plan.added),
        modified=len(plan.modified),
        deleted=len(plan.deleted_relative_paths),
        unchanged=len(plan.unchanged),
        files=counts["files"],
        symbols=counts["symbols"],
        names=counts["names"],
        data=counts.get("data", 0),
        data_names=counts.get("dataNames", 0),
        modules=counts["modules"],
        diagnostics=counts["diagnostics"],
        state_initialized=plan.state_initialized,
        incremental_aggregation_timings=[],
        structural_unchanged=False,
    )


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def parse_extensions(values: list[str] | None) -> set[str] | None:
    if not values:
        return None

    result: set[str] = set()

    for value in values:
        for part in value.split(","):
            ext = part.strip()

            if not ext:
                continue

            if not ext.startswith("."):
                ext = "." + ext

            result.add(ext)

    return result


def parse_excluded_dirs(values: list[str] | None) -> set[str] | None:
    if not values:
        return None

    result = set(DEFAULT_EXCLUDED_DIR_NAMES)

    for value in values:
        for part in value.split(","):
            name = part.strip()

            if name:
                result.add(name)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Incrementally update an existing mcp-cpp-project-indexer project index. "
            "Use --dry-run to show the diff without writing. "
            "Use --force to reindex all current files after parser/indexer changes."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project/source root. Defaults to current working directory.",
    )
    parser.add_argument(
        "--index-root",
        type=Path,
        default=None,
        help="Index root. Defaults to <root>/.mcp-cpp-project-indexer.",
    )
    parser.add_argument(
        "--extensions",
        nargs="*",
        default=None,
        help="Optional source extensions to scan.",
    )
    parser.add_argument(
        "--include-extensionless-headers",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Also discover extensionless files that look like C/C++ headers "
            "based on a conservative first-lines heuristic."
        ),
    )
    parser.add_argument(
        "--git-ignore",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Filter discovered files through git check-ignore when available. Default: true.",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=None,
        help="Directory name to exclude. Can be repeated or comma-separated.",
    )
    parser.add_argument(
        "--case-insensitive-paths",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Case-fold relative paths before comparing/hashing path keys.",
    )
    parser.add_argument(
        "--blank-comments",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Blank comments before scanning changed files.",
    )
    parser.add_argument(
        "--emit-diagnostic-file-indexes",
        "--emit-debug-file-indexes",
        dest="emit_debug_file_indexes",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Emit indexer/scanner diagnostic data inside updated per-file indexes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show added/modified/deleted files. Do not write anything.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help=(
            "Number of parallel file-indexing worker processes for added/modified files. "
            "Use 1 for sequential mode. Use 0 for conservative auto mode."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Reindex all current files instead of only files whose raw content hash changed. "
            "Use this after parser/indexer code changes."
        ),
    )
    parser.add_argument(
        "--known-files-only",
        action="store_true",
        help=(
            "Skip full filesystem discovery and only check files already present in the "
            "existing manifest. This is faster for edit/update loops but does not discover "
            "new source files."
        ),
    )
    parser.add_argument(
        "--changed-file",
        action="append",
        type=Path,
        default=[],
        help=(
            "Known changed file path, absolute or project-relative. "
            "May be passed multiple times by a watcher to avoid hashing unchanged files."
        ),
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show update planning/reindexing progress on stderr.",
    )
    parser.add_argument(
        "--print-summary-json",
        action="store_true",
        help="Print summary as JSON.",
    )
    parser.add_argument(
        "--summary-json-file",
        type=Path,
        default=None,
        help="Write update summary JSON to this file while keeping normal console output.",
    )

    args = parser.parse_args()
    root = args.root.resolve()
    index_root = (args.index_root or (root / DEFAULT_INDEX_DIR_NAME)).resolve()

    if not root.exists():
        raise SystemExit(f"Project root not found: {root}")

    try:
        if args.dry_run:
            result = run_update(
                root=root,
                index_root=index_root,
                extensions=parse_extensions(args.extensions),
                excluded_dir_names=parse_excluded_dirs(args.exclude_dir),
                include_extensionless_headers=args.include_extensionless_headers,
                use_git_ignore=args.git_ignore,
                emit_debug_file_indexes=args.emit_debug_file_indexes,
                case_insensitive_paths=args.case_insensitive_paths,
                blank_comments=args.blank_comments,
                dry_run=args.dry_run,
                force=args.force,
                known_files_only=args.known_files_only,
                changed_files=args.changed_file,
                progress_enabled=args.progress and not args.print_summary_json,
                jobs=args.jobs,
            )
        else:
            with index_update_lock(index_root):
                result = run_update(
                    root=root,
                    index_root=index_root,
                    extensions=parse_extensions(args.extensions),
                    excluded_dir_names=parse_excluded_dirs(args.exclude_dir),
                    include_extensionless_headers=args.include_extensionless_headers,
                    use_git_ignore=args.git_ignore,
                    emit_debug_file_indexes=args.emit_debug_file_indexes,
                    case_insensitive_paths=args.case_insensitive_paths,
                    blank_comments=args.blank_comments,
                    dry_run=args.dry_run,
                    force=args.force,
                    known_files_only=args.known_files_only,
                    changed_files=args.changed_file,
                    progress_enabled=args.progress and not args.print_summary_json,
                    jobs=args.jobs,
                )
    except IndexLockError as exc:
        raise SystemExit(str(exc)) from exc

    index_stats = {"totalCodeLines": 0, "totalTokens": 0}

    if not args.dry_run:
        manifest = load_manifest(index_root)

        if manifest is not None:
            index_stats = project_stats_from_manifest(manifest)

    summary = {
        "root": root.as_posix(),
        "indexRoot": index_root.as_posix(),
        "dryRun": args.dry_run,
        "force": args.force,
        "knownFilesOnly": args.known_files_only,
        "stateInitialized": result.state_initialized,
        "added": result.added,
        "modified": result.modified,
        "deleted": result.deleted,
        "unchanged": result.unchanged,
        "files": result.files,
        "symbols": result.symbols,
        "names": result.names,
        "data": result.data,
        "dataNames": result.data_names,
        "modules": result.modules,
        "diagnostics": result.diagnostics,
        "totalCodeLines": index_stats["totalCodeLines"],
        "totalTokens": index_stats["totalTokens"],
        "incrementalAggregationTimings": result.incremental_aggregation_timings,
        "structuralUnchanged": result.structural_unchanged,
    }

    if args.summary_json_file is not None:
        save_json(args.summary_json_file, summary)

    if args.print_summary_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    print()
    print("Update complete" if not args.dry_run else "Dry run complete")
    print("===============")
    print("Added:      ", result.added)
    print("Modified:   ", result.modified)
    print("Deleted:    ", result.deleted)
    print("Unchanged:  ", result.unchanged)

    if not args.dry_run:
        print("Files:      ", result.files)
        print("Symbols:    ", result.symbols)
        print("Names:      ", result.names)
        print("Data:       ", result.data)
        print("Data names: ", result.data_names)
        print("Modules:    ", result.modules)
        print("Diagnostics:", result.diagnostics)
        print("Code lines: ", index_stats["totalCodeLines"])
        print("Tokens:     ", index_stats["totalTokens"])
        print("State:      ", update_state_path(index_root).as_posix())
        print("Jobs:       ", normalize_jobs(args.jobs))


    return 0


if __name__ == "__main__":
    raise SystemExit(main())
