# mcp-cpp-project-indexer

MCP C++ project indexer for fast symbol, module, and source-range navigation in large C++20 codebases.

`vs-project-indexer-v2` ist ein parser-basierter C++ Code-Routing-Index für große Projekte.

Das Ziel ist nicht, C++ vollständig zu verstehen oder einen Compiler/LSP zu ersetzen. Das Ziel ist:

```text
Symbol / Modul / Datei finden -> exakte Source-Range lesen -> AI analysiert on demand
```

Der Indexer ist ein deterministischer Locator:

```text
Find code. Read code. Do not guess code.
```

Er baut keine Call-Graphs, keine Cross-References, keine Type-Resolution, keine Template-Instantiations und keine semantischen Analysen. Die AI liest die benötigten Originalzeilen und entscheidet selbst, welche weiteren Symbole sie rekursiv nachlädt.

---

## License

This project is licensed under the Apache License 2.0.

## 1. Projektidee

Große C++ Codebases sind für AI teuer, wenn ganze Dateien gelesen werden müssen.

Beispiel:

```text
Renderer.cpp komplett lesen: ~2000 Zeilen
On-demand über Symbolranges: ~283 Zeilen
Ersparnis: ca. 86% weniger Source-Text für diesen Analysepfad
```

Der Indexer reduziert Tokenverbrauch, Latenz und Kontextverschmutzung, indem er nur als Inhaltsverzeichnis dient:

```text
find_symbol("Editor::_OnScroll")
  -> symbolId, fileId, startLine, endLine

read_symbol(symbolId)
  -> Originalcode mit Zeilennummern
```

Danach analysiert die AI den gelesenen Code selbst. Wenn der Code eine weitere Projektfunktion aufruft, fragt die AI gezielt danach.

---

## 2. Architektur

```text
C++ Project
  -> build_file_index.py       # eine Datei -> cpp.file_index.v1
  -> build_project_index.py    # ganzes Projekt -> manifest/files/symbols/names/modules
  -> build_module_map.py       # Modul-Metadaten -> module_map.json
  -> code_index_mcp_server.py  # LM Studio / MCP Tool-Server
  -> AI liest nur benötigte Ranges
```

Output-Struktur eines Project-Index:

```text
<index-root>/
  manifest.json
  files/
    f_<pathHash>.json
  symbols.jsonl
  names.json
  modules.json
  diagnostics.json
  module_map.json              # optional, via build_module_map.py
```

---

## 3. Was der Indexer macht

Der Scanner erkennt best-effort:

* C++20 Module und Partitionen
* imports / exports
* namespaces
* classes / structs / enums
* functions / methods
* constructors / destructors / operators
* declarations und inline definitions
* exact startLine/endLine
* module fragments
* diagnostics für strukturell auffällige Dateien

Er arbeitet stream-/token-basiert, nicht regex-basiert.

Wichtige Regeln:

* Kommentare werden line-preserving geblankt.
* Preprocessor-/Macro-Bodies werden nicht als C++ Struktur tokenisiert.
* `#if 0`-Blöcke werden als inaktiv behandelt; `#else` wird wieder sichtbar.
* Unbekannte `#if/#ifdef/#ifndef`-Branches werden für Struktur-Scanning sichtbar gelassen, damit keine echten Braces verloren gehen.
* Raw strings inklusive `R`, `LR`, `u8R`, `uR`, `UR` werden übersprungen.
* Makros werden nicht expandiert.
* Field/member data wird nicht als Symbol für Routing behandelt.
* Function bodies werden nicht analysiert.

---

## 4. Was der Indexer nicht macht

Absichtlich nicht enthalten:

* kein Call-Graph
* kein `find_references`
* keine Type-Resolution
* keine Template-Instantiation
* keine Overload-Auflösung durch Compilersemantik
* keine Makro-Expansion
* keine Bug-Analyse
* keine Code-Zusammenfassung
* kein `analyze_symbol(symbolId)`

Die AI muss rekursiv arbeiten:

```text
1. read_symbol(X)
2. sichtbare Calls prüfen
3. externe APIs/Makros ignorieren, falls nicht relevant
4. Projekt-Symbole gezielt find_symbol/read_symbol
5. wiederholen, bis genug Kontext gelesen wurde
```

---

## 5. Einzelne Datei indexieren

Beispiel:

```powershell
python build_file_index.py `
  --file F:\Projects\smartftp-uiitemsview\SmartFTP\TextEditor\View\Controls\Editor.ixx `
  --project-root F:\Projects\smartftp-uiitemsview `
  --output C:\KI\vs-project-indexer-v2\eval\debug_Editor.json
```

Mit Debug-Daten:

```powershell
python build_file_index.py `
  --file F:\Projects\smartftp-uiitemsview\SmartFTP\Shell\Browser\Impl.ixx `
  --project-root F:\Projects\smartftp-uiitemsview `
  --output C:\KI\vs-project-indexer-v2\eval\debug_Impl.json `
  --emit-debug
