from __future__ import annotations

import sys
import unittest

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEXER_SRC = REPO_ROOT / "src" / "indexer"
if str(INDEXER_SRC) not in sys.path:
    sys.path.insert(0, str(INDEXER_SRC))

from cpp_function_graph_model import FunctionAstExtract, FunctionVisibilityContext
from cpp_function_graph_resolver import resolve_function_graph_edges


def visibility_with_symbols(*symbols: dict) -> FunctionVisibilityContext:
    return FunctionVisibilityContext(
        file_id="file-1",
        file_path="paint.cpp",
        function_symbol_id="fn-paint",
        current_namespace=("App",),
        current_class_symbol_id="cls-1",
        current_class_name="App::Painter",
        imported_modules=("App.Core",),
        visible_exported_symbols=(),
        same_file_symbols=tuple(symbols),
        same_file_data=(),
        member_data=(),
    )


class FunctionGraphResolverTests(unittest.TestCase):
    def test_resolves_this_member_against_current_class(self) -> None:
        visibility = visibility_with_symbols(
            {
                "symbolId": "fn-reset",
                "type": "method",
                "shortName": "Reset",
                "qualifiedName": "App::Painter::Reset",
                "container": "App::Painter",
            }
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            calls=(
                {
                    "callee": "this->Reset",
                    "callKind": "member",
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].edge_kind, "calls_resolved")
        self.assertEqual(edges[0].resolution_status, "exact")
        self.assertEqual(edges[0].to_symbol_id, "fn-reset")
        self.assertIn("current_class", edges[0].basis)

    def test_resolves_same_file_namespace_function_as_probable_candidate(self) -> None:
        visibility = visibility_with_symbols(
            {
                "symbolId": "fn-helper",
                "type": "function",
                "shortName": "Helper",
                "qualifiedName": "App::Helper",
                "container": "App",
            }
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            calls=(
                {
                    "callee": "Helper",
                    "callKind": "unqualified",
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(edges[0].edge_kind, "calls_candidate")
        self.assertEqual(edges[0].resolution_status, "probable")
        self.assertEqual(edges[0].to_symbol_id, "fn-helper")
        self.assertIn("same_namespace", edges[0].basis)

    def test_ambiguous_overload_returns_candidate_set(self) -> None:
        visibility = visibility_with_symbols(
            {
                "symbolId": "fn-draw-1",
                "type": "function",
                "shortName": "Draw",
                "qualifiedName": "App::Draw(int)",
                "container": "App",
            },
            {
                "symbolId": "fn-draw-2",
                "type": "function",
                "shortName": "Draw",
                "qualifiedName": "App::Draw(float)",
                "container": "App",
            },
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            calls=(
                {
                    "callee": "Draw",
                    "callKind": "unqualified",
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(edges[0].edge_kind, "calls_ambiguous")
        self.assertEqual(edges[0].resolution_status, "ambiguous")
        self.assertIsNone(edges[0].to_symbol_id)
        self.assertEqual({item["symbolId"] for item in edges[0].candidates}, {"fn-draw-1", "fn-draw-2"})

    def test_qualified_call_resolves_exact_project_symbol(self) -> None:
        visibility = visibility_with_symbols(
            {
                "symbolId": "fn-draw",
                "type": "function",
                "shortName": "Draw",
                "qualifiedName": "App::Draw",
                "container": "App",
            }
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            calls=(
                {
                    "callee": "App::Draw",
                    "callKind": "qualified",
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(edges[0].resolution_status, "exact")
        self.assertEqual(edges[0].to_symbol_id, "fn-draw")

    def test_external_call_is_marked_external(self) -> None:
        visibility = visibility_with_symbols()
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            calls=(
                {
                    "callee": "SendMessageW",
                    "callKind": "unqualified",
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(edges[0].edge_kind, "calls_external")
        self.assertEqual(edges[0].resolution_status, "external")
        self.assertIsNone(edges[0].to_symbol_id)
        self.assertEqual(edges[0].basis, ("not_in_project_symbol_index",))

    def test_using_namespace_adds_ambiguous_candidate_scope(self) -> None:
        visibility = FunctionVisibilityContext(
            file_id="file-1",
            file_path="paint.cpp",
            function_symbol_id="fn-paint",
            current_namespace=("App",),
            current_class_symbol_id=None,
            current_class_name=None,
            imported_modules=(),
            visible_exported_symbols=(),
            same_file_symbols=(
                {
                    "symbolId": "fn-theme-draw",
                    "type": "function",
                    "shortName": "Draw",
                    "qualifiedName": "Theme::Draw",
                    "container": "Theme",
                    "signature": "void Draw(int)",
                },
                {
                    "symbolId": "fn-legacy-draw",
                    "type": "function",
                    "shortName": "Draw",
                    "qualifiedName": "Legacy::Draw",
                    "container": "Legacy",
                    "signature": "void Draw(int)",
                },
            ),
            same_file_data=(),
            member_data=(),
            using_directives=(
                {
                    "namespace": "Theme",
                },
                {
                    "namespace": "Legacy",
                },
            ),
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            calls=(
                {
                    "callee": "Draw",
                    "callKind": "unqualified",
                    "argumentCount": 1,
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(edges[0].edge_kind, "calls_ambiguous")
        self.assertEqual(edges[0].resolution_status, "ambiguous")
        self.assertIn("using_namespace", edges[0].basis)
        self.assertEqual({item["symbolId"] for item in edges[0].candidates}, {"fn-theme-draw", "fn-legacy-draw"})
        self.assertTrue(all("arity_match" in item["basis"] for item in edges[0].candidates))

    def test_namespace_alias_expands_qualified_name(self) -> None:
        visibility = FunctionVisibilityContext(
            file_id="file-1",
            file_path="paint.cpp",
            function_symbol_id="fn-paint",
            current_namespace=("App",),
            current_class_symbol_id=None,
            current_class_name=None,
            imported_modules=(),
            visible_exported_symbols=(),
            same_file_symbols=(
                {
                    "symbolId": "fn-themed-draw",
                    "type": "function",
                    "shortName": "Draw",
                    "qualifiedName": "App::Theme::Draw",
                    "container": "App::Theme",
                    "signature": "void Draw()",
                },
            ),
            same_file_data=(),
            member_data=(),
            namespace_aliases=(
                {
                    "alias": "Theme",
                    "target": "App::Theme",
                },
            ),
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            calls=(
                {
                    "callee": "Theme::Draw",
                    "callKind": "qualified",
                    "argumentCount": 0,
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(edges[0].resolution_status, "exact")
        self.assertEqual(edges[0].to_symbol_id, "fn-themed-draw")
        self.assertIn("namespace_alias", edges[0].basis)

    def test_overload_scoring_prefers_arity_without_fake_exact_match(self) -> None:
        visibility = visibility_with_symbols(
            {
                "symbolId": "fn-draw-1",
                "type": "function",
                "shortName": "Draw",
                "qualifiedName": "App::Draw(int)",
                "container": "App",
                "signature": "void Draw(int value)",
            },
            {
                "symbolId": "fn-draw-2",
                "type": "function",
                "shortName": "Draw",
                "qualifiedName": "App::Draw(int, int)",
                "container": "App",
                "signature": "void Draw(int lhs, int rhs)",
            },
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            calls=(
                {
                    "callee": "Draw",
                    "callKind": "unqualified",
                    "argumentCount": 2,
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(edges[0].resolution_status, "ambiguous")
        self.assertIsNone(edges[0].to_symbol_id)
        self.assertEqual(edges[0].candidates[0]["symbolId"], "fn-draw-2")
        self.assertGreater(edges[0].candidates[0]["score"], edges[0].candidates[1]["score"])
        self.assertIn("arity_match", edges[0].candidates[0]["basis"])

    def test_member_call_uses_local_type_hint_as_probable_candidate(self) -> None:
        visibility = FunctionVisibilityContext(
            file_id="file-1",
            file_path="paint.cpp",
            function_symbol_id="fn-paint",
            current_namespace=("App",),
            current_class_symbol_id=None,
            current_class_name=None,
            imported_modules=(),
            visible_exported_symbols=(),
            same_file_symbols=(
                {
                    "symbolId": "fn-renderer-draw",
                    "type": "method",
                    "shortName": "Draw",
                    "qualifiedName": "App::Renderer::Draw",
                    "container": "App::Renderer",
                    "signature": "void Draw()",
                },
            ),
            same_file_data=(),
            member_data=(),
            local_declarations=(
                {
                    "name": "renderer",
                    "typeText": "App::Renderer",
                },
            ),
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            calls=(
                {
                    "callee": "renderer.Draw",
                    "callKind": "member",
                    "argumentCount": 0,
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(edges[0].edge_kind, "calls_candidate")
        self.assertEqual(edges[0].resolution_status, "probable")
        self.assertEqual(edges[0].to_symbol_id, "fn-renderer-draw")
        self.assertIn("local_type_hint", edges[0].basis)

    def test_member_call_local_type_hint_matches_container_tail(self) -> None:
        visibility = FunctionVisibilityContext(
            file_id="file-1",
            file_path="paint.cpp",
            function_symbol_id="fn-paint",
            current_namespace=("App",),
            current_class_symbol_id=None,
            current_class_name=None,
            imported_modules=(),
            visible_exported_symbols=(),
            same_file_symbols=(
                {
                    "symbolId": "fn-widget-draw",
                    "type": "method",
                    "shortName": "Draw",
                    "qualifiedName": "App::Widget::Draw",
                    "container": "App::Widget",
                    "signature": "void Draw()",
                },
            ),
            same_file_data=(),
            member_data=(),
            local_declarations=(
                {
                    "name": "widget",
                    "typeText": "Widget *",
                },
            ),
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            calls=(
                {
                    "callee": "widget->Draw",
                    "callKind": "member",
                    "argumentCount": 0,
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(edges[0].edge_kind, "calls_candidate")
        self.assertEqual(edges[0].resolution_status, "probable")
        self.assertEqual(edges[0].to_symbol_id, "fn-widget-draw")
        self.assertIn("local_type_hint", edges[0].basis)

    def test_data_access_and_control_flow_markers_are_structural_edges(self) -> None:
        visibility = FunctionVisibilityContext(
            file_id="file-1",
            file_path="paint.cpp",
            function_symbol_id="fn-paint",
            current_namespace=("App",),
            current_class_symbol_id="cls-1",
            current_class_name="App::Painter",
            imported_modules=(),
            visible_exported_symbols=(),
            same_file_symbols=(),
            same_file_data=(),
            member_data=(
                {
                    "dataId": "data-overlay",
                    "name": "_OverlayPosition",
                    "shortName": "_OverlayPosition",
                    "qualifiedName": "App::Painter::_OverlayPosition",
                    "container": "App::Painter",
                    "typeText": "int",
                },
            ),
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            member_accesses=(
                {
                    "text": "_OverlayPosition",
                    "accessKind": "write_candidate",
                },
            ),
            control_flow=(
                {
                    "marker": "if",
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual([edge.edge_kind for edge in edges], ["writes_data_candidate", "control_flow_marker"])
        self.assertEqual(edges[0].resolution_status, "probable")
        self.assertEqual(edges[0].candidates[0]["dataId"], "data-overlay")
        self.assertIn("indexed_member_data", edges[0].basis)
        self.assertEqual(edges[1].resolution_status, "exact")
        self.assertEqual(edges[1].to_text, "if")
        self.assertFalse(edges[1].behavior_claims_allowed)

    def test_data_and_control_flow_edges_can_be_disabled(self) -> None:
        visibility = visibility_with_symbols()
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            member_accesses=(
                {
                    "text": "_OverlayPosition",
                    "accessKind": "read_candidate",
                },
            ),
            control_flow=(
                {
                    "marker": "return",
                },
            ),
        )

        edges = resolve_function_graph_edges(
            ast_extract=ast,
            visibility=visibility,
            include_data_access=False,
            include_control_flow=False,
        )

        self.assertEqual(edges, ())

    def test_qualified_data_access_records_qualified_basis(self) -> None:
        visibility = FunctionVisibilityContext(
            file_id="file-1",
            file_path="paint.cpp",
            function_symbol_id="fn-paint",
            current_namespace=("App",),
            current_class_symbol_id=None,
            current_class_name=None,
            imported_modules=(),
            visible_exported_symbols=(),
            same_file_symbols=(),
            same_file_data=(
                {
                    "dataId": "data-count",
                    "name": "g_count",
                    "qualifiedName": "App::g_count",
                    "container": "App",
                    "typeText": "int",
                },
            ),
            member_data=(),
        )
        ast = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            member_accesses=(
                {
                    "text": "App::g_count",
                    "accessKind": "read_candidate",
                },
            ),
        )

        edges = resolve_function_graph_edges(ast_extract=ast, visibility=visibility)

        self.assertEqual(edges[0].edge_kind, "reads_data_candidate")
        self.assertEqual(edges[0].resolution_status, "probable")
        self.assertEqual(edges[0].candidates[0]["dataId"], "data-count")
        self.assertIn("qualified_data_name", edges[0].basis)


if __name__ == "__main__":
    unittest.main()
