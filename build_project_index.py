from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from cpp_project_index import (
    DEFAULT_EXCLUDED_DIR_NAMES,
    DEFAULT_SOURCE_EXTENSIONS,
    build_project_index,
)

class ProgressSpinner:
    def __init__(self, *, root: Path) -> None:
        self.root = root
        self.frames = "|/-\\"
        self.started = time.monotonic()
        self.last_update = 0.0

    def __call__(self, index: int, total: int, path: Path) -> None:
        now = time.monotonic()

        # Avoid too much console I/O.
        if now - self.last_update < 0.05 and index != total:
            return

        self.last_update = now
        frame = self.frames[index % len(self.frames)]
        percent = (index / total * 100.0) if total else 100.0

        try:
            relative = path.relative_to(self.root).as_posix()
        except ValueError:
            relative = path.as_posix()

        # Keep the line reasonably short.
        max_path_len = 90

        if len(relative) > max_path_len:
            relative = "..." + relative[-max_path_len:]

        elapsed = now - self.started

        sys.stderr.write(
            f"\r{frame} Indexing {index}/{total} "
            f"({percent:5.1f}%) "
            f"{elapsed:6.1f}s  {relative:<90}"
        )
        sys.stderr.flush()

        if index == total:
            sys.stderr.write("\n")
            sys.stderr.flush()

DEFAULT_INDEX_DIR_NAME = ".mcp-cpp-project-indexer"


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a parser-only cpp.project_index.v1 runtime index. "
            "The index is for MCP code routing: find symbols and read exact "
            "source ranges. It does not analyze code."
        )
    )

    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project/source root to scan. Defaults to current working directory.",
    )

    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Directory where the project index is written. Defaults to <root>/.mcp-cpp-project-indexer.",
    )
    parser.add_argument(
        "--extensions",
        nargs="*",
        default=None,
        help=(
            "Optional source extensions to scan. Accepts values like .cpp .h .ixx "
            "or comma-separated cpp,h,ixx. Defaults to the built-in C/C++ set."
        ),
    )

    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=None,
        help=(
            "Directory name to exclude. Can be repeated or comma-separated. "
            "Values are added to the default exclude set."
        ),
    )

    parser.add_argument(
        "--case-insensitive-paths",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Case-fold relative paths before hashing. Recommended on Windows projects.",
    )

    parser.add_argument(
        "--blank-comments",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Blank comments before scanning while preserving line numbers and columns.",
    )

    parser.add_argument(
        "--emit-debug-file-indexes",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Emit debug scanner data inside files/<fileId>.json, such as "
            "structuralEvents, scopeIntervals and functionBodyRanges. Default is false."
        ),
    )

    parser.add_argument(
        "--print-summary-json",
        action="store_true",
        help="Print summary as JSON instead of human-readable lines.",
    )

    parser.add_argument(
        "--list-defaults",
        action="store_true",
        help="Print default extensions and excluded directory names, then exit.",
    )

    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show indexing progress on stderr.",
    )

    args = parser.parse_args()

    root = args.root.resolve()
    output_root = args.output_root or (root / DEFAULT_INDEX_DIR_NAME)

    if args.list_defaults:
        print(
            json.dumps(
                {
                    "extensions": sorted(DEFAULT_SOURCE_EXTENSIONS),
                    "excludedDirs": sorted(DEFAULT_EXCLUDED_DIR_NAMES),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if not args.root.exists():
        raise SystemExit(f"Project root not found: {args.root}")

    if not args.root.is_dir():
        raise SystemExit(f"Project root is not a directory: {args.root}")

    extensions = parse_extensions(args.extensions)
    excluded_dirs = parse_excluded_dirs(args.exclude_dir)

    progress_callback = None

    if args.progress and not args.print_summary_json:
        progress_callback = ProgressSpinner(root=args.root)

    result = build_project_index(
        root=root,
        output_root=output_root,
        extensions=extensions,
        excluded_dir_names=excluded_dirs,
        emit_debug_file_indexes=args.emit_debug_file_indexes,
        case_insensitive_paths=args.case_insensitive_paths,
        blank_comments=args.blank_comments,
        progress_callback=progress_callback,
    )

    summary = {
        "root": result.root.resolve().as_posix(),
        "outputRoot": result.output_root.resolve().as_posix(),
        "files": result.files_count,
        "symbols": result.symbols_count,
        "names": result.names_count,
        "modules": result.modules_count,
        "diagnostics": result.diagnostics_count,
        "manifest": (result.output_root / "manifest.json").as_posix(),
        "symbolsJsonl": (result.output_root / "symbols.jsonl").as_posix(),
        "namesJson": (result.output_root / "names.json").as_posix(),
        "modulesJson": (result.output_root / "modules.json").as_posix(),
        "diagnosticsJson": (result.output_root / "diagnostics.json").as_posix(),
    }

    if args.print_summary_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    print("Built cpp.project_index.v1")
    print("Root:", summary["root"])
    print("Output:", summary["outputRoot"])
    print("Files:", summary["files"])
    print("Symbols:", summary["symbols"])
    print("Names:", summary["names"])
    print("Modules:", summary["modules"])
    print("Diagnostics:", summary["diagnostics"])
    print("Manifest:", summary["manifest"])
    print("Symbols JSONL:", summary["symbolsJsonl"])
    print("Names JSON:", summary["namesJson"])
    print("Data:", result.data_count)
    print("Data names:", result.data_names_count)
    print("Data JSONL:", args.output_root / "data.jsonl")
    print("Data names JSON:", args.output_root / "data_names.json")
    print("Modules JSON:", summary["modulesJson"])
    print("Diagnostics JSON:", summary["diagnosticsJson"])


if __name__ == "__main__":
    main()