```

Debug-Felder werden nur mit `--emit-debug` geschrieben:

```text
scopeIntervals
structuralEvents
functionBodyRanges
```

Ohne Debug bleibt das File-JSON klein und runtime-orientiert.

---

## 6. Ganzes Projekt indexieren

Beispiel:

```powershell
python build_project_index.py `
  --root F:\Projects\smartftp-uiitemsview `
  --output-root C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview
```

Der Build zeigt einen kleinen Progress-Spinner auf `stderr`.

Beispiel-Ergebnis:

```text
Built cpp.project_index.v1
Root: F:/Projects/smartftp-uiitemsview
Output: C:/KI/vs-project-indexer-v2/eval/smartftp-uiitemsview
Files: 7076
Symbols: 97583
Names: 95674
Modules: 3774
Diagnostics: 7
Manifest: C:/KI/vs-project-indexer-v2/eval/smartftp-uiitemsview/manifest.json
Symbols JSONL: C:/KI/vs-project-indexer-v2/eval/smartftp-uiitemsview/symbols.jsonl
Names JSON: C:/KI/vs-project-indexer-v2/eval/smartftp-uiitemsview/names.json
Modules JSON: C:/KI/vs-project-indexer-v2/eval/smartftp-uiitemsview/modules.json
Diagnostics JSON: C:/KI/vs-project-indexer-v2/eval/smartftp-uiitemsview/diagnostics.json
```

Diagnostics sind non-fatal. Sie bedeuten, dass einzelne Dateien strukturell auffällig sind, der Index aber best-effort weitergebaut wurde.

Diagnostics prüfen:

```powershell
python -c "import json; from collections import Counter; d=json.load(open(r'C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview\diagnostics.json',encoding='utf-8')); print(len(d)); print(Counter(x.get('code') for x in d)); [print(x['relativePath'], x['code'], x['message'], x.get('range')) for x in d]"
```

---

## 7. Module Map bauen

Nach dem Project-Index kann eine zusätzliche Modul-Map erzeugt werden:

```powershell
python build_module_map.py `
  --index-root C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview
```

Output:

```text
C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview\module_map.json
```

Die Modul-Map enthält:

* alle C++20 Module
* primary module name
* partition name
* zugehörige Dateien
* direkte imports
* importedBy
* module tree
* unresolved imports

Auch hier gilt: Metadaten, keine Analyse.

---

## 8. Module Tree dumpen

Text-Dump:

```powershell
python dump_module_tree.py `
  --index-root C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview
```

In Datei schreiben:

```powershell
python dump_module_tree.py `
  --index-root C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview `
  --output C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview\module-tree.txt
```

Import-Tree eines Moduls:

```powershell
python dump_module_tree.py `
  --index-root C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview `
  --imports SmartFTP.Shell.Browser:Impl `
  --max-depth 5
```

---

## 9. MCP Server starten

Der MCP-Server liest den fertigen Index. Er baut den Index nicht selbst neu.

```powershell
python code_index_mcp_server.py `
  --project-root F:\Projects\smartftp-uiitemsview `
  --index-root C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview
```

Der Server kommuniziert über MCP/JSON-RPC stdio.

Er ist read-only und stellt nur Locator-/Read-Tools bereit.

---

## 10. LM Studio Integration

Beispiel `mcp.json`:

```json
{
  "mcpServers": {
    "vs-project-indexer": {
      "command": "python",
      "args": [
        "C:\\KI\\vs-project-indexer-v2\\code_index_mcp_server.py",
        "--project-root",
        "F:\\Projects\\smartftp-uiitemsview",
        "--index-root",
        "C:\\KI\\vs-project-indexer-v2\\eval\\smartftp-uiitemsview"
      ]
    }
  }
}
```

Nach Änderungen am Index:

```text
1. build_project_index.py ausführen
2. build_module_map.py ausführen
3. MCP Server / LM Studio neu starten
```

---

## 11. MCP Tools

### Project / Summary

```text
get_project_summary
```

Gibt Counts und Index-Pfade zurück.

### Symbol Tools

```text
find_symbol(query)
find_declaration(query)
find_symbols_glob(pattern)
read_symbol(symbolId)
read_range(file, startLine, endLine)
list_file_symbols(file)
```

`find_symbol` ist metadata-only. Es sucht:

* exact shortName
* exact qualifiedName / search alias
* fallback substring über shortName, qualifiedName, signature

Es liest keinen Source-Code.

Canonical argument:

```json
{ "query": "Editor::_OnScroll" }
```

`name` kann optional als Legacy-Alias akzeptiert werden, aber `query` ist der Standard.

### File Tools

```text
find_files(pattern)
```

Glob über project-relative paths. Kein Source-Grep.

Beispiele:

```text
*Editor*
*/TextEditor/*.ixx
*/Shell/Browser/*
```

### Module Tools

```text
find_module(moduleName)
list_module_files(moduleName)
search_modules(pattern)
get_module_map_summary
get_module_info(moduleName)
list_module_imports(moduleName)
list_module_imported_by(moduleName)
get_module_tree(maxDepth)
```

