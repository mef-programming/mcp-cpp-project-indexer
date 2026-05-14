from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SCHEMA_NAME = "cpp.file_index.v1"
INDEXER_NAME = "vs-project-indexer"
INDEXER_VERSION = "0.1"
SCANNER_VERSION = "cpp-structural-scan.v1"


MODULE_UNIT_KINDS = {
    "non_module",
    "module_interface",
    "module_implementation",
    "module_partition_interface",
    "module_partition_implementation",
    "header_unit",
    "unknown_module_unit",
}

MODULE_FRAGMENT_KINDS = {
    "global_module_fragment",
    "module_purview",
    "private_module_fragment",
    "unknown",
}

IMPORT_KINDS = {
    "module_import",
    "module_partition_import",
    "header_unit_import",
    "std_import",
    "unknown_import",
}

EXPORT_KINDS = {
    "export_declaration",
    "export_namespace",
    "export_block",
    "export_import",
    "export_module_declaration",
    "unknown_export",
}

SYMBOL_TYPES = {
    "namespace",
    "class",
    "class_declaration",
    "struct",
    "struct_declaration",
    "enum",
    "function",
    "function_declaration",
    "method",
    "method_declaration",
    "constructor",
    "constructor_declaration",
    "destructor",
    "destructor_declaration",
    "operator",
    "operator_declaration",
}

TEMPLATE_TARGET_KINDS = {
    "class_template",
    "struct_template",
    "function_template",
    "method_template",
    "constructor_template",
    "destructor_template",
    "operator_template",
    "enum_template",
    "variable_template",
    "alias_template",
    "unknown_template",
}

TEMPLATE_SPECIALIZATION_KINDS = {
    "primary_template",
    "explicit_specialization",
    "partial_specialization",
    "explicit_instantiation",
    "unknown",
}

STRUCTURAL_EVENT_KINDS = {
    "global_module_fragment",
    "module_declaration",
    "private_module_fragment",
    "import_declaration",
    "export_declaration",
    "namespace",
    "class",
    "class_declaration",
    "struct",
    "struct_declaration",
    "enum",
    "function",
    "function_declaration",
    "method",
    "method_declaration",
    "constructor",
    "constructor_declaration",
    "destructor",
    "destructor_declaration",
    "operator",
    "operator_declaration",
    "template_declaration",
    "function_body",
    "unknown",
}

CONTROL_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "static_assert",
    "requires",
    "return",
    "co_return",
}

FUNCTION_SPECIFIERS = {
    "constexpr",
    "consteval",
    "constinit",
    "virtual",
    "explicit",
    "override",
    "final",
    "noexcept",
}

STORAGE_SPECIFIERS = {
    "static",
    "extern",
    "inline",
    "thread_local",
    "mutable",
    "friend",
}

DECLARATION_SKIP_PREFIXES = {
    "using",
    "friend",
    "typedef",
    "static_assert",
}


@dataclass(slots=True)
class Token:
    value: str
    kind: str
    line: int
    col0: int


@dataclass(slots=True)
class SourceRange:
    start_line: int
    end_line: int
    start_col0: int | None = None
    end_col0: int | None = None

    def to_json(self) -> dict[str, int]:
        result = {
            "startLine": self.start_line,
            "endLine": self.end_line,
        }

        if self.start_col0 is not None:
            result["startColumn"] = self.start_col0 + 1

        if self.end_col0 is not None:
            result["endColumn"] = self.end_col0 + 1

        return result


@dataclass(slots=True)
class TemplatePrefix:
    start_line: int
    end_line: int
    start_col0: int
    end_col0_exclusive: int
    raw_prefix: str
    parameters: list[str]
    requires_clause: str = ""

    def range_json(self) -> dict[str, int]:
        return SourceRange(
            start_line=self.start_line,
            end_line=self.end_line,
            start_col0=self.start_col0,
            end_col0=self.end_col0_exclusive - 1,
        ).to_json()


@dataclass(slots=True)
class TemplateAttachment:
    target_kind: str
    specialization_kind: str
    prefixes: list[TemplatePrefix]

    @property
    def start_line(self) -> int:
        return self.prefixes[0].start_line

    def to_json(self) -> dict[str, Any]:
        raw_prefix = " ".join(prefix.raw_prefix for prefix in self.prefixes).strip()
        requires_clause = " ".join(
            prefix.requires_clause
            for prefix in self.prefixes
            if prefix.requires_clause
        ).strip()

        return {
            "isTemplate": True,
            "targetKind": self.target_kind,
            "specializationKind": self.specialization_kind,
            "templateDepth": len(self.prefixes),
            "templateRanges": [prefix.range_json() for prefix in self.prefixes],
            "parameters": [
                parameter
                for prefix in self.prefixes
                for parameter in prefix.parameters
            ],
            "rawPrefix": raw_prefix,
            "requiresClause": requires_clause,
        }


@dataclass(slots=True)
class StructuralEvent:
    kind: str
    name: str
    qualified_name: str

    start_line: int
    end_line: int | None
    start_col0: int | None
    end_col0: int | None

    open_brace_line: int | None
    open_brace_col0: int | None
    close_line: int | None

    signature: str
    order: int

    inline: bool = False
    exported: bool = False
    fragment: str = "unknown"
    template: TemplateAttachment | None = None

    def effective_end_line(self) -> int:
        return self.end_line or self.close_line or self.start_line

    def range_json(self) -> dict[str, int]:
        return SourceRange(
            start_line=self.start_line,
            end_line=self.effective_end_line(),
            start_col0=self.start_col0,
            end_col0=self.end_col0,
        ).to_json()


@dataclass(slots=True)
class ScopeFrame:
    kind: str
    name: str
    qualified_name: str
    start_line: int
    inline: bool = False
    exported: bool = False


@dataclass(slots=True)
class BraceRecord:
    kind: str
    events: list[StructuralEvent]


@dataclass(slots=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    start_line: int | None = None
    end_line: int | None = None

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }

        if self.start_line is not None and self.end_line is not None:
            result["range"] = {
                "startLine": self.start_line,
                "endLine": self.end_line,
            }
        else:
            result["range"] = None

        return result
