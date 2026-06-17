from __future__ import annotations

import sys
import unittest

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEXER_SRC = REPO_ROOT / "src" / "indexer"
if str(INDEXER_SRC) not in sys.path:
    sys.path.insert(0, str(INDEXER_SRC))

from cpp_function_graph_extract import LightweightFunctionBodyParser, extract_raw_function_ast
from cpp_function_graph_tree_sitter import TreeSitterCppFunctionBodyParser, TreeSitterUnavailableError


class FunctionGraphRawExtractionTests(unittest.TestCase):
    def test_extracts_calls_member_access_locals_and_control_flow(self) -> None:
        function_text = "\n".join(
            [
                "void Paint()",
                "{",
                "    auto opacity = _CalculatePulseOpacity();",
                "    NS::Draw(opacity, _OverlayPosition);",
                "    this->_State.Reset();",
                "    _OverlayPosition = opacity;",
                "    if (_OverlayPosition > 0) {",
                "        return;",
                "    }",
                "}",
            ]
        )

        extract = extract_raw_function_ast(
            symbol_id="fn-paint",
            source_fingerprint="sha256:source",
            function_text=function_text,
            base_line=40,
            base_byte=120,
        )

        calls = {item["callee"]: item for item in extract.calls}
        self.assertEqual(calls["_CalculatePulseOpacity"]["callKind"], "unqualified")
        self.assertEqual(calls["NS::Draw"]["callKind"], "qualified")
        self.assertEqual(calls["NS::Draw"]["argumentCount"], 2)
        self.assertEqual(calls["this->_State.Reset"]["callKind"], "member")

        writes = [
            item
            for item in extract.member_accesses
            if item["text"] == "_OverlayPosition" and item["accessKind"] == "write_candidate"
        ]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["line"], 45)

        locals_by_name = {item["name"]: item for item in extract.local_declarations}
        self.assertEqual(locals_by_name["opacity"]["typeText"], "auto")
        self.assertEqual(locals_by_name["opacity"]["line"], 42)

        markers = [(item["marker"], item["line"]) for item in extract.control_flow]
        self.assertIn(("if", 46), markers)
        self.assertIn(("return", 47), markers)

    def test_parser_adapter_delegates_to_raw_extractor(self) -> None:
        parser = LightweightFunctionBodyParser()

        extract = parser.parse_function(
            symbol_id="fn",
            source_fingerprint="sha256:source",
            function_text="void f()\n{\n    foo();\n}\n",
            base_line=10,
            base_byte=200,
        )

        self.assertEqual(extract.parser_id, parser.parser_id)
        self.assertEqual(extract.parser_version, parser.parser_version)
        self.assertEqual(extract.calls[0]["callee"], "foo")
        self.assertEqual(extract.calls[0]["line"], 12)

    def test_tree_sitter_adapter_is_isolated_until_dependency_exists(self) -> None:
        with self.assertRaises(TreeSitterUnavailableError):
            TreeSitterCppFunctionBodyParser()


if __name__ == "__main__":
    unittest.main()
