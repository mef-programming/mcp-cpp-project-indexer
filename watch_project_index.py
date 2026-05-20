from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from cpp_index_lock import IndexLockError, index_watcher_lock
from cpp_project_index import (
    DEFAULT_EXCLUDED_DIR_NAMES,
    DEFAULT_SOURCE_EXTENSIONS,
    discover_source_files,
    normalize_jobs,
)


DEFAULT_INDEX_DIR_NAME = ".mcp-cpp-project-indexer"
WATCH_UPDATE_SUMMARY_NAME = ".watch_update_summary.json"


def summary_has_index_changes(summary_path: Path) -> bool:
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True

    if summary.get("structuralUnchanged") is True:
        return False

    return any(
        int(summary.get(key) or 0) > 0
        for key in ("added", "modified", "deleted")
    )


@dataclass(frozen=True, slots=True)
class FileStamp:
    mtime_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    path: Path
    stamp: FileStamp


@dataclass(slots=True)
class SnapshotDiff:
    added: list[Path]
    modified: list[Path]
    deleted: list[Path]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.modified or self.deleted)

    @property
    def requires_full_discovery_update(self) -> bool:
        return bool(self.added or self.deleted)


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


def relative_key(path: Path, root: Path, *, case_insensitive_paths: bool) -> str:
    relative = path.relative_to(root).as_posix()

    if case_insensitive_paths:
        return relative.casefold()

    return relative


def snapshot_source_files(
    *,
    root: Path,
    extensions: set[str] | None,
    excluded_dir_names: set[str] | None,
    case_insensitive_paths: bool,
) -> dict[str, SnapshotEntry]:
    result: dict[str, SnapshotEntry] = {}
    source_files = discover_source_files(
        root,
        extensions=extensions,
        excluded_dir_names=excluded_dir_names,
    )

    for path in source_files:
        try:
            stat = path.stat()
        except OSError:
            continue

        result[relative_key(path, root, case_insensitive_paths=case_insensitive_paths)] = SnapshotEntry(
            path=path,
            stamp=FileStamp(
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
            ),
        )

    return result


def diff_snapshots(
    before: dict[str, SnapshotEntry],
    after: dict[str, SnapshotEntry],
    *,
    root: Path,
) -> SnapshotDiff:
    before_keys = set(before)
    after_keys = set(after)

    added = [
        after[key].path
        for key in sorted(after_keys - before_keys)
    ]
    deleted = [
        before[key].path
        for key in sorted(before_keys - after_keys)
    ]
    modified = [
        after[key].path
        for key in sorted(before_keys & after_keys)
        if before[key].stamp != after[key].stamp
    ]

    return SnapshotDiff(
        added=added,
        modified=modified,
        deleted=deleted,
    )


def print_diff(diff: SnapshotDiff, root: Path) -> None:
    print()
    print("Detected source changes")
    print("=======================")
    print("Added:   ", len(diff.added))
    print("Modified:", len(diff.modified))
    print("Deleted: ", len(diff.deleted))

    for prefix, paths in (
        ("+", diff.added),
        ("*", diff.modified),
        ("-", diff.deleted),
    ):
        for path in paths[:20]:
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                relative = path.as_posix()

            print(f"  {prefix} {relative}")

        if len(paths) > 20:
            print(f"  ... {len(paths) - 20} more")


