from __future__ import annotations

import argparse
import json
from pathlib import Path

from cpp_file_index import build_and_save_file_index, summarize_file_index
from cpp_index_utils import safe_name


DEFAULT_OUTPUT_ROOT = Path.cwd() / ".mcp-cpp-project-indexer" / "file-indexes"


def default_output_path(
    *,
    file_path: Path,
    output_root: Path,
) -> Path:
    return output_root / f"{safe_name(file_path.name)}.cpp.file_index.v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a parser-only cpp.file_index.v1 JSON file. "
            "This tool routes code by symbols and exact source ranges; "
            "it does not analyze code."
        )
    )

    parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="C++ source/module file to index.",
    )

    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help=(
            "Project root used to compute normalized relative paths and pathHash. "
            "If omitted, the indexed file parent directory is used."
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path. If omitted, output-root/<filename>.cpp.file_index.v1.json is used.",
    )

    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Directory used when --output is omitted.",
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
        "--emit-diagnostics",
        "--emit-debug",
        dest="emit_debug",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Emit scanner diagnostic data such as structuralEvents, scopeIntervals and functionBodyRanges.",
    )

    parser.add_argument(
        "--print-summary-json",
        action="store_true",
        help="Print summary as JSON instead of human-readable lines.",
    )

    args = parser.parse_args()

    if not args.file.exists():
        raise SystemExit(f"File not found: {args.file}")

    output = args.output

    if output is None:
        output = default_output_path(
            file_path=args.file,
            output_root=args.output_root,
        )

    project_root = args.project_root or args.file.parent

    file_index = build_and_save_file_index(
        path=args.file,
        output=output,
        project_root=project_root,
        case_insensitive_paths=args.case_insensitive_paths,
        blank_comments=args.blank_comments,
        emit_debug=args.emit_debug,
    )

    summary = summarize_file_index(file_index)

    if args.print_summary_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print("Built cpp.file_index.v1")
        print("File:", summary["file"])
        print("Output:", output)
        print("FileId:", summary["fileId"])
        print("Lines:", summary["lineCount"])
        print("Unit kind:", summary["unitKind"])
        print("Module:", summary["fullModuleName"])
        print("Imports:", summary["imports"])
        print("Exports:", summary["exports"])
        print("Symbols:", summary["symbols"])
        print("Data:", summary["data"])

        if args.emit_debug:
            print("Scope intervals:", summary["scopeIntervals"])
            print("Structural events:", summary["structuralEvents"])
            print("Function bodies:", summary["functionBodyRanges"])

        print("Diagnostics:", summary["diagnostics"])


if __name__ == "__main__":
    main()
