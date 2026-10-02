from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEXER_SRC = REPO_ROOT / "src" / "indexer"
if str(INDEXER_SRC) not in sys.path:
    sys.path.insert(0, str(INDEXER_SRC))
SERVER_SRC = REPO_ROOT / "src" / "server"
if str(SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(SERVER_SRC))

from code_index_mcp_server import ServerIndexWatcher
from watch_project_index import (
    diff_snapshots,
    orientation_snapshot_matches_index,
    snapshot_orientation_files,
)


def run_script(name: str, *args: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / name), *args, "--print-summary-json", "--no-progress"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return json.loads(completed.stdout)


def run_project_update(root: Path, index_root: Path, *extra: str) -> dict[str, object]:
    summary_path = index_root / "test-update-summary.json"
    subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "update_project_index.py"),
            "--root", str(root), "--index-root", str(index_root),
            "--summary-json-file", str(summary_path), "--no-progress", *extra,
        ],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return json.loads(summary_path.read_text(encoding="utf-8"))


def run_source_update(root: Path, index_root: Path, source: Path, *extra: str) -> dict[str, object]:
    return run_project_update(
        root, index_root, "--known-files-only", "--changed-file", str(source), *extra
    )


class OrientationDocumentRefreshTests(unittest.TestCase):
    def test_source_path_add_and_delete_refresh_document_link_status(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index_root = root / ".mcp-cpp-project-indexer"
            readme = root / "README.md"
            readme.write_text(
                "TopologyKind: topology\nPurpose: Source links\n## Navigation\n"
                "```text\ntarget  added.cpp  Added source file\n```\n",
                encoding="utf-8",
            )
            (root / "sample.cpp").write_text("int sample() { return 1; }\n", encoding="utf-8")
            run_script("build_project_index.py", "--root", str(root), "--output-root", str(index_root))

            def link_status() -> str:
                orientation = json.loads((index_root / "orientation.json").read_text(encoding="utf-8"))
                return orientation["nodes"][0]["navigation"][0]["pathStatus"]

            self.assertEqual(link_status(), "unresolved")
            added = root / "added.cpp"
            added.write_text("int added() { return 2; }\n", encoding="utf-8")
            self.assertTrue(run_project_update(root, index_root)["orientationUpdated"])
            self.assertEqual(link_status(), "resolved")

            added.unlink()
            self.assertTrue(run_project_update(root, index_root)["orientationUpdated"])
            self.assertEqual(link_status(), "unresolved")

    def test_server_watcher_reloads_after_document_only_refresh(self) -> None:
        with TemporaryDirectory() as directory:
            summary_path = Path(directory) / "summary.json"
            summary_path.write_text(
                json.dumps({"added": 0, "modified": 0, "deleted": 0,
                            "structuralUnchanged": True, "orientationUpdated": True}),
                encoding="utf-8",
            )
            self.assertTrue(ServerIndexWatcher._summary_has_index_changes(summary_path))

    def test_standalone_watcher_refreshes_document_without_source_change(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index_root = root / ".mcp-cpp-project-indexer"
            (root / "sample.cpp").write_text("int sample() { return 1; }\n", encoding="utf-8")
            readme = root / "README.md"
            readme.write_text("TopologyKind: topology\nPurpose: Before watcher\n", encoding="utf-8")
            run_script("build_project_index.py", "--root", str(root), "--output-root", str(index_root))

            watcher = subprocess.Popen(
                [
                    sys.executable, str(REPO_ROOT / "watch_project_index.py"),
                    "--root", str(root), "--index-root", str(index_root),
                    "--poll-interval", "0.1", "--debounce", "0.1", "--no-module-map",
                ],
                cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 10
                while not (index_root / ".watcher.lock").exists() and time.monotonic() < deadline:
                    self.assertIsNone(watcher.poll())
                    time.sleep(0.05)
                self.assertTrue((index_root / ".watcher.lock").exists())
                time.sleep(0.25)
                readme.write_text("TopologyKind: topology\nPurpose: After watcher edit\n", encoding="utf-8")

                while time.monotonic() < deadline:
                    self.assertIsNone(watcher.poll())
                    saved = json.loads((index_root / "orientation.json").read_text(encoding="utf-8"))
                    if saved["nodes"][0]["purpose"] == "After watcher edit":
                        break
                    time.sleep(0.1)
                else:
                    self.fail("Watcher did not refresh the document-only change")
            finally:
                watcher.terminate()
                watcher.wait(timeout=5)

    def test_document_only_update_and_deletion_refresh_orientation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index_root = root / ".mcp-cpp-project-indexer"
            source = root / "sample.cpp"
            readme = root / "README.md"
            source.write_text("int sample() { return 1; }\n", encoding="utf-8")
            readme.write_text("TopologyKind: topology\nPurpose: First version\n", encoding="utf-8")
            run_script("build_project_index.py", "--root", str(root), "--output-root", str(index_root))

            before = snapshot_orientation_files(root)
            self.assertTrue(orientation_snapshot_matches_index(index_root, root, before))
            self.assertEqual(json.loads((index_root / "orientation.json").read_text(encoding="utf-8"))["counts"]["nodes"], 1)

            readme.write_text("TopologyKind: topology\nPurpose: Updated document text\n", encoding="utf-8")
            changed = snapshot_orientation_files(root)
            self.assertTrue(diff_snapshots(before, changed, root=root).changed)
            self.assertFalse(orientation_snapshot_matches_index(index_root, root, changed))

            summary = run_script("update_project_index.py", "--root", str(root), "--index-root", str(index_root), "--orientation-only")
            self.assertTrue(summary["orientationUpdated"])
            self.assertEqual((summary["added"], summary["modified"], summary["deleted"]), (0, 0, 0))
            self.assertTrue(orientation_snapshot_matches_index(index_root, root, changed))
            orientation = json.loads((index_root / "orientation.json").read_text(encoding="utf-8"))
            self.assertEqual(orientation["nodes"][0]["purpose"], "Updated document text")

            readme.unlink()
            deleted = snapshot_orientation_files(root)
            self.assertTrue(diff_snapshots(changed, deleted, root=root).changed)
            summary = run_script("update_project_index.py", "--root", str(root), "--index-root", str(index_root), "--orientation-only")
            self.assertTrue(summary["orientationUpdated"])
            self.assertEqual(json.loads((index_root / "orientation.json").read_text(encoding="utf-8"))["counts"]["nodes"], 0)
            self.assertEqual(json.loads((index_root / "manifest.json").read_text(encoding="utf-8"))["counts"]["orientationNodes"], 0)

    def test_source_only_update_reuses_orientation_then_doc_change_rebuilds(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index_root = root / ".mcp-cpp-project-indexer"
            source = root / "sample.cpp"
            readme = root / "README.md"
            source.write_text("int sample() { return 1; }\n", encoding="utf-8")
            readme.write_text("TopologyKind: topology\nPurpose: Original\n", encoding="utf-8")
            run_script("build_project_index.py", "--root", str(root), "--output-root", str(index_root))

            orientation_path = index_root / "orientation.json"
            original = orientation_path.read_bytes()
            original_mtime = orientation_path.stat().st_mtime_ns
            source.write_text("int renamed() { return 2; }\n", encoding="utf-8")
            summary = run_source_update(root, index_root, source)
            self.assertEqual(summary["modified"], 1)
            self.assertFalse(summary["orientationUpdated"])
            self.assertIn("reuse orientation docs", [item["phase"] for item in summary["incrementalAggregationTimings"]])
            self.assertEqual(orientation_path.read_bytes(), original)
            self.assertEqual(orientation_path.stat().st_mtime_ns, original_mtime)

            readme.write_text("TopologyKind: topology\nPurpose: Changed documentation\n", encoding="utf-8")
            source.write_text("int changed_again() { return 3; }\n", encoding="utf-8")
            summary = run_source_update(root, index_root, source)
            self.assertTrue(summary["orientationUpdated"])
            orientation = json.loads(orientation_path.read_text(encoding="utf-8"))
            self.assertEqual(orientation["nodes"][0]["purpose"], "Changed documentation")

            summary = run_source_update(root, index_root, source, "--force")
            self.assertTrue(summary["orientationUpdated"])


if __name__ == "__main__":
    unittest.main()
