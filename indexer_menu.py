from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Callable


DEFAULT_INDEX_DIR_NAME = ".mcp-cpp-project-indexer"


class MenuContext:
    def __init__(self, *, root: Path, index_root: Path, indexer_root: Path, jobs: int) -> None:
        self.root = root.resolve()
        self.index_root = index_root.resolve()
        self.indexer_root = indexer_root.resolve()
        self.jobs = jobs
        self.python = Path(sys.executable)

    def script(self, name: str) -> Path:
        return self.indexer_root / name


def quote_arg(value: Path | str) -> str:
    text = str(value)

    if " " in text or "\t" in text:
        return f'"{text}"'

    return text


def print_command(args: list[str]) -> None:
    print()
    print("Command:")
    print("  " + " ".join(quote_arg(arg) for arg in args))
    print()


def run_command(args: list[str], *, cwd: Path | None = None) -> int:
    print_command(args)

    try:
        completed = subprocess.run(args, cwd=str(cwd) if cwd else None, check=False)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 127

    return completed.returncode


def require_file(path: Path) -> bool:
    if path.exists():
        return True

    print(f"ERROR: Required file not found: {path}")
    return False


def build_project_index(ctx: MenuContext) -> int:
    script = ctx.script("build_project_index.py")

    if not require_file(script):
        return 2

    return run_command(
        [
            str(ctx.python),
            str(script),
            "--root",
            str(ctx.root),
            "--output-root",
            str(ctx.index_root),
            "--jobs",
            str(ctx.jobs),
        ]
    )


def build_module_map(ctx: MenuContext) -> int:
    script = ctx.script("build_module_map.py")

    if not require_file(script):
        return 2

    return run_command(
        [
            str(ctx.python),
            str(script),
            "--index-root",
            str(ctx.index_root),
        ]
    )


def build_all(ctx: MenuContext) -> int:
    result = build_project_index(ctx)

    if result != 0:
        return result

    return build_module_map(ctx)


def dump_module_tree(ctx: MenuContext) -> int:
    script = ctx.script("dump_module_tree.py")
    output = ctx.index_root / "module-tree.txt"

    if not require_file(script):
        return 2

    return run_command(
        [
            str(ctx.python),
            str(script),
            "--index-root",
            str(ctx.index_root),
            "--output",
            str(output),
        ]
    )


def dump_import_tree(ctx: MenuContext) -> int:
    script = ctx.script("dump_module_tree.py")

    if not require_file(script):
        return 2

    module_name = input("Module name, e.g. Example.Module:Partition: ").strip()

    if not module_name:
        print("No module name entered.")
        return 1

    max_depth_text = input("Max depth [5]: ").strip()
    max_depth = max_depth_text if max_depth_text else "5"

    safe_name = module_name.replace(":", "__").replace(".", "_").replace("/", "_").replace("\\", "_")
    output = ctx.index_root / f"imports-{safe_name}.txt"

    return run_command(
        [
            str(ctx.python),
            str(script),
            "--index-root",
            str(ctx.index_root),
            "--imports",
            module_name,
            "--max-depth",
            max_depth,
            "--output",
            str(output),
        ]
    )


def show_diagnostics(ctx: MenuContext) -> int:
    diagnostics_path = ctx.index_root / "diagnostics.json"

    if not diagnostics_path.exists():
        print(f"Diagnostics file not found: {diagnostics_path}")
        print("Build the project index first.")
        return 1

    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    counter = Counter(item.get("code") for item in diagnostics)

    print()
    print("Diagnostics:", len(diagnostics))
    print("By code:")

    for code, count in sorted(counter.items(), key=lambda item: str(item[0])):
        print(f"  {code}: {count}")

    print()
    print("Entries:")

    for item in diagnostics:
        print(
            f"  {item.get('relativePath')} "
            f"{item.get('code')} "
            f"{item.get('message')} "
            f"{item.get('range')}"
        )

    return 0


