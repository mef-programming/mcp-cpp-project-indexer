from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_command(*args: str) -> None:
    subprocess.run(
        [sys.executable, *args], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True,
    )


def make_fixture(root: Path, documents: int) -> Path:
    root.mkdir()
    source = root / "sample.cpp"
    source.write_text("int original() { return 0; }\n", encoding="utf-8")
    for number in range(documents):
        folder = root / f"part-{number:04d}"
        folder.mkdir()
        (folder / "README.md").write_text(
            f"# Part {number}\nTopologyKind: topology\n"
            f"Purpose: Synthetic orientation document {number}\n"
            "## Navigation\n```text\nsource  sample.cpp  Shared source file\n```\n",
            encoding="utf-8",
        )
    return source


def timed_update(root: Path, index_root: Path, source: Path) -> tuple[dict[str, Any], float]:
    summary_path = index_root / "benchmark-summary.json"
    started = time.perf_counter()
    run_command(
        str(REPO_ROOT / "update_project_index.py"),
        "--root", str(root), "--index-root", str(index_root),
        "--known-files-only", "--changed-file", str(source),
        "--summary-json-file", str(summary_path), "--no-progress",
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return json.loads(summary_path.read_text(encoding="utf-8")), round(elapsed_ms, 3)


def compare_incremental_updates(*, documents: int = 200, runs: int = 3) -> dict[str, Any]:
    if documents < 1 or runs < 1:
        raise ValueError("documents and runs must be at least 1")

    samples: dict[str, dict[str, list[float]]] = {
        arm: {"wallMs": [], "aggregationMs": [], "orientationMs": []}
        for arm in ("reuse", "rebuild")
    }
    equivalent = True

    with TemporaryDirectory() as directory:
        temporary = Path(directory)
        root = temporary / "project"
        source = make_fixture(root, documents)
        baseline = temporary / "baseline-index"
        run_command(
            str(REPO_ROOT / "build_project_index.py"),
            "--root", str(root), "--output-root", str(baseline),
            "--no-progress", "--no-git-ignore",
        )
        baseline_orientation = json.loads((baseline / "orientation.json").read_text(encoding="utf-8"))
        if baseline_orientation["counts"]["nodes"] != documents:
            raise RuntimeError("fixture did not produce the requested orientation nodes")

        for run in range(runs):
            source.write_text(f"int changed_{run}() {{ return {run + 1}; }}\n", encoding="utf-8")
            outputs: dict[str, dict[str, Any]] = {}
            order = ("reuse", "rebuild") if run % 2 == 0 else ("rebuild", "reuse")

            for arm in order:
                index_root = temporary / f"index-{run}-{arm}"
                shutil.copytree(baseline, index_root)
                if arm == "rebuild":
                    orientation_path = index_root / "orientation.json"
                    cached = json.loads(orientation_path.read_text(encoding="utf-8"))
                    cached["documentStamps"] = {}
                    orientation_path.write_text(json.dumps(cached, indent=2), encoding="utf-8")

                summary, wall_ms = timed_update(root, index_root, source)
                if summary["modified"] != 1 or summary["structuralUnchanged"]:
                    raise RuntimeError(f"{arm} did not take the incremental aggregation path")
                if summary["orientationUpdated"] != (arm == "rebuild"):
                    raise RuntimeError(f"{arm} took the wrong orientation path")

                phases = summary["incrementalAggregationTimings"]
                samples[arm]["wallMs"].append(wall_ms)
                samples[arm]["aggregationMs"].append(round(sum(p["seconds"] for p in phases) * 1000, 3))
                samples[arm]["orientationMs"].append(round(sum(
                    p["seconds"] for p in phases
                    if p["phase"] in {"check orientation docs", "reuse orientation docs", "rebuild orientation docs", "write orientation docs", "skip orientation write"}
                ) * 1000, 3))
                outputs[arm] = {
                    "manifest": json.loads((index_root / "manifest.json").read_text(encoding="utf-8")),
                    "orientation": json.loads((index_root / "orientation.json").read_text(encoding="utf-8")),
                }

            equivalent = equivalent and outputs["reuse"] == outputs["rebuild"]

    medians = {
        arm: {key: round(statistics.median(values), 3) for key, values in data.items()}
        for arm, data in samples.items()
    }
    return {
        "fixtureDocuments": documents,
        "runsPerArm": runs,
        "equivalent": equivalent,
        "samples": samples,
        "medianMs": medians,
        "wallSpeedup": round(medians["rebuild"]["wallMs"] / medians["reuse"]["wallMs"], 2),
        "aggregationSpeedup": round(medians["rebuild"]["aggregationMs"] / medians["reuse"]["aggregationMs"], 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure source-only incremental updates with orientation reuse versus rebuild."
    )
    parser.add_argument("--documents", type=int, default=200, help="Synthetic orientation documents (default: 200).")
    parser.add_argument("--runs", type=int, default=3, help="Runs per arm (default: 3).")
    args = parser.parse_args()
    try:
        report = compare_incremental_updates(documents=args.documents, runs=args.runs)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["equivalent"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
