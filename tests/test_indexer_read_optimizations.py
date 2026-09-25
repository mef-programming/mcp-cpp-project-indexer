from __future__ import annotations

import json
import sys
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory


INDEXER_SRC = Path(__file__).resolve().parents[1] / "src" / "indexer"
if str(INDEXER_SRC) not in sys.path:
    sys.path.insert(0, str(INDEXER_SRC))

from build_module_map import build_module_map, load_file_indexes
from cpp_orientation_index import discover_orientation_documents


class IndexerReadOptimizationTests(unittest.TestCase):
    def test_module_map_is_identical_with_sequential_and_parallel_reads(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            files = root / "files"
            files.mkdir()
            manifest_files = [
                {"fileId": "one", "relativePath": "one.ixx"},
                {"fileId": "two", "relativePath": "two.ixx"},
            ]
            (root / "manifest.json").write_text(
                json.dumps({"root": str(root), "files": manifest_files}), encoding="utf-8"
            )
            (files / "one.json").write_text(
                json.dumps({"module": {"fullModuleName": "Example.One"}, "imports": [
                    {"module": "Example.Two", "resolvedModule": "Example.Two", "kind": "import"}
                ]}),
                encoding="utf-8",
            )
            (files / "two.json").write_text(
                json.dumps({"module": {"fullModuleName": "Example.Two"}, "imports": []}),
                encoding="utf-8",
            )

            self.assertEqual(build_module_map(root, jobs=1), build_module_map(root, jobs=4))
            self.assertEqual(build_module_map(root, jobs=1)["counts"]["modules"], 2)
            self.assertEqual(len(load_file_indexes(index_root=root, file_ids=["one", "one"], jobs=4)), 1)

    def test_orientation_discovery_prunes_generated_directories(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("docs", "Intermediate", "Library", "node_modules", ".github"):
                (root / folder).mkdir()
                (root / folder / "README.md").write_text(folder, encoding="utf-8")
            (root / "docs" / "architecture-topology.md").write_text("topology", encoding="utf-8")

            found = [path.relative_to(root).as_posix() for path in discover_orientation_documents(root)]
            self.assertEqual(found, [".github/README.md", "docs/architecture-topology.md", "docs/README.md"])


if __name__ == "__main__":
    unittest.main()
