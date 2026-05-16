from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpp_file_index import build_file_index
from cpp_index_utils import save_json
from cpp_project_index import (
    DEFAULT_EXCLUDED_DIR_NAMES,
    DEFAULT_SOURCE_EXTENSIONS,
    PROJECT_INDEX_SCHEMA,
    discover_source_files,
    file_index_output_path,
    search_aliases_for_symbol,
    symbol_ref_from_file_symbol,
    data_ref_from_file_data,
    search_aliases_for_data,
    build_file_indexes_for_project,
    normalize_jobs,
)


DEFAULT_INDEX_DIR_NAME = ".mcp-cpp-project-indexer"
UPDATE_STATE_SCHEMA = "cpp.project_index.update_state.v1"


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


class UpdateProgress:
    def __init__(self, *, root: Path, enabled: bool) -> None:
        self.root = root
        self.enabled = enabled
        self.started = time.monotonic()
        self.last_update = 0.0

    def _relative(self, path: Path) -> str:
        try:
            relative = path.relative_to(self.root).as_posix()
        except ValueError:
            relative = path.as_posix()

        max_path_len = 90

        if len(relative) > max_path_len:
            return "..." + relative[-max_path_len:]

        return relative

    def status(self, text: str) -> None:
        if not self.enabled:
            return

        elapsed = time.monotonic() - self.started
        sys.stderr.write(f"\r- {text} {elapsed:6.1f}s{'':<80}")
        sys.stderr.flush()

    def file(self, text: str, completed: int, total: int, path: Path) -> None:
        if not self.enabled:
            return

        now = time.monotonic()

        if now - self.last_update < 0.05 and completed != total:
            return

        self.last_update = now
        percent = completed * 100.0 / max(1, total)
        elapsed = now - self.started
        relative = self._relative(path)
        sys.stderr.write(
            f"\r- {text} {completed}/{total} "
            f"({percent:5.1f}%) {elapsed:6.1f}s  {relative:<90}"
        )
        sys.stderr.flush()

    def discovered(self, visited: int, path: Path) -> None:
        if not self.enabled:
            return

        now = time.monotonic()

        if now - self.last_update < 0.1:
            return

        self.last_update = now
        elapsed = now - self.started
        relative = self._relative(path)
        sys.stderr.write(
            f"\r- Discovering files {visited} scanned "
            f"{elapsed:6.1f}s  {relative:<90}"
        )
        sys.stderr.flush()

    def done(self, text: str) -> None:
        if not self.enabled:
            return

        elapsed = time.monotonic() - self.started
        sys.stderr.write(f"\r- {text} {elapsed:6.1f}s{'':<90}\n")
        sys.stderr.flush()


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


def load_json_or_none(path: Path) -> Any | None:
    if not path.exists():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def update_state_path(index_root: Path) -> Path:
    return index_root / "update_state.json"


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
    case_insensitive_paths: bool,
    force: bool,
    known_files_only: bool,
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
            progress_callback=progress.discovered if progress is not None else None,
        )

    if progress is not None:
        progress.done(f"Discovery complete: {len(source_files)} current files")

    current_by_key: dict[str, Path] = {}
    current_hashes: dict[str, str] = {}
    total = len(source_files)

    for index, path in enumerate(source_files, start=1):
        if progress is not None:
            progress.file("Hashing files", index, total, path)

        key = normalize_relative_path(path, root, case_insensitive=case_insensitive_paths)
        current_by_key[key] = path
        current_hashes[key] = raw_content_hash(path)

    if progress is not None:
        progress.done(f"Hashing complete: {len(current_hashes)} files")

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

        if state_item is None:
            # First incremental run after a full build. Trust the existing file
            # index and initialize our raw-content hash state instead of
            # reparsing every file.
            unchanged.append(path)
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

    symbols.sort(
        key=lambda item: (
            item["qualifiedName"] or item["shortName"] or "",
            item["relativePath"],
            item["startLine"],
            item["endLine"],
        )
    )
    manifest_files.sort(key=lambda item: item["relativePath"].casefold())

    data_items.sort(
        key=lambda item: (
            item["qualifiedName"] or item["name"] or "",
            item["relativePath"],
            item["startLine"],
            item["endLine"],
        )
    )

    manifest = {
        "schema": PROJECT_INDEX_SCHEMA,
        "root": root.resolve().as_posix(),
        "filesDir": "files",
        "files": manifest_files,
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

    save_json(index_root / "manifest.json", manifest)
    save_json(index_root / "names.json", dict(sorted(names.items(), key=lambda item: item[0].casefold())))
    save_json(index_root / "modules.json", dict(sorted(modules.items(), key=lambda item: item[0].casefold())))
    save_json(index_root / "diagnostics.json", diagnostics)

    with (index_root / "symbols.jsonl").open("w", encoding="utf-8") as handle:
        for symbol in symbols:
            handle.write(json.dumps(symbol, ensure_ascii=False) + "\n")

    save_json(index_root / "data_names.json", dict(sorted(data_names.items(), key=lambda item: item[0].casefold())))

    with (index_root / "data.jsonl").open("w", encoding="utf-8") as handle:
        for data_item in data_items:
            handle.write(json.dumps(data_item, ensure_ascii=False) + "\n")

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
    emit_debug_file_indexes: bool,
    case_insensitive_paths: bool,
    blank_comments: bool,
    dry_run: bool,
    force: bool,
    known_files_only: bool,
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
        case_insensitive_paths=case_insensitive_paths,
        force=force,
        known_files_only=known_files_only,
        progress=progress,
    )

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
        new_state_files[key] = {
            "relativePath": file_index["relativePath"],
            "fileId": file_index["fileId"],
            "rawContentHash": current_hashes[key],
        }

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
        "--emit-debug-file-indexes",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Emit debug scanner data inside updated per-file indexes.",
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

    args = parser.parse_args()
    root = args.root.resolve()
    index_root = (args.index_root or (root / DEFAULT_INDEX_DIR_NAME)).resolve()

    if not root.exists():
        raise SystemExit(f"Project root not found: {root}")

    result = run_update(
        root=root,
        index_root=index_root,
        extensions=parse_extensions(args.extensions),
        excluded_dir_names=parse_excluded_dirs(args.exclude_dir),
        emit_debug_file_indexes=args.emit_debug_file_indexes,
        case_insensitive_paths=args.case_insensitive_paths,
        blank_comments=args.blank_comments,
        dry_run=args.dry_run,
        force=args.force,
        known_files_only=args.known_files_only,
        progress_enabled=args.progress and not args.print_summary_json,
        jobs=args.jobs,
    )

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
    }

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
        print("State:      ", update_state_path(index_root).as_posix())
        print("Jobs:       ", normalize_jobs(args.jobs))


    return 0


if __name__ == "__main__":
    raise SystemExit(main())
