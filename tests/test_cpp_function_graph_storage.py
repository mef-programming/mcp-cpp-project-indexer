from __future__ import annotations

import sys
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEXER_SRC = REPO_ROOT / "src" / "indexer"
if str(INDEXER_SRC) not in sys.path:
    sys.path.insert(0, str(INDEXER_SRC))

from cpp_function_graph_cache import (
    FunctionGraphCacheKey,
    ast_cache_key_for_extract,
    graph_cache_key_for_result,
)
from cpp_function_graph_model import (
    FUNCTION_GRAPH_SCHEMA,
    SOURCE_STRUCTURE_CLAIM_STRENGTH,
    FunctionAstExtract,
    FunctionGraphEdge,
    FunctionGraphFingerprints,
    FunctionGraphResult,
)
from cpp_function_graph_storage import FunctionGraphStorage, function_graph_db_path
from cpp_index_sqlite import SQLITE_INDEX_FILENAME


class FunctionGraphStorageTests(unittest.TestCase):
    def test_ast_extract_cache_round_trip_uses_existing_index_db_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            index_root = Path(temp_dir) / ".mcp-cpp-project-indexer"
            storage = FunctionGraphStorage.from_index_root(index_root)
            extract = FunctionAstExtract(
                symbol_id="fn-paint",
                source_fingerprint="sha256:source",
                parser_id="fixture",
                parser_version="v1",
                extractor_version="extractor-v1",
                calls=(
                    {
                        "callee": "Draw",
                        "line": 12,
                    },
                ),
            )

            key = ast_cache_key_for_extract(extract)
            storage.store_ast_extract(key, extract)
            loaded = storage.load_ast_extract(key)

            self.assertEqual(function_graph_db_path(index_root).name, SQLITE_INDEX_FILENAME)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded, extract)

    def test_graph_cache_round_trip_and_edge_lookup(self) -> None:
        with TemporaryDirectory() as temp_dir:
            storage = FunctionGraphStorage.from_index_root(Path(temp_dir) / ".mcp-cpp-project-indexer")
            edge = FunctionGraphEdge(
                from_symbol_id="fn-paint",
                edge_kind="calls_candidate",
                to_text="Draw",
                to_symbol_id="fn-draw",
                resolution_status="probable",
                confidence=0.82,
                basis=("same_namespace", "same_file"),
                candidates=(
                    {
                        "symbolId": "fn-draw",
                        "qualifiedName": "App::Draw",
                    },
                ),
            )
            result = FunctionGraphResult(
                schema=FUNCTION_GRAPH_SCHEMA,
                status="computed",
                from_cache=False,
                symbol_id="fn-paint",
                function_name="Paint",
                qualified_name="App::Painter::Paint",
                file="paint.cpp",
                start_line=10,
                end_line=15,
                parser_id="fixture",
                parser_version="v1",
                resolver_version="resolver-v1",
                claim_strength=SOURCE_STRUCTURE_CLAIM_STRENGTH,
                behavior_claims_allowed=False,
                fingerprints=FunctionGraphFingerprints(
                    function_body="sha256:source",
                    graph="sha256:graph",
                    file="sha256:file",
                    symbol_index="sha256:symbols",
                    module_visibility="sha256:modules",
                ),
                edges=(edge,),
            )
            key = graph_cache_key_for_result(
                result,
                file_fingerprint="sha256:file",
                symbol_index_fingerprint="sha256:symbols",
                module_visibility_fingerprint="sha256:modules",
            )

            storage.store_graph_result(key, result)
            loaded = storage.load_graph_result(key)
            from_edges = storage.list_edges_from("fn-paint")
            to_edges = storage.list_edges_to("fn-draw")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.fingerprints.graph, "sha256:graph")
        self.assertEqual(loaded.edges, (edge,))
        self.assertEqual(len(from_edges), 1)
        self.assertEqual(from_edges[0]["toSymbolId"], "fn-draw")
        self.assertEqual(from_edges[0]["claimStrength"], SOURCE_STRUCTURE_CLAIM_STRENGTH)
        self.assertFalse(from_edges[0]["behaviorClaimsAllowed"])
        self.assertEqual(to_edges, from_edges)

    def test_graph_cache_miss_when_visibility_fingerprint_changes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            storage = FunctionGraphStorage.from_index_root(Path(temp_dir) / ".mcp-cpp-project-indexer")
            result = FunctionGraphResult(
                schema=FUNCTION_GRAPH_SCHEMA,
                status="computed",
                from_cache=False,
                symbol_id="fn-paint",
                function_name="Paint",
                qualified_name="App::Painter::Paint",
                file="paint.cpp",
                start_line=10,
                end_line=15,
                parser_id="fixture",
                parser_version="v1",
                resolver_version="resolver-v1",
                claim_strength=SOURCE_STRUCTURE_CLAIM_STRENGTH,
                behavior_claims_allowed=False,
                fingerprints=FunctionGraphFingerprints(
                    function_body="sha256:source",
                    graph="sha256:graph",
                ),
                edges=(),
            )
            key = FunctionGraphCacheKey(
                function_symbol_id="fn-paint",
                function_body_fingerprint="sha256:source",
                file_fingerprint="sha256:file",
                symbol_index_fingerprint="sha256:symbols",
                module_visibility_fingerprint="sha256:modules-a",
                parser_id="fixture",
                parser_version="v1",
                resolver_version="resolver-v1",
            )
            changed_visibility_key = FunctionGraphCacheKey(
                function_symbol_id="fn-paint",
                function_body_fingerprint="sha256:source",
                file_fingerprint="sha256:file",
                symbol_index_fingerprint="sha256:symbols",
                module_visibility_fingerprint="sha256:modules-b",
                parser_id="fixture",
                parser_version="v1",
                resolver_version="resolver-v1",
            )

            storage.store_graph_result(key, result)

            self.assertIsNotNone(storage.load_graph_result(key))
            self.assertIsNone(storage.load_graph_result(changed_visibility_key))

    def test_storing_new_graph_replaces_outgoing_edges_for_symbol(self) -> None:
        with TemporaryDirectory() as temp_dir:
            storage = FunctionGraphStorage.from_index_root(Path(temp_dir) / ".mcp-cpp-project-indexer")

            def make_result(graph_fingerprint: str, callee: str) -> FunctionGraphResult:
                return FunctionGraphResult(
                    schema=FUNCTION_GRAPH_SCHEMA,
                    status="computed",
                    from_cache=False,
                    symbol_id="fn-paint",
                    function_name="Paint",
                    qualified_name="App::Painter::Paint",
                    file="paint.cpp",
                    start_line=10,
                    end_line=15,
                    parser_id="fixture",
                    parser_version="v1",
                    resolver_version="resolver-v1",
                    claim_strength=SOURCE_STRUCTURE_CLAIM_STRENGTH,
                    behavior_claims_allowed=False,
                    fingerprints=FunctionGraphFingerprints(
                        function_body="sha256:source",
                        graph=graph_fingerprint,
                        file="sha256:file",
                        symbol_index="sha256:symbols",
                        module_visibility="sha256:modules",
                    ),
                    edges=(
                        FunctionGraphEdge(
                            from_symbol_id="fn-paint",
                            edge_kind="calls_candidate",
                            to_text=callee,
                            to_symbol_id=f"fn-{callee.casefold()}",
                            resolution_status="probable",
                            confidence=0.82,
                            basis=("same_file",),
                        ),
                    ),
                )

            first = make_result("sha256:graph-a", "OldHelper")
            second = make_result("sha256:graph-b", "NewHelper")
            storage.store_graph_result(
                graph_cache_key_for_result(
                    first,
                    file_fingerprint="sha256:file",
                    symbol_index_fingerprint="sha256:symbols",
                    module_visibility_fingerprint="sha256:modules",
                ),
                first,
            )
            storage.store_graph_result(
                graph_cache_key_for_result(
                    second,
                    file_fingerprint="sha256:file",
                    symbol_index_fingerprint="sha256:symbols",
                    module_visibility_fingerprint="sha256:modules",
                ),
                second,
            )

            edges = storage.list_edges_from("fn-paint")

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["toText"], "NewHelper")

    def test_cache_stats_and_prune_versions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            storage = FunctionGraphStorage.from_index_root(Path(temp_dir) / ".mcp-cpp-project-indexer")

            def make_result(graph_fingerprint: str, parser_version: str, resolver_version: str) -> FunctionGraphResult:
                return FunctionGraphResult(
                    schema=FUNCTION_GRAPH_SCHEMA,
                    status="computed",
                    from_cache=False,
                    symbol_id=f"fn-{graph_fingerprint[-1]}",
                    function_name="Paint",
                    qualified_name="App::Painter::Paint",
                    file="paint.cpp",
                    start_line=10,
                    end_line=15,
                    parser_id="fixture",
                    parser_version=parser_version,
                    resolver_version=resolver_version,
                    claim_strength=SOURCE_STRUCTURE_CLAIM_STRENGTH,
                    behavior_claims_allowed=False,
                    fingerprints=FunctionGraphFingerprints(
                        function_body=f"sha256:source-{graph_fingerprint[-1]}",
                        graph=graph_fingerprint,
                        file="sha256:file",
                        symbol_index="sha256:symbols",
                        module_visibility="sha256:modules",
                    ),
                    edges=(
                        FunctionGraphEdge(
                            from_symbol_id=f"fn-{graph_fingerprint[-1]}",
                            edge_kind="calls_candidate",
                            to_text="Helper",
                            to_symbol_id="fn-helper",
                            resolution_status="probable",
                            confidence=0.82,
                            basis=("same_file",),
                        ),
                    ),
                )

            old_result = make_result("sha256:graph-a", "parser-old", "resolver-old")
            current_result = make_result("sha256:graph-b", "parser-current", "resolver-current")
            for result in (old_result, current_result):
                storage.store_graph_result(
                    graph_cache_key_for_result(
                        result,
                        file_fingerprint="sha256:file",
                        symbol_index_fingerprint="sha256:symbols",
                        module_visibility_fingerprint="sha256:modules",
                    ),
                    result,
                )

            before = storage.cache_stats()
            pruned = storage.prune_cache_versions(
                keep_parser_versions={"parser-current"},
                keep_resolver_versions={"resolver-current"},
            )
            after = storage.cache_stats()

        self.assertEqual(before["graphResults"], 2)
        self.assertEqual(before["graphEdges"], 2)
        self.assertEqual(pruned["graphResultsPruned"], 1)
        self.assertEqual(pruned["graphEdgesPruned"], 1)
        self.assertEqual(after["graphResults"], 1)
        self.assertEqual(after["graphEdges"], 1)


if __name__ == "__main__":
    unittest.main()
