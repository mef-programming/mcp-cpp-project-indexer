from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

from pathlib import Path
from unittest.mock import patch


INDEXER_SRC = Path(__file__).resolve().parents[1] / "src" / "indexer"
if str(INDEXER_SRC) not in sys.path:
    sys.path.insert(0, str(INDEXER_SRC))

import cpp_orientation_index as orientation


LEGACY_EXCLUDED_DIRS = {
    ".git", ".mcp-cpp-project-indexer", ".mcp-ts-project-indexer",
    ".mcp-python-project-indexer", ".vs", ".vscode", "node_modules",
    "__pycache__", "dist", "build", "out",
}


def legacy_discover_orientation_documents(
    root: Path,
    *,
    doc_files: tuple[str, ...] = orientation.DEFAULT_ORIENTATION_FILES,
) -> list[Path]:
    """Discovery implementation from before the fork optimization (b506358)."""
    doc_names = {name.casefold() for name in doc_files}
    result: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        relative_parts = path.relative_to(root).parts
        if any(part in LEGACY_EXCLUDED_DIRS for part in relative_parts[:-1]):
            continue

        if path.name.casefold() in doc_names or "topology" in path.stem.casefold():
            result.append(path)

    result.sort(key=lambda item: orientation.normalize_doc_path(item.relative_to(root)).casefold())
    return result


def compare_orientation_builds(root: Path, *, runs: int = 1) -> dict[str, object]:
    if runs < 1:
        raise ValueError("runs must be at least 1")
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")

    root = root.resolve()
    timings: dict[str, list[float]] = {"legacy": [], "current": []}
    results: dict[str, dict[str, object]] = {}

    for run in range(runs):
        for version in (("legacy", "current") if run % 2 == 0 else ("current", "legacy")):
            started = time.perf_counter()
            if version == "legacy":
                with patch.object(orientation, "discover_orientation_documents", legacy_discover_orientation_documents):
                    result = orientation.build_orientation_index(root)
            else:
                result = orientation.build_orientation_index(root)
            timings[version].append(round((time.perf_counter() - started) * 1000, 3))
            results[version] = result

    equivalent = results["legacy"] == results["current"]
    legacy_median = statistics.median(timings["legacy"])
    current_median = statistics.median(timings["current"])
    return {
        "root": str(root),
        "runs": runs,
        "equivalent": equivalent,
        "legacyNodes": results["legacy"]["counts"]["nodes"],
        "currentNodes": results["current"]["counts"]["nodes"],
        "legacyMillis": timings["legacy"],
        "currentMillis": timings["current"],
        "legacyMedianMillis": legacy_median,
        "currentMedianMillis": current_median,
        "speedup": round(legacy_median / current_median, 2) if equivalent and current_median > 0 else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare pre-fork and current orientation rebuild times without changing the project index."
    )
    parser.add_argument("--root", type=Path, required=True, help="C++ project root to measure.")
    parser.add_argument("--runs", type=int, default=1, help="Runs per version (default: 1).")
    args = parser.parse_args()

    try:
        report = compare_orientation_builds(args.root, runs=args.runs)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["equivalent"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
