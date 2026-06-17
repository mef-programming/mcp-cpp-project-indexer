from __future__ import annotations

import sys
import unittest

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_SRC = REPO_ROOT / "src" / "server"
INDEXER_SRC = REPO_ROOT / "src" / "indexer"
for support_path in (SERVER_SRC, INDEXER_SRC):
    if str(support_path) not in sys.path:
        sys.path.insert(0, str(support_path))

import code_index_mcp_server as server


class FunctionGraphMcpSchemaTests(unittest.TestCase):
    def test_get_function_body_graph_schema_is_registered_and_packable(self) -> None:
        tools = {
            item["name"]: item
            for item in server.tool_definitions(include_orientation=False)
        }

        self.assertIn("get_function_body_graph", tools)
        self.assertIn("get_function_body_graph", server.PACKABLE_TOOL_NAMES)
        self.assertEqual(
            server.CAPABILITY_TOOL_METADATA["get_function_body_graph"]["claimStrength"],
            "source_structure_allowed",
        )
        self.assertFalse(
            server.CAPABILITY_TOOL_METADATA["get_function_body_graph"]["sourceBehaviorAllowed"]
        )

        schema = tools["get_function_body_graph"]["inputSchema"]
        self.assertEqual(schema["required"], ["symbolId"])
        self.assertEqual(
            schema["properties"]["mode"]["enum"],
            ["cache_only", "compute_if_missing", "refresh"],
        )
        self.assertIn("responseFormat", schema["properties"])
        self.assertEqual(schema["properties"]["maxEdges"]["maximum"], 1000)

    def test_xref_and_neighborhood_schemas_are_registered_and_structural(self) -> None:
        tools = {
            item["name"]: item
            for item in server.tool_definitions(include_orientation=False)
        }

        for tool_name in ("get_call_xrefs_from", "get_call_xrefs_to", "get_symbol_neighborhood"):
            with self.subTest(tool_name=tool_name):
                self.assertIn(tool_name, tools)
                self.assertIn(tool_name, server.PACKABLE_TOOL_NAMES)
                self.assertEqual(
                    server.CAPABILITY_TOOL_METADATA[tool_name]["claimStrength"],
                    "source_structure_allowed",
                )
                self.assertFalse(
                    server.CAPABILITY_TOOL_METADATA[tool_name]["sourceBehaviorAllowed"]
                )
                schema = tools[tool_name]["inputSchema"]
                self.assertEqual(schema["required"], ["symbolId"])
                self.assertIn("responseFormat", schema["properties"])

        self.assertEqual(
            tools["get_call_xrefs_from"]["inputSchema"]["properties"]["limit"]["maximum"],
            1000,
        )
        self.assertEqual(
            tools["get_symbol_neighborhood"]["inputSchema"]["properties"]["incomingLimit"]["default"],
            100,
        )


if __name__ == "__main__":
    unittest.main()
