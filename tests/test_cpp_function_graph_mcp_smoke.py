from __future__ import annotations

import json
import subprocess
import sys
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_SRC = REPO_ROOT / "src" / "server"
INDEXER_SRC = REPO_ROOT / "src" / "indexer"
for support_path in (SERVER_SRC, INDEXER_SRC):
    if str(support_path) not in sys.path:
        sys.path.insert(0, str(support_path))

from code_index_mcp_server import CodeIndexTools


class FunctionGraphMcpSmokeTests(unittest.TestCase):
    def test_real_index_tool_path_computes_cache_xrefs_and_structural_edges(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            index_root = project_root / ".mcp-cpp-project-indexer"
            (project_root / "sample.cpp").write_text(
                "\n".join(
                    [
                        "namespace App {",
                        "void Helper();",
                        "class Painter {",
                        "    int _OverlayPosition;",
                        "    void Paint();",
                        "};",
                        "void Painter::Paint()",
                        "{",
                        "    Helper();",
                        "    if (_OverlayPosition > 0) {",
                        "        _OverlayPosition = 1;",
                        "    }",
                        "    SendMessageW();",
                        "}",
                        "void Helper()",
                        "{",
                        "}",
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "build_project_index.py"),
                    "--root",
                    str(project_root),
                    "--output-root",
                    str(index_root),
                    "--jobs",
                    "1",
                    "--no-progress",
                    "--print-summary-json",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            tools = CodeIndexTools(project_root=project_root, index_root=index_root)
            try:
                paint_symbol_id = self._definition_symbol_id(tools, "Paint")

                graph = self._call_json(
                    tools.get_function_body_graph(
                        {
                            "symbolId": paint_symbol_id,
                            "mode": "compute_if_missing",
                            "responseFormat": "minified",
                        }
                    )
                )
                cache_only = self._call_json(
                    tools.get_function_body_graph(
                        {
                            "symbolId": paint_symbol_id,
                            "mode": "cache_only",
                            "responseFormat": "minified",
                        }
                    )
                )
                xrefs = self._call_json(
                    tools.get_call_xrefs_from(
                        {
                            "symbolId": paint_symbol_id,
                            "responseFormat": "minified",
                        }
                    )
                )
                filtered = self._call_json(
                    tools.get_function_body_graph(
                        {
                            "symbolId": paint_symbol_id,
                            "mode": "refresh",
                            "includeDataAccess": False,
                            "includeControlFlow": False,
                            "responseFormat": "minified",
                        }
                    )
                )
            finally:
                tools.index.close_sqlite_connections()

        edge_statuses = {edge["toText"]: edge["resolutionStatus"] for edge in graph["edges"]}
        edge_kinds = {edge["edgeKind"] for edge in graph["edges"]}
        filtered_edge_kinds = {edge["edgeKind"] for edge in filtered["edges"]}

        self.assertEqual(graph["schema"], "cpp.function_body_graph.v0.1")
        self.assertEqual(graph["status"], "computed")
        self.assertFalse(graph["fromCache"])
        self.assertFalse(graph["behaviorClaimsAllowed"])
        self.assertIn(edge_statuses["Helper"], {"exact", "probable", "ambiguous"})
        self.assertEqual(edge_statuses["SendMessageW"], "external")
        self.assertIn("reads_data_candidate", edge_kinds)
        self.assertIn("writes_data_candidate", edge_kinds)
        self.assertIn("control_flow_marker", edge_kinds)
        self.assertTrue(cache_only["fromCache"])
        self.assertEqual(cache_only["fingerprints"]["graph"], graph["fingerprints"]["graph"])
        self.assertGreaterEqual(xrefs["returnedEdges"], 3)
        self.assertFalse(xrefs["behaviorClaimsAllowed"])
        self.assertNotIn("reads_data_candidate", filtered_edge_kinds)
        self.assertNotIn("writes_data_candidate", filtered_edge_kinds)
        self.assertNotIn("control_flow_marker", filtered_edge_kinds)
        self.assertFalse(filtered["behaviorClaimsAllowed"])

    def _definition_symbol_id(self, tools: CodeIndexTools, query: str) -> str:
        results = self._call_json(
            tools.find_symbol(
                {
                    "query": query,
                    "exactOnly": True,
                    "responseFormat": "minified",
                }
            )
        )
        for item in results:
            if item.get("type") == "function" and int(item.get("endLine") or 0) > int(item.get("startLine") or 0):
                return str(item["symbolId"])
        self.fail(f"Definition symbol not found for {query!r}")

    def _call_json(self, result: dict[str, Any]) -> Any:
        self.assertFalse(result.get("isError"), result)
        return json.loads(str(result["content"][0]["text"]))


if __name__ == "__main__":
    unittest.main()