def run_update(
    *,
    root: Path,
    index_root: Path,
    jobs: int,
    known_files_only: bool,
    changed_files: list[Path],
    build_module_map: bool,
    indexer_root: Path,
    emit_debug_file_indexes: bool,
) -> int:
    update_args = [
        str(sys.executable),
        str(indexer_root / "update_project_index.py"),
        "--root",
        str(root),
        "--index-root",
        str(index_root),
        "--jobs",
        str(jobs),
    ]
    summary_path = index_root / WATCH_UPDATE_SUMMARY_NAME
    update_args.extend(["--summary-json-file", str(summary_path)])

    if known_files_only:
        update_args.append("--known-files-only")

        for path in changed_files:
            try:
                relative = path.relative_to(root)
            except ValueError:
                relative = path

            update_args.extend(["--changed-file", relative.as_posix()])

    if emit_debug_file_indexes:
        update_args.append("--emit-diagnostic-file-indexes")

    print()
    print("Command:")
    print("  " + " ".join(update_args))
    print()

    completed = subprocess.run(update_args, check=False)

    if completed.returncode != 0:
        return completed.returncode

    if not summary_has_index_changes(summary_path):
        print()
        print("No index changes after content-hash check; skipping module map.")
        return 0

    if not build_module_map:
        return 0

    module_args = [
        str(sys.executable),
        str(indexer_root / "build_module_map.py"),
        "--index-root",
        str(index_root),
    ]
    print()
    print("Command:")
    print("  " + " ".join(module_args))
    print()

    return subprocess.run(module_args, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Watch source files and run update_project_index.py after changes. "
            "This is a polling watcher with debounce, requiring no external Python packages."
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
        "--indexer-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory containing the mcp-cpp-project-indexer scripts.",
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
        help="Case-fold relative paths before comparing path keys.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Worker process count for update actions. Use 0 for auto.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds between source tree scans.",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=1.5,
        help="Seconds to wait for changes to settle before running update.",
    )
    parser.add_argument(
        "--module-map",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rebuild module_map.json after each successful index update.",
    )
    parser.add_argument(
        "--emit-diagnostic-file-indexes",
        "--emit-debug-file-indexes",
        dest="emit_debug_file_indexes",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Pass diagnostic emission to watcher-triggered index updates.",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    index_root = (args.index_root or (root / DEFAULT_INDEX_DIR_NAME)).resolve()
    indexer_root = args.indexer_root.resolve()
    extensions = parse_extensions(args.extensions)
    excluded_dir_names = parse_excluded_dirs(args.exclude_dir)

    if not root.exists():
        raise SystemExit(f"Project root not found: {root}")

    if not (index_root / "manifest.json").exists():
        raise SystemExit("No existing project index found. Run build_project_index.py first.")

    try:
        watcher_lock = index_watcher_lock(index_root)
        watcher_lock.acquire()
    except IndexLockError as exc:
        raise SystemExit(
            f"{exc}. Another watcher is already active for this index root."
        ) from exc

    try:
        print("Watching project index")
        print("======================")
        print("Root:       ", root)
        print("Index root: ", index_root)
        print("Indexer:    ", indexer_root)
        print("Jobs:       ", normalize_jobs(args.jobs))
        print("Poll:       ", f"{args.poll_interval:.2f}s")
        print("Debounce:   ", f"{args.debounce:.2f}s")
        print("Module map: ", args.module_map)
        print("Diagnostics:", args.emit_debug_file_indexes)
        print("Stop with Ctrl+C.")

        snapshot = snapshot_source_files(
            root=root,
            extensions=extensions,
            excluded_dir_names=excluded_dir_names,
            case_insensitive_paths=args.case_insensitive_paths,
        )
        print("Initial source files:", len(snapshot))

        pending_since: float | None = None
        pending_snapshot: dict[str, SnapshotEntry] | None = None
        pending_diff: SnapshotDiff | None = None

        while True:
            time.sleep(max(0.1, args.poll_interval))

            current = snapshot_source_files(
                root=root,
                extensions=extensions,
                excluded_dir_names=excluded_dir_names,
                case_insensitive_paths=args.case_insensitive_paths,
            )
            diff = diff_snapshots(snapshot, current, root=root)

            if not diff.changed:
                continue

            pending_since = time.monotonic()
            pending_snapshot = current
            pending_diff = diff

            while True:
                time.sleep(max(0.1, args.poll_interval))
                current = snapshot_source_files(
                    root=root,
                    extensions=extensions,
                    excluded_dir_names=excluded_dir_names,
                    case_insensitive_paths=args.case_insensitive_paths,
                )
                next_diff = diff_snapshots(pending_snapshot, current, root=root)

                if next_diff.changed:
                    pending_since = time.monotonic()
                    pending_snapshot = current
                    pending_diff = diff_snapshots(snapshot, current, root=root)

                if pending_since is not None and time.monotonic() - pending_since >= args.debounce:
                    break

            if pending_snapshot is None or pending_diff is None:
                continue

            print_diff(pending_diff, root)
            result = run_update(
                root=root,
                index_root=index_root,
                jobs=args.jobs,
                known_files_only=not pending_diff.requires_full_discovery_update,
                changed_files=pending_diff.modified,
                build_module_map=args.module_map,
                indexer_root=indexer_root,
                emit_debug_file_indexes=args.emit_debug_file_indexes,
            )

            if result == 0:
                snapshot = pending_snapshot
                print()
                print("Watch update complete.")
            else:
                print()
                print(f"Watch update failed with exit code {result}. Keeping previous snapshot.")

            pending_since = None
            pending_snapshot = None
            pending_diff = None

    except KeyboardInterrupt:
        print()
        print("Watcher stopped.")
        return 0
    finally:
        watcher_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
