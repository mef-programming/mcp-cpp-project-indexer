from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any


DEFAULT_INDEX_DIR_NAME = ".mcp-cpp-project-indexer"


def clear_screen() -> None:
    print("\x1b[2J\x1b[H", end="")


def supports_ansi() -> bool:
    return os.name != "nt" or "WT_SESSION" in os.environ or "ANSICON" in os.environ


def fmt_count(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_http_status(base_url: str, timeout: float = 0.5) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/status", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


def manifest_status(root: Path, index_root: Path) -> dict[str, Any]:
    manifest = load_json(index_root / "manifest.json") or {}
    module_map = load_json(index_root / "module_map.json") or {}
    counts = manifest.get("counts", {})
    stats = manifest.get("stats", {})
    module_counts = module_map.get("counts", {})

    return {
        "project": {
            "root": root.as_posix(),
            "indexRoot": index_root.as_posix(),
        },
        "index": {
            "schema": manifest.get("schema"),
            "counts": counts,
            "stats": stats,
            "manifestMtime": path_mtime(index_root / "manifest.json"),
            "moduleMapMtime": path_mtime(index_root / "module_map.json"),
        },
        "moduleMap": {
            "loaded": bool(module_map),
            "counts": module_counts,
        },
        "locks": {
            "updateLockFileExists": (index_root / ".update.lock").exists(),
            "watcherLockFileExists": (index_root / ".watcher.lock").exists(),
        },
        "watcher": {
            "configured": False,
            "running": False,
            "lockHeld": False,
        },
    }


def path_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


class CommandRunner:
    def __init__(self, max_lines: int = 18) -> None:
        self.lines: deque[str] = deque(maxlen=max_lines)
        self.process: subprocess.Popen[str] | None = None
        self.last_exit_code: int | None = None
        self.last_command = ""
        self._queue: queue.Queue[str] = queue.Queue()
        self._reader: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, args: list[str], *, cwd: Path | None = None) -> bool:
        if self.running:
            self.lines.append("A command is already running.")
            return False

        self.last_command = " ".join(args)
        self.last_exit_code = None
        self.lines.clear()
        self.lines.append("> " + self.last_command)

        self.process = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        return True

    def poll(self) -> None:
        while True:
            try:
                line = self._queue.get_nowait()
            except queue.Empty:
                break

            self.lines.append(line.rstrip())

        if self.process is not None and self.process.poll() is not None:
            self.last_exit_code = self.process.returncode
            self.lines.append(f"Process exited with code {self.last_exit_code}.")
            self.process = None

    def stop(self) -> None:
        if not self.running or self.process is None:
            self.lines.append("No running command to stop.")
            return

        self.process.terminate()
        self.lines.append("Terminate requested for running command.")

    def _read_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None

        for line in self.process.stdout:
            self._queue.put(line)