def show_project_summary(ctx: MenuContext) -> int:
    manifest_path = ctx.index_root / "manifest.json"
    module_map_path = ctx.index_root / "module_map.json"

    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}")
        print("Build the project index first.")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    print()
    print("Project index")
    print("=============")
    print("Root:      ", manifest.get("root"))
    print("Index root:", ctx.index_root.as_posix())
    print("Schema:    ", manifest.get("schema"))

    counts = manifest.get("counts", {})

    for key in ["files", "symbols", "names", "modules", "diagnostics"]:
        print(f"{key.capitalize():12}: {counts.get(key, 0)}")

    if module_map_path.exists():
        module_map = json.loads(module_map_path.read_text(encoding="utf-8"))
        module_counts = module_map.get("counts", {})
        print()
        print("Module map")
        print("==========")
        print("Schema:             ", module_map.get("schema"))
        print("Modules:            ", module_counts.get("modules", 0))
        print("Unresolved imports: ", module_counts.get("unresolvedImports", 0))
    else:
        print()
        print("Module map: not built")

    return 0


def build_single_file(ctx: MenuContext) -> int:
    script = ctx.script("build_file_index.py")

    if not require_file(script):
        return 2

    file_text = input("File to index: ").strip().strip('"')

    if not file_text:
        print("No file entered.")
        return 1

    file_path = Path(file_text)

    if not file_path.is_absolute():
        file_path = ctx.root / file_path

    if not file_path.exists():
        print(f"File not found: {file_path}")
        return 1

    emit_debug = input("Emit debug data? [y/N]: ").strip().casefold() == "y"
    safe_name = file_path.name.replace(":", "_").replace("/", "_").replace("\\", "_")
    output = ctx.index_root / "debug" / f"{safe_name}.json"

    args = [
        str(ctx.python),
        str(script),
        "--file",
        str(file_path),
        "--project-root",
        str(ctx.root),
        "--output",
        str(output),
    ]

    if emit_debug:
        args.append("--emit-debug")

    return run_command(args)


def update_project_index(ctx: MenuContext) -> int:
    script = ctx.script("update_project_index.py")

    if not require_file(script):
        return 2

    return run_command(
        [
            str(ctx.python),
            str(script),
            "--root",
            str(ctx.root),
            "--index-root",
            str(ctx.index_root),
            "--jobs",
            str(ctx.jobs),
        ]
    )


def update_project_index_dry_run(ctx: MenuContext) -> int:
    script = ctx.script("update_project_index.py")

    if not require_file(script):
        return 2

    return run_command(
        [
            str(ctx.python),
            str(script),
            "--root",
            str(ctx.root),
            "--index-root",
            str(ctx.index_root),
            "--dry-run",
        ]
    )


def update_all(ctx: MenuContext) -> int:
    result = update_project_index(ctx)

    if result != 0:
        return result

    return build_module_map(ctx)

def run_mcp_server(ctx: MenuContext) -> int:
    script = ctx.script("code_index_mcp_server.py")

    if not require_file(script):
        return 2

    print("Starting MCP server. Stop with Ctrl+C.")
    return run_command(
        [
            str(ctx.python),
            str(script),
            "--project-root",
            str(ctx.root),
            "--index-root",
            str(ctx.index_root),
        ]
    )


def print_lmstudio_config(ctx: MenuContext) -> int:
    config = {
        "mcpServers": {
            "mcp-cpp-project-indexer": {
                "command": str(ctx.python),
                "args": [
                    str(ctx.script("code_index_mcp_server.py")),
                    "--project-root",
                    str(ctx.root),
                    "--index-root",
                    str(ctx.index_root),
                ],
            }
        }
    }

    print(json.dumps(config, indent=2, ensure_ascii=False))
    return 0


