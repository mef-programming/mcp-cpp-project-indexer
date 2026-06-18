from __future__ import annotations

import sys
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEXER_SRC = REPO_ROOT / "src" / "indexer"
if str(INDEXER_SRC) not in sys.path:
    sys.path.insert(0, str(INDEXER_SRC))

from cpp_function_graph_model import FunctionAstExtract, FunctionSourceSlice
from cpp_function_graph_visibility import build_function_visibility_context
from cpp_file_index import build_file_index


class FakeVisibilityIndex:
    uses_sqlite = False

    def __init__(self) -> None:
        self.modules = {
            "App.Core": ["file-1", "file-2"],
        }
        self.symbol_by_id = {
            "cls-1": {
                "symbolId": "cls-1",
                "fileId": "file-1",
                "relativePath": "paint.cpp",
                "type": "class",
                "shortName": "Painter",
                "qualifiedName": "App::Painter",
                "container": "App",
                "startLine": 3,
                "endLine": 20,
                "signature": "class Painter",
            },
            "fn-paint": {
                "symbolId": "fn-paint",
                "fileId": "file-1",
                "relativePath": "paint.cpp",
                "type": "method",
                "shortName": "Paint",
                "qualifiedName": "App::Painter::Paint",
                "container": "App::Painter",
                "startLine": 10,
                "endLine": 15,
                "signature": "void Paint()",
            },
            "fn-helper": {
                "symbolId": "fn-helper",
                "fileId": "file-1",
                "relativePath": "paint.cpp",
                "type": "function",
                "shortName": "Helper",
                "qualifiedName": "App::Helper",
                "container": "App",
                "startLine": 22,
                "endLine": 25,
                "signature": "void Helper()",
            },
            "fn-exported": {
                "symbolId": "fn-exported",
                "fileId": "file-2",
                "relativePath": "module.cppm",
                "type": "function",
                "shortName": "Draw",
                "qualifiedName": "App::Draw",
                "container": "App",
                "startLine": 5,
                "endLine": 7,
                "signature": "void Draw()",
            },
        }
        self.symbols = list(self.symbol_by_id.values())
        self.data = [
            {
                "dataId": "data-overlay",
                "fileId": "file-1",
                "relativePath": "paint.cpp",
                "declarationKind": "field",
                "name": "_OverlayPosition",
                "shortName": "_OverlayPosition",
                "qualifiedName": "App::Painter::_OverlayPosition",
                "container": "App::Painter",
                "typeText": "int",
                "startLine": 6,
                "endLine": 6,
            },
            {
                "dataId": "data-global",
                "fileId": "file-1",
                "relativePath": "paint.cpp",
                "declarationKind": "variable",
                "name": "g_count",
                "shortName": "g_count",
                "qualifiedName": "App::g_count",
                "container": "App",
                "typeText": "int",
                "startLine": 2,
                "endLine": 2,
            },
        ]
        self.using_declarations = [
            {
                "fileId": "file-1",
                "name": "Draw",
                "target": "App::Theme::Draw",
                "activeFromLine": 1,
                "activeToLine": 200,
            },
        ]
        self.using_directives = [
            {
                "fileId": "file-1",
                "namespace": "App::Theme",
                "activeFromLine": 1,
                "activeToLine": 200,
            },
        ]
        self.namespace_aliases = [
            {
                "fileId": "file-1",
                "alias": "Theme",
                "target": "App::Theme",
                "activeFromLine": 1,
                "activeToLine": 200,
            },
        ]


