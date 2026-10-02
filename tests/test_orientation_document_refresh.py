from __future__ import annotations

import json
import subprocess
import sys
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEXER_SRC = REPO_ROOT / "src" / "indexer"
if str(INDEXER_SRC) not in sys.path:
    sys.path.insert(0, str(INDEXER_SRC))

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


class OrientationDocumentRefreshTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