def clean_index(ctx: MenuContext) -> int:
    if not ctx.index_root.exists():
        print(f"Index root does not exist: {ctx.index_root}")
        return 0

    print(f"This will delete: {ctx.index_root}")
    answer = input("Continue? Type YES: ").strip()

    if answer != "YES":
        print("Cancelled.")
        return 1

    import shutil

    shutil.rmtree(ctx.index_root)
    print("Deleted.")
    return 0


def menu_items() -> list[tuple[str, Callable[[MenuContext], int]]]:
    return [
        ("Build project index", build_project_index),
        ("Build module map", build_module_map),
        ("Build project index + module map", build_all),
        ("Dry-run update project index", update_project_index_dry_run),
        ("Update project index", update_project_index),
        ("Update project index + module map", update_all),        
        ("Show project summary", show_project_summary),
        ("Show diagnostics", show_diagnostics),
        ("Dump module tree to module-tree.txt", dump_module_tree),
        ("Dump import tree for one module", dump_import_tree),
        ("Build single file index", build_single_file),
        ("Print LM Studio mcp.json config", print_lmstudio_config),
        ("Run MCP server", run_mcp_server),
        ("Clean index directory", clean_index),
    ]


def interactive_menu(ctx: MenuContext) -> int:
    items = menu_items()

    while True:
        print()
        print("mcp-cpp-project-indexer")
        print("=======================")
        print("Project root:", ctx.root)
        print("Index root:  ", ctx.index_root)
        print("Indexer root:", ctx.indexer_root)
        print("Jobs:        ", ctx.jobs)
        print()

        for index, (title, _) in enumerate(items, start=1):
            print(f"{index:2}. {title}")

        print(" 0. Exit")
        print()

        choice_text = input("Select: ").strip()

        if choice_text in {"", "0", "q", "quit", "exit"}:
            return 0

        try:
            choice = int(choice_text)
        except ValueError:
            print("Invalid choice.")
            continue

        if not 1 <= choice <= len(items):
            print("Invalid choice.")
            continue

        _, handler = items[choice - 1]
        result = handler(ctx)

        if result != 0:
            print(f"Command returned exit code {result}.")

        input("Press Enter to continue...")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive menu for mcp-cpp-project-indexer build/server commands."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="C++ project root. Defaults to current working directory.",
    )
    parser.add_argument(
        "--index-root",
        type=Path,
        default=None,
        help="Index root. Defaults to <root>/.mcp-cpp-project-indexer.",
    )
    parser.add_argument(
        "--indexer-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory containing the mcp-cpp-project-indexer scripts.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Worker process count for build/update actions. Use 0 for auto.",
    )
    parser.add_argument(
        "--action",
        choices=[
            "build-index",
            "build-module-map",
            "build-all",
            "update-dry-run",
            "update-index",
            "update-all",
            "summary",
            "diagnostics",
            "dump-module-tree",
            "lmstudio-config",
            "server",
        ],
        default=None,
        help="Run one action non-interactively instead of showing the menu.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    index_root = (args.index_root or (root / DEFAULT_INDEX_DIR_NAME)).resolve()
    indexer_root = args.indexer_root.resolve()

    if not root.exists():
        print(f"Project root not found: {root}")
        return 1

    ctx = MenuContext(
        root=root,
        index_root=index_root,
        indexer_root=indexer_root,
        jobs=args.jobs,
    )

    action_map: dict[str, Callable[[MenuContext], int]] = {
        "build-index": build_project_index,
        "build-module-map": build_module_map,
        "build-all": build_all,
        "update-dry-run": update_project_index_dry_run,
        "update-index": update_project_index,
        "update-all": update_all,
        "summary": show_project_summary,
        "diagnostics": show_diagnostics,
        "dump-module-tree": dump_module_tree,
        "lmstudio-config": print_lmstudio_config,
        "server": run_mcp_server,
    }

    if args.action:
        return action_map[args.action](ctx)

    return interactive_menu(ctx)


if __name__ == "__main__":
    raise SystemExit(main())
