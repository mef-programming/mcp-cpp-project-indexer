from __future__ import annotations

import sys
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEXER_SRC = REPO_ROOT / "src" / "indexer"
if str(INDEXER_SRC) not in sys.path:
    sys.path.insert(0, str(INDEXER_SRC))

from cpp_function_graph_model import (
    BEHAVIOR_CLAIMS_ALLOWED,
    FUNCTION_GRAPH_SCHEMA,
    SOURCE_STRUCTURE_CLAIM_STRENGTH,
    FunctionGraphRequest,
)
from cpp_function_graph_service import FunctionGraphSourceError, FunctionGraphSourceService, text_fingerprint


class FakeIndex:
    def __init__(self) -> None:
        self.uses_sqlite = False
        self.file_by_id = {
            "file-1": {
                "fileId": "file-1",
                "relativePath": "sample.cpp",
            }
        }
        self.symbols = [
            {
                "symbolId": "fn-1",
                "fileId": "file-1",
                "type": "function",
                "shortName": "add",
                "qualifiedName": "add",
                "startLine": 3,
                "endLine": 6,
                "signature": "int add(int lhs, int rhs)",
            },
            {
                "symbolId": "class-1",
                "fileId": "file-1",
                "type": "class",
                "shortName": "Thing",
                "qualifiedName": "Thing",
                "startLine": 1,
                "endLine": 1,
                "signature": "class Thing",
            },
            {
                "symbolId": "fn-helper",
                "fileId": "file-1",
                "type": "function",
                "shortName": "Helper",
                "qualifiedName": "Helper",
                "startLine": 8,
                "endLine": 8,
                "signature": "void Helper()",
            },
            {
                "symbolId": "fn-paint",
                "fileId": "file-1",
                "type": "function",
                "shortName": "Paint",
                "qualifiedName": "Paint",
                "startLine": 10,
                "endLine": 14,
                "signature": "void Paint()",
            },
        ]
        self.symbol_by_id = {
            str(symbol["symbolId"]): symbol
            for symbol in self.symbols
        }
        self.data = []
        self.modules = {}


