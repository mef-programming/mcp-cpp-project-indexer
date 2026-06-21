# mcp-cpp-project-indexer

`mcp-cpp-project-indexer` is a deterministic C++ source-range indexer for MCP-based AI code navigation.

It is built for AI agents that should **not** read entire C++ files blindly. The indexer gives the agent compact routing metadata first, then lets it read the exact original source range needed for the task.

```text
Find code. Read code. Do not guess code.
```

Full reference documentation is still available in [readme.md](readme.md). This page is the short landing page for new users and evaluators.

---

## Why AI agents use it

Large C++ projects are expensive and noisy when an AI agent starts by reading whole files. This project exposes a deterministic routing layer through MCP so an agent can ask:

- where is this function, class, method, or data member?
- which exact source range should be read?
- which C++20 module imports or exports this partition?
- which changed hunk intersects which indexed symbol or data range?

The result is token-efficient C++ code navigation with stronger evidence discipline:

```text
Metadata may locate code.
Only source reads may justify behavior claims.
```

That makes it useful for:

- source-grounded AI code analysis
- C++20 module and partition discovery
- changed-code review without blind full-file reads
- large native/legacy C++ codebases where exact source ranges matter
- MCP clients and agent runtimes that need compact code-navigation tools

---

## Minimal workflow

```text
User asks about Widget::OnScroll
-> find_symbol("Widget::OnScroll")
-> read_symbol(symbolId)
-> AI explains only what was visible in that source range
```

For changed-code review:

```text
list_changed_files
get_file_change_hunks(includeIndexedRangeSummary:true, includeSource:false)
get_file_change_hunks(symbolId/dataId, includeSource:true)
read_symbol/read_range only when current source behavior is needed
```

The indexer is only the table of contents. The AI still has to read source ranges and reason from the original code.

---

## 5-minute quick start

### 1. Clone this repository

```powershell
git clone https://github.com/mef-programming/mcp-cpp-project-indexer.git
cd mcp-cpp-project-indexer
```

### 2. Build an index for your C++ project

```powershell
python <indexer-root>\build_project_index.py `
  --root <project-root> `
  --output-root <project-root>\.mcp-cpp-project-indexer
```

### 3. Start the MCP server

```powershell
python <indexer-root>\code_index_mcp_server.py `
  --project-root <project-root> `
  --index-root <project-root>\.mcp-cpp-project-indexer
```

For multiple MCP clients or a long-running shared process, use HTTP transport:

```powershell
python <indexer-root>\code_index_mcp_server.py `
  --project-root <project-root> `
  --index-root <project-root>\.mcp-cpp-project-indexer `
  --transport http `
  --http-host 127.0.0.1 `
  --http-port 8765
```

### 4. Add the server to your MCP client

Minimal LM Studio-style config:

```json
{
  "mcpServers": {
    "mcp-cpp-project-indexer": {
      "command": "python",
      "args": [
        "<indexer-root>\\code_index_mcp_server.py",
        "--project-root",
        "<project-root>",
        "--index-root",
        "<project-root>\\.mcp-cpp-project-indexer"
      ]
    }
  }
}
```

### 5. Ask for exact source, not whole files

Good first request:

```text
Find the symbol Widget::OnScroll, read its implementation, and explain only what is visible in the source range.
```

Expected tool path:

```text
find_symbol -> read_symbol -> source-grounded answer
```

For best results, give your AI the rules from [prompt_template.md](prompt_template.md).

---

## Scale and token reduction

The full documentation includes current scale runs for a large C++20 commercial codebase and a Chromium checkout. The indexer stores global symbol/data routing in SQLite so the MCP server can start without loading millions of entries into Python objects.

In one measured workflow, exact source-range routing reduced source text read from roughly 2,000 lines to 283 lines, an **86% reduction**. See [Production Scale & Performance](readme.md#-production-scale--performance) for details.

---

## Feedback from real C++ projects wanted

If you cloned this repository and tried it on a real C++ project, feedback is welcome even if it is not a formal bug report.

Please [open an issue](https://github.com/mef-programming/mcp-cpp-project-indexer/issues/new/choose) for:

- MCP client compatibility reports
- C++ project compatibility results
- token-reduction or source-read reduction examples
- confusing setup steps or missing documentation
- parser/indexer misses on real C++ code
- feature requests for AI-agent workflows

Useful details to include:

```text
MCP client:
Transport: stdio / HTTP
Platform: Windows / Linux / macOS
Project shape: CMake / MSBuild / custom / mixed
C++ style: headers / C++20 modules / generated files / SDK-heavy
Approximate scale: files, lines, modules, symbols if known
What worked:
What failed or felt unclear:
```

Early compatibility feedback is useful even when nothing crashed.

---

## What it is not

This project intentionally does **not** pretend to be a compiler or semantic analyzer.

It is not:

- a compiler-accurate whole-program call graph
- an LSP replacement
- a refactoring engine
- a template-instantiation resolver
- a macro-expansion engine
- a bug-analysis model

It returns routing facts and exact source ranges. Behavior claims must come from source that was explicitly read.

---

## More documentation

- [Full documentation](readme.md)
- [Recommended AI usage rules](prompt_template.md)
- [Work items and implementation notes](docs/work/README.md)
- [MEF Programming homepage](https://www.mef-programming.eu/)
