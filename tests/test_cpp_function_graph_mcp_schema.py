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

    def test_management_commands_expose_function_graph_cache_maintenance(self) -> None:
        class FakeTools:
            def function_graph_cache_stats(self) -> dict:
                return {"stats": {"graphResults": 2}}

            def function_graph_cache_prune_versions(
                self,
                *,
                keep_parser_versions: set[str],
                keep_resolver_versions: set[str],
                dry_run: bool = True,
                keep_current: bool = False,
            ) -> dict:
                if not keep_parser_versions and not keep_resolver_versions:
                    raise server.McpError(-32602, "keep versions required")
                return {
                    "dryRun": dry_run,
                    "keepCurrent": keep_current,
                    "keepParserVersions": sorted(keep_parser_versions),
                    "keepResolverVersions": sorted(keep_resolver_versions),
                    "pruned": {"graphResultsPruned": 1},
                }

        class FakeRunner:
            default_jobs = 1

            def __init__(self) -> None:
                self.events: list[str] = []

            def append_event(self, event: str) -> None:
                self.events.append(event)

            def status(self) -> dict:
                return {"running": False}

        runner = FakeRunner()
        mcp = server.McpServer.__new__(server.McpServer)
        mcp.tools = FakeTools()
        mcp.management_runner = runner
        mcp.watch_jobs = 1
        mcp.watch_emit_debug_file_indexes = False
        mcp.watch_include_extensionless_headers = False
        mcp.watch_git_ignore = True
        mcp.watch_poll_interval = 1.0
        mcp.watch_debounce = 1.0
        mcp.watch_module_map = True

        stats = mcp.handle_management_command({"command": "function_graph_cache_stats"})
        pruned = mcp.handle_management_command(
            {
                "command": "function_graph_cache_prune_versions",
                "keepParserVersions": ["parser-v1"],
                "keepResolverVersions": ["resolver-v1"],
            }
        )
        committed = mcp.handle_management_command(
            {
                "command": "function_graph_cache_prune_versions",
                "keepParserVersions": ["parser-v1"],
                "keepResolverVersions": ["resolver-v1"],
                "dryRun": False,
            }
        )

        self.assertEqual(stats["functionGraphCache"]["stats"]["graphResults"], 2)
        self.assertTrue(pruned["functionGraphCache"]["dryRun"])
        self.assertEqual(pruned["functionGraphCache"]["keepParserVersions"], ["parser-v1"])
        self.assertEqual(pruned["functionGraphCache"]["keepResolverVersions"], ["resolver-v1"])
        self.assertFalse(committed["functionGraphCache"]["dryRun"])
        self.assertIn("Function graph cache stats", runner.events[0])
        self.assertIn("Function graph cache version prune", runner.events[1])

        with self.assertRaises(server.McpError):
            mcp.handle_management_command({"command": "function_graph_cache_prune_versions"})

    def test_management_status_lists_function_graph_cache_commands(self) -> None:
        runner = server.ManagementCommandRunner(
            indexer_root=REPO_ROOT / "src" / "indexer",
            project_root=REPO_ROOT,
            index_root=REPO_ROOT / ".mcp-cpp-project-indexer",
            default_jobs=1,
        )

        commands = set(runner.status()["availableCommands"])

        self.assertIn("function_graph_cache_stats", commands)
        self.assertIn("function_graph_cache_prune_versions", commands)


if __name__ == "__main__":
    unittest.main()