Module-Tools erwarten C++20 Modulsyntax:

```text
SmartFTP.TextEditor:View.Controls.Editor
SmartFTP.Shell.Browser:Impl
uiframework.Elements:ElementImpl
```

Nicht verwenden mit C++ Namespace-Syntax:

```text
SmartFTP::TextEditor::View::Controls
UIFramework::Elements
```

Für Namespaces/Klassen/Funktionen:

```text
find_symbol
find_symbols_glob
```

---

## 12. Empfohlener AI-Systemprompt

Der Modell-Systemprompt sollte klarstellen:

```text
Use vs-project-indexer as a deterministic source-range locator.
It does not analyze code, build call graphs, expand macros, resolve types,
instantiate templates, or perform refactoring.

Use query as the canonical argument for symbol lookup tools.

Correct:
  find_symbol({"query": "Editor::_OnScroll"})
  find_declaration({"query": "OnNotifyReflect"})

Avoid:
  find_symbol({"name": "Editor::_OnScroll"})

Read source before making implementation claims.
Use read_symbol or read_range for exact original lines.
```

Full system prompt is maintained separately as:

```text
code_index_tool_system_prompt
```

---

## 13. Example Workflows

### Find declaration and definition

User:

```text
Finde die Deklaration und Definition von OnNotifyReflect.
```

AI workflow:

```text
find_symbol({"query": "OnNotifyReflect"})
read_symbol(symbolId for declaration)
read_symbol(symbolId for definition)
```

Result:

```text
Declaration:
SmartFTP/TextEditor/View/Controls/Editor.ixx:60

Definition:
SmartFTP/TextEditor/View/Controls/Editor.cpp:64-70
```

### Analyze a function on demand

User:

```text
Zeig mir Editor::_OnScroll.
```

AI workflow:

```text
find_symbol({"query": "Editor::_OnScroll"})
read_symbol(symbolId)
```

Then the AI sees project-local calls and follows only relevant ones:

```text
GetHWND -> find_symbol/read_symbol
SendMessageW -> Win32 API, not project code
MAKEWPARAM -> Win32 macro, not project code
```

### Module query

User:

```text
Welche Module importieren SmartFTP.Shell.Browser:Impl?
```

AI workflow:

```text
list_module_imported_by({"moduleName": "SmartFTP.Shell.Browser:Impl"})
```

---

## 14. Important Design Rules

### Exact line numbers are the heart of the system

Every useful symbol must route to exact source lines.

```text
symbolId -> fileId -> startLine/endLine -> original source
```

### Runtime index should stay small

The runtime index stores routing facts.

Debug data such as structural events and scope maps should only be emitted with `--emit-debug`.

### Keep search and browsing separate

Symbol search uses `names.json` / `symbols.jsonl`.

Module browsing uses `module_map.json`.

File browsing uses manifest/file metadata.

### Do not hide real parser problems

Diagnostics should stay visible. Do not exclude whole folders just to get zero diagnostics unless the source is intentionally outside the indexed project.

### Do not add semantic features just because the model asks

No call graph. No analyze symbol. No reference graph. No macro expansion.

The AI is responsible for recursive exploration.

---

## 15. Current Status Snapshot

Known working with a large C++20 project:

```text
Files:      ~7076
Symbols:    ~97583
Names:      ~95674
Modules:     ~3774
Diagnostics: low single digits after lexer fixes
Build time: ~1:30 min on current machine
```

Important lexer/scanner fixes already included:

* macro bodies skipped
* `#if 0 / #else / #endif` handled for structural scanning
* unknown `#if` branches kept visible
* raw strings with `R`, `LR`, `u8R`, `uR`, `UR` skipped
* access labels not included in method signatures
* constructor initializer lists handled
* `DECLSPEC_NOVTABLE`-style class-head macro before class name handled
* body statements not emitted as function declarations
* macro-like `Identifier(...)` statements without return type not emitted as functions

---

## 16. Typical Rebuild Sequence

```powershell
cd C:\KI\vs-project-indexer-v2

python build_project_index.py `
  --root F:\Projects\smartftp-uiitemsview `
  --output-root C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview

python build_module_map.py `
  --index-root C:\KI\vs-project-indexer-v2\eval\smartftp-uiitemsview
```

Then restart LM Studio / MCP server.

---

## 17. Minimal Smoke Tests

After server integration, ask the model:

```text
get_project_summary
```

```text
Zeig mir alle Module unter uiframework.Core.Direct2D.
```

```text
Welche Module importieren SmartFTP.Shell.Browser:Impl?
```

```text
Finde die Deklaration und Definition von OnNotifyReflect.
```

```text
Lies alle Overloads von GetAccessibleImpl.
```

```text
Welche Datei definiert uiframework.Elements:ElementImpl?
```

Expected behavior:

* model uses tools
* does not read whole files
* does not confuse namespaces with modules
* calls `read_symbol` only after symbol metadata lookup
* follows project calls recursively only when needed
