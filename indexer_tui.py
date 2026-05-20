from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from indexer_control import (
    DEFAULT_INDEX_ROOT,
    DEFAULT_PROJECT_ROOT,
    fmt_count,
    kill_process_tree,
    load_ui_settings,
    manifest_status,
    parse_http_url,
    read_http_status,
    request_process_exit,
    save_ui_settings,
    subprocess_creation_flags,
)


try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical
    from textual.widgets import Button, Footer, Header, RichLog, Static

    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False


class ProcessRunner:
    def __init__(self, app: "IndexerTuiApp") -> None:
        self.app = app
        self.process: subprocess.Popen[str] | None = None
        self.last_exit_code: int | None = None
        self.last_command = ""
        self.output_queue: queue.Queue[str] = queue.Queue()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, args: list[str]) -> None:
        if self.running:
            self.app.write_log("A command is already running.")
            return

        self.last_command = " ".join(args)
        self.last_exit_code = None
        self.app.write_log("> " + self.last_command)
        self.process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess_creation_flags(),
        )
        threading.Thread(target=self._read_output, daemon=True).start()

    def stop(self, *, wait: bool = False) -> None:
        if not self.running or self.process is None:
            self.app.write_log("No running command to stop.")
            return

        process = self.process
        if not wait:
            process.terminate()
            self.app.write_log("Terminate requested for running command.")
            return

        request_process_exit(process)
        self.app.write_log("Graceful process shutdown requested.")

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kill_process_tree(process)
            self.app.write_log("Running command did not exit; kill requested.")
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def _read_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None

        for line in self.process.stdout:
            self.output_queue.put(line.rstrip())

        exit_code = self.process.wait()
        self.last_exit_code = exit_code
        self.process = None
        self.output_queue.put(f"Process exited with code {exit_code}.")
        self.app.call_from_thread(self.app.refresh_status)