class ControlCenter:
    def __init__(
        self,
        *,
        root: Path,
        index_root: Path,
        indexer_root: Path,
        jobs: int,
        http_url: str,
        emit_diagnostic_file_indexes: bool,
    ) -> None:
        self.root = root.resolve()
        self.index_root = index_root.resolve()
        self.indexer_root = indexer_root.resolve()
        self.jobs = jobs
        self.http_url = http_url.rstrip("/")
        self.emit_diagnostic_file_indexes = emit_diagnostic_file_indexes
        self.python = Path(sys.executable)
        self.runner = CommandRunner()
        self.last_status: dict[str, Any] | None = None
        self.status_source = "disk"

    def run(self) -> int:
        if supports_ansi():
            clear_screen()

        while True:
            self.runner.poll()
            self.refresh_status()
            self.render()
            choice = input("Command [B/U/F/M/H/W/S/X/R/Q]: ").strip().casefold()

            if choice in {"q", "quit", "exit"}:
                return 0

            if choice == "b":
                self.start_build()
            elif choice == "u":
                self.start_update()
            elif choice == "f":
                self.start_fast_update()
            elif choice == "m":
                self.start_module_map()
            elif choice == "h":
                self.start_http_server_with_watcher()
            elif choice == "w":
                self.start_watcher()
            elif choice == "s":
                self.toggle_diagnostics()
            elif choice == "x":
                self.runner.stop()
            elif choice == "r":
                continue
            else:
                self.runner.lines.append("Unknown command.")

    def refresh_status(self) -> None:
        http_status = read_http_status(self.http_url)

        if http_status is not None:
            self.last_status = http_status
            self.status_source = "http"
            return

        self.last_status = manifest_status(self.root, self.index_root)
        self.status_source = "disk"

    def render(self) -> None:
        if supports_ansi():
            clear_screen()

        status = self.last_status or {}
        project = status.get("project", {})
        index = status.get("index", {})
        counts = index.get("counts", {})
        stats = index.get("stats", {})
        watcher = status.get("watcher", {})
        server = status.get("server", {})
        locks = status.get("locks", {})

        print("mcp-cpp-project-indexer control center")
        print("=====================================")
        print(f"Project: {project.get('root') or self.root.as_posix()}")
        print(f"Index:   {project.get('indexRoot') or self.index_root.as_posix()}")
        print(
            "Server:  "
            f"{server.get('transport', self.status_source)} "
            f"{self.http_url if self.status_source == 'http' else '(not connected)'}"
        )
        print(
            "Watcher: "
            f"{'running' if watcher.get('running') else 'stopped'} "
            f"lock={'held' if watcher.get('lockHeld') else 'not-held'} "
            f"last={watcher.get('lastUpdateResult') or '-'}"
        )
        print(
            "Locks:   "
            f"updateFile={bool(locks.get('updateLockFileExists'))} "
            f"watcherFile={bool(locks.get('watcherLockFileExists'))}"
        )
        print(
            "Index:   "
            f"files {fmt_count(counts.get('files'))} | "
            f"symbols {fmt_count(counts.get('symbols'))} | "
            f"data {fmt_count(counts.get('data'))} | "
            f"modules {fmt_count(counts.get('modules'))} | "
            f"diagnostics {fmt_count(counts.get('diagnostics'))}"
        )
        print(
            "Stats:   "
            f"code lines {fmt_count(stats.get('totalCodeLines'))} | "
            f"tokens {fmt_count(stats.get('totalTokens'))}"
        )
        print(
            "Mode:    "
            f"diagnostic file sections {'ON' if self.emit_diagnostic_file_indexes else 'OFF'} | "
            f"jobs {self.jobs}"
        )
        print()
        print("[B] Build  [U] Update  [F] Fast update  [M] Module map")
        print("[H] HTTP+watcher  [W] Watcher  [S] Toggle diagnostics  [X] Stop  [R] Refresh  [Q] Quit")
        print()
        print("Activity")
        print("--------")

        if self.runner.running:
            print("Running command...")
        elif self.runner.last_exit_code is not None:
            print(f"Last exit code: {self.runner.last_exit_code}")
        else:
            print("Idle.")

        for line in self.runner.lines:
            print(line[:160])

        print()

    def command_base(self, script_name: str) -> list[str]:
        return [str(self.python), str(self.indexer_root / script_name)]

    def append_diagnostics_flag(self, args: list[str]) -> None:
        if self.emit_diagnostic_file_indexes:
            args.append("--emit-diagnostic-file-indexes")

    def start_build(self) -> None:
        args = self.command_base("build_project_index.py") + [
            "--root",
            str(self.root),
            "--output-root",
            str(self.index_root),
            "--jobs",
            str(self.jobs),
        ]
        self.append_diagnostics_flag(args)
        self.runner.start(args)

    def start_update(self) -> None:
        args = self.command_base("update_project_index.py") + [
            "--root",
            str(self.root),
            "--index-root",
            str(self.index_root),
            "--jobs",
            str(self.jobs),
        ]
        self.append_diagnostics_flag(args)
        self.runner.start(args)

    def start_fast_update(self) -> None:
        args = self.command_base("update_project_index.py") + [
            "--root",
            str(self.root),
            "--index-root",
            str(self.index_root),
            "--jobs",
            str(self.jobs),
            "--known-files-only",
        ]
        self.append_diagnostics_flag(args)
        self.runner.start(args)

    def start_module_map(self) -> None:
        args = self.command_base("build_module_map.py") + [
            "--index-root",
            str(self.index_root),
        ]
        self.runner.start(args)

    def start_watcher(self) -> None:
        args = self.command_base("watch_project_index.py") + [
            "--root",
            str(self.root),
            "--index-root",
            str(self.index_root),
            "--indexer-root",
            str(self.indexer_root),
            "--jobs",
            str(self.jobs),
        ]
        self.append_diagnostics_flag(args)
        self.runner.start(args)

    def start_http_server_with_watcher(self) -> None:
        host, port = parse_http_url(self.http_url)
        args = self.command_base("code_index_mcp_server.py") + [
            "--project-root",
            str(self.root),
            "--index-root",
            str(self.index_root),
            "--transport",
            "http",
            "--http-host",
            host,
            "--http-port",
            str(port),
            "--watch-index",
            "--watch-jobs",
            str(self.jobs),
        ]

        if self.emit_diagnostic_file_indexes:
            args.append("--watch-emit-diagnostic-file-indexes")

        self.runner.start(args)

    def toggle_diagnostics(self) -> None:
        self.emit_diagnostic_file_indexes = not self.emit_diagnostic_file_indexes
        self.runner.lines.append(
            "Diagnostic file sections "
            + ("enabled." if self.emit_diagnostic_file_indexes else "disabled.")
        )


def parse_http_url(url: str) -> tuple[str, int]:
    text = url.replace("http://", "").replace("https://", "")
    host_port = text.split("/", 1)[0]

    if ":" not in host_port:
        return host_port, 80

    host, port_text = host_port.rsplit(":", 1)
    return host, int(port_text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Terminal control center for mcp-cpp-project-indexer."
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
        help="Directory containing indexer scripts.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Worker process count for build/update/watch actions.",
    )
    parser.add_argument(
        "--http-url",
        default="http://127.0.0.1:8765",
        help="HTTP server base URL used for live status.",
    )
    parser.add_argument(
        "--emit-diagnostic-file-indexes",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Initial diagnostic file section setting for launched commands.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    index_root = (args.index_root or (root / DEFAULT_INDEX_DIR_NAME)).resolve()
    control = ControlCenter(
        root=root,
        index_root=index_root,
        indexer_root=args.indexer_root.resolve(),
        jobs=args.jobs,
        http_url=args.http_url,
        emit_diagnostic_file_indexes=args.emit_diagnostic_file_indexes,
    )
    return control.run()


if __name__ == "__main__":
    raise SystemExit(main())