class FunctionGraphVisibilityTests(unittest.TestCase):
    def test_file_index_records_relative_using_namespace_with_scope_range(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            source_path = project_root / "sample.cpp"
            source_path.write_text(
                "\n".join(
                    [
                        "namespace App {",
                        "namespace Theme { void Draw(); }",
                        "using namespace Theme;",
                        "void Paint()",
                        "{",
                        "}",
                        "}",
                        "void Outside()",
                        "{",
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            file_index = build_file_index(path=source_path, project_root=project_root)

        directives = file_index["usingDirectives"]
        self.assertEqual(len(directives), 1)
        self.assertEqual(directives[0]["namespace"], "App::Theme")
        self.assertEqual(directives[0]["scope"], "App")
        self.assertEqual(directives[0]["activeFromLine"], 4)
        self.assertEqual(directives[0]["activeToLine"], 7)

    def test_visibility_context_includes_same_file_class_module_and_member_data(self) -> None:
        index = FakeVisibilityIndex()
        source = FunctionSourceSlice(
            symbol_id="fn-paint",
            function_name="Paint",
            qualified_name="App::Painter::Paint",
            symbol_type="method",
            file_id="file-1",
            relative_path="paint.cpp",
            start_line=10,
            end_line=15,
            base_line=10,
            base_byte=100,
            text="void Painter::Paint() {}",
            function_body_fingerprint="sha256:source",
        )
        ast_extract = FunctionAstExtract(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            parser_id="fixture",
            parser_version="v1",
            extractor_version="v1",
            local_declarations=(
                {
                    "name": "opacity",
                    "typeText": "auto",
                    "line": 12,
                },
            ),
        )

        context = build_function_visibility_context(index=index, source=source, ast_extract=ast_extract)

        self.assertEqual(context.file_id, "file-1")
        self.assertEqual(context.file_path, "paint.cpp")
        self.assertEqual(context.function_symbol_id, "fn-paint")
        self.assertEqual(context.current_namespace, ("App",))
        self.assertEqual(context.current_class_symbol_id, "cls-1")
        self.assertEqual(context.current_class_name, "App::Painter")
        self.assertEqual(context.imported_modules, ("App.Core",))

        same_file_ids = {item["symbolId"] for item in context.same_file_symbols}
        self.assertIn("fn-helper", same_file_ids)
        self.assertIn("cls-1", same_file_ids)

        visible_ids = {item["symbolId"] for item in context.visible_exported_symbols}
        self.assertIn("fn-exported", visible_ids)

        member_data_ids = {item["dataId"] for item in context.member_data}
        self.assertEqual(member_data_ids, {"data-overlay"})

        local_names = {item["name"] for item in context.local_declarations}
        self.assertEqual(local_names, {"opacity"})
        self.assertEqual(context.using_declarations[0]["target"], "App::Theme::Draw")
        self.assertEqual(context.using_directives[0]["namespace"], "App::Theme")
        self.assertEqual(context.namespace_aliases[0]["alias"], "Theme")

    def test_visibility_context_loads_using_scope_items_from_file_index(self) -> None:
        index = FakeVisibilityIndex()
        index.using_declarations = []
        index.using_directives = []
        index.namespace_aliases = []

        def load_file_index(file_id: str) -> dict:
            self.assertEqual(file_id, "file-1")
            return {
                "usingDeclarations": [
                    {
                        "name": "Draw",
                        "target": "App::Theme::Draw",
                        "startLine": 2,
                        "endLine": 2,
                        "activeFromLine": 3,
                        "activeToLine": 50,
                    },
                ],
                "usingDirectives": [
                    {
                        "namespace": "App::Theme",
                        "startLine": 3,
                        "endLine": 3,
                        "activeFromLine": 4,
                        "activeToLine": 50,
                    },
                ],
                "namespaceAliases": [
                    {
                        "alias": "Theme",
                        "target": "App::Theme",
                        "startLine": 4,
                        "endLine": 4,
                        "activeFromLine": 5,
                        "activeToLine": 50,
                    },
                ],
            }

        index.load_file_index = load_file_index
        source = FunctionSourceSlice(
            symbol_id="fn-paint",
            function_name="Paint",
            qualified_name="App::Painter::Paint",
            symbol_type="method",
            file_id="file-1",
            relative_path="paint.cpp",
            start_line=10,
            end_line=15,
            base_line=10,
            base_byte=100,
            text="void Painter::Paint() {}",
            function_body_fingerprint="sha256:source",
        )

        context = build_function_visibility_context(index=index, source=source)

        self.assertEqual(context.using_declarations[0]["fileId"], "file-1")
        self.assertEqual(context.using_declarations[0]["target"], "App::Theme::Draw")
        self.assertEqual(context.using_directives[0]["namespace"], "App::Theme")
        self.assertEqual(context.namespace_aliases[0]["target"], "App::Theme")


if __name__ == "__main__":
    unittest.main()