class FunctionGraphSourceServiceTests(unittest.TestCase):
    def test_extract_function_source_returns_raw_text_and_fingerprint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "sample.cpp").write_text(
                "\n".join(
                    [
                        "#include <cstdint>",
                        "",
                        "int add(int lhs, int rhs)",
                        "{",
                        "    return lhs + rhs;",
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            service = FunctionGraphSourceService(project_root=project_root, index=FakeIndex())
            result = service.extract_function_source("fn-1")

        expected_text = "\n".join(
            [
                "int add(int lhs, int rhs)",
                "{",
                "    return lhs + rhs;",
                "}",
            ]
        ) + "\n"
        self.assertEqual(result.text, expected_text)
        self.assertEqual(result.symbol_id, "fn-1")
        self.assertEqual(result.relative_path, "sample.cpp")
        self.assertEqual(result.start_line, 3)
        self.assertEqual(result.end_line, 6)
        self.assertEqual(result.base_line, 3)
        self.assertEqual(result.base_byte, len("#include <cstdint>\n\n".encode("utf-8")))
        self.assertEqual(result.function_body_fingerprint, text_fingerprint(expected_text))

    def test_missing_symbol_returns_structured_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            service = FunctionGraphSourceService(project_root=Path(temp_dir), index=FakeIndex())

            with self.assertRaises(FunctionGraphSourceError) as raised:
                service.extract_function_source("missing")

        self.assertEqual(raised.exception.error.code, "symbol_not_found")
        self.assertEqual(raised.exception.error.symbol_id, "missing")

    def test_non_callable_symbol_returns_structured_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            service = FunctionGraphSourceService(project_root=Path(temp_dir), index=FakeIndex())

            with self.assertRaises(FunctionGraphSourceError) as raised:
                service.extract_function_source("class-1")

        self.assertEqual(raised.exception.error.code, "not_callable_symbol")
        self.assertEqual(raised.exception.error.symbol_id, "class-1")

    def test_empty_graph_result_has_stable_contract(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "sample.cpp").write_text(
                "\n".join(
                    [
                        "#include <cstdint>",
                        "",
                        "int add(int lhs, int rhs)",
                        "{",
                        "    return lhs + rhs;",
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            service = FunctionGraphSourceService(project_root=project_root, index=FakeIndex())
            first = service.build_empty_graph_result(FunctionGraphRequest(symbol_id="fn-1"))
            second = service.build_empty_graph_result("fn-1")

        self.assertEqual(first.schema, FUNCTION_GRAPH_SCHEMA)
        self.assertEqual(first.status, "computed")
        self.assertFalse(first.from_cache)
        self.assertEqual(first.symbol_id, "fn-1")
        self.assertEqual(first.function_name, "add")
        self.assertEqual(first.file, "sample.cpp")
        self.assertEqual(first.start_line, 3)
        self.assertEqual(first.end_line, 6)
        self.assertIsNone(first.parser_id)
        self.assertIsNone(first.parser_version)
        self.assertIsNone(first.resolver_version)
        self.assertEqual(first.claim_strength, SOURCE_STRUCTURE_CLAIM_STRENGTH)
        self.assertEqual(first.behavior_claims_allowed, BEHAVIOR_CLAIMS_ALLOWED)
        self.assertEqual(first.edges, ())
        self.assertTrue(first.fingerprints.function_body.startswith("sha256:"))
        self.assertTrue(first.fingerprints.graph.startswith("sha256:"))
        self.assertEqual(first.fingerprints.graph, second.fingerprints.graph)

    def test_get_function_body_graph_computes_and_returns_cached_graph(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            index_root = project_root / ".mcp-cpp-project-indexer"
            (project_root / "sample.cpp").write_text(
                "\n".join(
                    [
                        "#include <windows.h>",
                        "",
                        "int add(int lhs, int rhs)",
                        "{",
                        "    return lhs + rhs;",
                        "}",
                        "",
                        "void Helper();",
                        "",
                        "void Paint()",
                        "{",
                        "    Helper();",
                        "    SendMessageW();",
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            service = FunctionGraphSourceService(
                project_root=project_root,
                index=FakeIndex(),
                index_root=index_root,
            )
            request = FunctionGraphRequest(symbol_id="fn-paint", mode="compute_if_missing")

            first = service.get_function_body_graph(
                request,
                file_fingerprint="sha256:file",
                symbol_index_fingerprint="sha256:symbols",
                module_visibility_fingerprint="sha256:modules",
            )
            second = service.get_function_body_graph(
                FunctionGraphRequest(symbol_id="fn-paint", mode="cache_only"),
                file_fingerprint="sha256:file",
                symbol_index_fingerprint="sha256:symbols",
                module_visibility_fingerprint="sha256:modules",
            )
            xrefs_from = service.get_call_xrefs_from("fn-paint")
            xrefs_to = service.get_call_xrefs_to("fn-helper")
            neighborhood = service.get_symbol_neighborhood("fn-helper")

        self.assertEqual(first.status, "computed")
        self.assertFalse(first.from_cache)
        self.assertEqual(first.claim_strength, SOURCE_STRUCTURE_CLAIM_STRENGTH)
        self.assertFalse(first.behavior_claims_allowed)
        self.assertEqual(
            {(edge.to_text, edge.resolution_status) for edge in first.edges},
            {("Helper", "probable"), ("SendMessageW", "external")},
        )
        self.assertEqual(second.status, "computed")
        self.assertTrue(second.from_cache)
        self.assertEqual(second.fingerprints.graph, first.fingerprints.graph)
        self.assertEqual(xrefs_from["direction"], "from")
        self.assertEqual(xrefs_from["returnedEdges"], 2)
        self.assertFalse(xrefs_from["behaviorClaimsAllowed"])
        self.assertEqual(xrefs_to["direction"], "to")
        self.assertEqual(xrefs_to["returnedEdges"], 1)
        self.assertEqual(xrefs_to["edges"][0]["fromSymbolId"], "fn-paint")
        self.assertEqual(neighborhood["target"]["symbolId"], "fn-helper")
        self.assertEqual(neighborhood["callerCount"], 1)
        self.assertEqual(neighborhood["callers"][0]["symbolId"], "fn-paint")
        self.assertFalse(neighborhood["behaviorClaimsAllowed"])

    def test_graph_cache_options_change_causes_cache_miss(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            index_root = project_root / ".mcp-cpp-project-indexer"
            (project_root / "sample.cpp").write_text(
                "\n".join(
                    [
                        "#include <windows.h>",
                        "",
                        "int add(int lhs, int rhs)",
                        "{",
                        "    return lhs + rhs;",
                        "}",
                        "",
                        "void Helper();",
                        "",
                        "void Paint()",
                        "{",
                        "    Helper();",
                        "    SendMessageW();",
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            service = FunctionGraphSourceService(
                project_root=project_root,
                index=FakeIndex(),
                index_root=index_root,
            )

            first = service.get_function_body_graph(
                FunctionGraphRequest(
                    symbol_id="fn-paint",
                    mode="compute_if_missing",
                    include_external=False,
                ),
                file_fingerprint="sha256:file",
                symbol_index_fingerprint="sha256:symbols",
                module_visibility_fingerprint="sha256:modules",
            )
            second = service.get_function_body_graph(
                FunctionGraphRequest(symbol_id="fn-paint", mode="cache_only"),
                file_fingerprint="sha256:file",
                symbol_index_fingerprint="sha256:symbols",
                module_visibility_fingerprint="sha256:modules",
            )

        self.assertEqual(first.status, "computed")
        self.assertFalse(first.from_cache)
        self.assertEqual({edge.to_text for edge in first.edges}, {"Helper"})
        self.assertEqual(second.status, "cache_miss")
        self.assertFalse(second.from_cache)


if __name__ == "__main__":
    unittest.main()