if TEXTUAL_AVAILABLE:

    class IndexerTuiApp(App):
        CSS = """
        Screen {
            background: #080808;
            color: #d8d8d8;
        }

        #topbar {
            height: 1;
            background: #2d5aa7;
            color: white;
            padding: 0 1;
        }

        #main {
            height: 1fr;
        }

        #actions {
            width: 30;
            border: solid #4c4c4c;
            padding: 1;
        }

        #dashboard {
            width: 1fr;
            border: solid #4c4c4c;
            padding: 1 2;
        }

        #status-grid {
            height: auto;
        }

        #statusbar {
            height: 1;
            background: #2d5aa7;
            color: white;
            padding: 0 1;
        }

        #log {
            height: 1fr;
            border: solid #4c4c4c;
        }

        Button {
            width: 100%;
            margin-bottom: 1;
        }

        .card {
            height: auto;
            margin-bottom: 1;
        }
        """

        BINDINGS = [
            ("b", "build", "Build"),
            ("u", "update", "Update"),
            ("f", "fast_update", "Fast update"),
            ("m", "module_map", "Module map"),
            ("h", "http_server", "HTTP server"),
            ("w", "watcher", "Watcher"),
            ("s", "toggle_diagnostics", "Diagnostics"),
            ("x", "stop_process", "Stop"),
            ("r", "refresh_status", "Refresh"),
            ("q", "quit", "Quit"),
        ]

        def __init__(
            self,
            *,
            root: Path,
            index_root: Path,
            indexer_root: Path,
            jobs: int,
            http_url: str,
            emit_diagnostic_file_indexes: bool,
            theme: str | None = None,
        ) -> None:
            super().__init__()
            self.root = root.resolve()
            self.index_root = index_root.resolve()
            self.indexer_root = indexer_root.resolve()
            self.jobs = jobs
            self.http_url = http_url.rstrip("/")
            self.emit_diagnostic_file_indexes = emit_diagnostic_file_indexes
            self.initial_theme = theme or ""
            self.python = Path(sys.executable)
            self.status_source = "disk"
            self.status: dict[str, Any] = {}
            self.runner = ProcessRunner(self)
            self.apply_theme_setting(self.initial_theme)

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Static("", id="topbar")
            with Horizontal(id="main"):
                with Vertical(id="actions"):
                    yield Static("Actions", classes="card")
                    yield Button("Build index", id="action-build", variant="primary")
                    yield Button("Update index", id="action-update")
                    yield Button("Fast update", id="action-fast-update")
                    yield Button("Build module map", id="action-module-map")
                    yield Button("Start HTTP + watcher", id="action-http-server", variant="success")
                    yield Button("Start watcher", id="action-watcher")
                    yield Button("Toggle diagnostics", id="action-diagnostics")
                    yield Button("Settings / Help", id="action-help")
                    yield Button("Stop command", id="action-stop", variant="error")
                with Vertical(id="dashboard"):
                    with Container(id="status-grid"):
                        yield Static("", id="project")
                        yield Static("", id="server")
                        yield Static("", id="watcher")
                        yield Static("", id="counts")
                        yield Static("", id="stats")
                        yield Static("", id="locks")
                        yield Static("", id="mode")
                    yield RichLog(id="log", wrap=True, markup=False, highlight=False)
            yield Static("", id="statusbar")
            yield Footer()

        def on_mount(self) -> None:
            self.apply_theme_setting(self.initial_theme)
            self.refresh_status()
            self.set_interval(1.0, self.refresh_status)
            self.set_interval(0.1, self.flush_process_log)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            button_id = event.button.id

            if button_id == "action-build":
                self.action_build()
            elif button_id == "action-update":
                self.action_update()
            elif button_id == "action-fast-update":
                self.action_fast_update()
            elif button_id == "action-module-map":
                self.action_module_map()
            elif button_id == "action-http-server":
                self.action_http_server()
            elif button_id == "action-watcher":
                self.action_watcher()
            elif button_id == "action-diagnostics":
                self.action_toggle_diagnostics()
            elif button_id == "action-help":
                self.show_help()
            elif button_id == "action-stop":
                self.action_stop_process()

        def refresh_status(self) -> None:
            http_status = read_http_status(self.http_url)

            if http_status is not None:
                self.status = http_status
                self.status_source = "http"
            else:
                self.status = manifest_status(self.root, self.index_root)
                self.status_source = "disk"

            self.update_dashboard()

        def update_dashboard(self) -> None:
            project = self.status.get("project", {})
            index = self.status.get("index", {})
            server = self.status.get("server", {})
            watcher = self.status.get("watcher", {})
            locks = self.status.get("locks", {})
            counts = index.get("counts", {})
            stats = index.get("stats", {})

            self.query_one("#project", Static).update(
                f"Project  {project.get('root') or self.root.as_posix()}\n"
                f"Index    {project.get('indexRoot') or self.index_root.as_posix()}"
            )
            self.query_one("#server", Static).update(
                f"Server   {server.get('transport', self.status_source)}   "
                f"{self.http_url if self.status_source == 'http' else 'not connected'}   "
                f"pid {server.get('pid', '-')}"
            )
            self.query_one("#watcher", Static).update(
                f"Watcher  {'running' if watcher.get('running') else 'stopped'}   "
                f"lock {'held' if watcher.get('lockHeld') else 'not-held'}   "
                f"last {watcher.get('lastUpdateResult') or '-'}"
            )
            self.query_one("#counts", Static).update(
                "Index    "
                f"files {fmt_count(counts.get('files'))}   "
                f"symbols {fmt_count(counts.get('symbols'))}   "
                f"data {fmt_count(counts.get('data'))}   "
                f"modules {fmt_count(counts.get('modules'))}   "
                f"diagnostics {fmt_count(counts.get('diagnostics'))}"
            )
            self.query_one("#stats", Static).update(
                "Stats    "
                f"code lines {fmt_count(stats.get('totalCodeLines'))}   "
                f"tokens {fmt_count(stats.get('totalTokens'))}"
            )
            self.query_one("#locks", Static).update(
                "Locks    "
                f"updateFile={bool(locks.get('updateLockFileExists'))}   "
                f"watcherFile={bool(locks.get('watcherLockFileExists'))}"
            )
            self.query_one("#mode", Static).update(
                "Mode     "
                f"diagnostic file sections {'ON' if self.emit_diagnostic_file_indexes else 'OFF'}   "
                f"jobs {self.jobs}   "
                f"theme {self.current_theme_name()}"
            )
            self.query_one("#topbar", Static).update(
                "mcp-cpp-project-indexer   "
                f"HTTP: {'connected' if self.status_source == 'http' else 'offline'}   "
                f"Watcher: {'running' if watcher.get('running') else 'stopped'}   "
                f"Diagnostics: {fmt_count(counts.get('diagnostics'))}   "
                f"Jobs: {self.jobs}"
            )
            running = "running" if self.runner.running else "idle"
            self.query_one("#statusbar", Static).update(
                f" F1 Help | B Build | U Update | H HTTP+Watcher | X Stop | Q Quit "
                f"| {running} | source={self.status_source}"
            )

        def write_log(self, text: str) -> None:
            try:
                self.query_one("#log", RichLog).write(text)
            except Exception:
                pass

        def flush_process_log(self) -> None:
            for _ in range(200):
                try:
                    line = self.runner.output_queue.get_nowait()
                except queue.Empty:
                    break

                self.write_log(line)

        def command_base(self, script_name: str) -> list[str]:
            return [str(self.python), str(self.indexer_root / script_name)]

        def append_diagnostic_flag(self, args: list[str]) -> None:
            if self.emit_diagnostic_file_indexes:
                args.append("--emit-diagnostic-file-indexes")

        def action_build(self) -> None:
            args = self.command_base("build_project_index.py") + [
                "--root",
                str(self.root),
                "--output-root",
                str(self.index_root),
                "--jobs",
                str(self.jobs),
            ]
            self.append_diagnostic_flag(args)
            self.runner.start(args)

        def action_update(self) -> None:
            args = self.command_base("update_project_index.py") + [
                "--root",
                str(self.root),
                "--index-root",
                str(self.index_root),
                "--jobs",
                str(self.jobs),
            ]
            self.append_diagnostic_flag(args)
            self.runner.start(args)

        def action_fast_update(self) -> None:
            args = self.command_base("update_project_index.py") + [
                "--root",
                str(self.root),
                "--index-root",
                str(self.index_root),
                "--jobs",
                str(self.jobs),
                "--known-files-only",
            ]
            self.append_diagnostic_flag(args)
            self.runner.start(args)

        def action_module_map(self) -> None:
            if not (self.index_root / "manifest.json").exists():
                self.write_log(
                    f"Cannot build module map: manifest not found at {self.index_root / 'manifest.json'}"
                )
                return

            self.runner.start(
                self.command_base("build_module_map.py")
                + ["--index-root", str(self.index_root)]
            )

        def action_http_server(self) -> None:
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

        def action_watcher(self) -> None:
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
            self.append_diagnostic_flag(args)
            self.runner.start(args)

        def action_toggle_diagnostics(self) -> None:
            self.emit_diagnostic_file_indexes = not self.emit_diagnostic_file_indexes
            self.write_log(
                "Diagnostic file sections "
                + ("enabled." if self.emit_diagnostic_file_indexes else "disabled.")
            )
            self.save_settings()
            self.update_dashboard()

        def action_stop_process(self) -> None:
            self.runner.stop()

        def on_exit(self) -> None:
            self.runner.stop(wait=True)
            self.save_settings()

        def apply_theme_setting(self, theme: str) -> None:
            if not theme:
                return

            normalized = theme.lower()
            if normalized in {"dark", "light"}:
                self.dark = normalized == "dark"
                return

            try:
                self.theme = theme
                return
            except Exception:
                pass

            if "light" in normalized:
                self.dark = False
            elif "dark" in normalized:
                self.dark = True

        def current_theme_name(self) -> str:
            try:
                theme = getattr(self, "theme")
                if isinstance(theme, str) and theme:
                    return theme
            except Exception:
                pass

            try:
                return "dark" if self.dark else "light"
            except Exception:
                return self.initial_theme or "textual-dark"

        def save_settings(self) -> None:
            save_ui_settings(
                indexer_root=self.indexer_root,
                root=self.root,
                index_root=self.index_root,
                http_url=self.http_url,
                jobs=self.jobs,
                emit_diagnostic_file_indexes=self.emit_diagnostic_file_indexes,
                theme=self.current_theme_name(),
            )

        def action_refresh_status(self) -> None:
            self.refresh_status()
            self.write_log("Status refreshed.")

        def show_help(self) -> None:
            self.write_log("Project root: " + str(self.root))
            self.write_log("Index root: " + str(self.index_root))
            self.write_log("HTTP URL: " + self.http_url)
            self.write_log("Theme: " + self.current_theme_name())
            self.write_log(
                "Shortcuts: B build, U update, F fast update, M module map, "
                "H HTTP+watcher, W watcher, S diagnostics, X stop, R refresh, Q quit."
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optional Textual terminal UI for mcp-cpp-project-indexer."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_PROJECT_ROOT,
        help="C++ project root. Defaults to MCP_CPP_PROJECT_ROOT or current directory.",
    )
    parser.add_argument(
        "--index-root",
        type=Path,
        default=DEFAULT_INDEX_ROOT,
        help="Index root. Defaults to MCP_CPP_INDEX_ROOT or <root>/.mcp-cpp-project-indexer.",
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
        help="Worker process count for launched commands.",
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

    if not TEXTUAL_AVAILABLE:
        print("Textual is not installed.")
        print("Install the optional UI dependency with:")
        print("  pip install -r requirements-ui.txt")
        return 2

    root = args.root.resolve()
    index_root = args.index_root.resolve()
    indexer_root = args.indexer_root.resolve()
    settings = load_ui_settings(
        indexer_root=indexer_root,
        root=root,
        index_root=index_root,
    )
    http_url = str(settings.get("httpUrl") or args.http_url)
    jobs = int(settings.get("jobs") or args.jobs)
    theme = str(settings.get("theme") or "textual-dark")
    emit_diagnostic_file_indexes = bool(
        settings.get("emitDiagnosticFileIndexes")
        if "emitDiagnosticFileIndexes" in settings
        else args.emit_diagnostic_file_indexes
    )
    app = IndexerTuiApp(
        root=root,
        index_root=index_root,
        indexer_root=indexer_root,
        jobs=jobs,
        http_url=http_url,
        emit_diagnostic_file_indexes=emit_diagnostic_file_indexes,
        theme=theme,
    )
    try:
        app.run()
    finally:
        app.save_settings()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
