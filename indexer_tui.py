from __future__ import annotations

import argparse
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from indexer_control import (
    DEFAULT_INDEX_ROOT,
    DEFAULT_PROJECT_ROOT,
    fmt_bytes,
    fmt_count,
    fmt_duration,
    iso_age_seconds,
    kill_process_tree,
    load_ui_settings,
    manifest_status,
    parse_http_url,
    process_stats,
    read_http_status,
    request_process_exit,
    save_ui_settings,
    subprocess_creation_flags,
    ui_settings_path,
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
        self.stopping = False

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, args: list[str]) -> None:
        if self.running:
            self.app.write_log("A command is already running.")
            return

        self.stopping = False
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

        if self.stopping:
            return

        self.stopping = True
        request_process_exit(process)
        self.output_queue.put("Graceful process shutdown requested.")

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kill_process_tree(process)
            self.output_queue.put("Running command did not exit; kill requested.")
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def stop_async(self) -> None:
        if not self.running:
            self.app.write_log("No running command to stop.")
            return

        threading.Thread(
            target=self.stop,
            kwargs={"wait": True},
            daemon=True,
        ).start()

    def shutdown(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return

        process = self.process
        if not self.stopping:
            self.stopping = True
            request_process_exit(process)

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kill_process_tree(process)
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
        self.stopping = False
        self.output_queue.put(f"Process exited with code {exit_code}.")
        try:
            self.app.call_from_thread(self.app.refresh_status)
        except RuntimeError:
            pass


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
            ("c", "clean_index", "Clean"),
            ("u", "update", "Update"),
            ("f", "fast_update", "Fast update"),
            ("m", "module_map", "Module map"),
            ("h", "http_server", "HTTP server"),
            ("g", "management_server", "HTTP management"),
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
            management_api_enabled: bool,
            management_token: str,
            emit_diagnostic_file_indexes: bool,
            theme: str | None = None,
        ) -> None:
            super().__init__()
            self.root = root.resolve()
            self.index_root = index_root.resolve()
            self.indexer_root = indexer_root.resolve()
            self.jobs = jobs
            self.http_url = http_url.rstrip("/")
            self.management_api_enabled = management_api_enabled
            self.management_token = management_token
            self.emit_diagnostic_file_indexes = emit_diagnostic_file_indexes
            self.initial_theme = theme or ""
            self.python = Path(sys.executable)
            self.status_source = "disk"
            self.status: dict[str, Any] = {}
            self.runner = ProcessRunner(self)
            self.shutdown_done = False
            self.clean_confirm_until = 0.0
            self.apply_theme_setting(self.initial_theme)

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Static("", id="topbar")
            with Horizontal(id="main"):
                with Vertical(id="actions"):
                    yield Static("Actions", classes="card")
                    yield Button("Build index", id="action-build", variant="primary")
                    yield Button("Clean index", id="action-clean")
                    yield Button("Update index", id="action-update")
                    yield Button("Fast update", id="action-fast-update")
                    yield Button("Build module map", id="action-module-map")
                    yield Button("Start HTTP + watcher", id="action-http-server", variant="success")
                    yield Button("Start HTTP + management", id="action-management-server", variant="success")
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

        def on_unmount(self) -> None:
            self.shutdown_processes()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            button_id = event.button.id

            if button_id == "action-build":
                self.action_build()
            elif button_id == "action-clean":
                self.action_clean_index()
            elif button_id == "action-update":
                self.action_update()
            elif button_id == "action-fast-update":
                self.action_fast_update()
            elif button_id == "action-module-map":
                self.action_module_map()
            elif button_id == "action-http-server":
                self.action_http_server()
            elif button_id == "action-management-server":
                self.action_management_server()
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
            server_process = self.resolve_process_stats(server.get("process", {}))
            watcher = self.status.get("watcher", {})
            locks = self.status.get("locks", {})
            counts = index.get("counts", {})
            stats = index.get("stats", {})

            http_connected = self.status_source == "http"
            watcher_running = bool(watcher.get("running"))
            lock_held = bool(watcher.get("lockHeld"))
            update_lock_file = bool(locks.get("updateLockFileExists"))
            watcher_lock_file = bool(locks.get("watcherLockFileExists"))
            diagnostics_enabled = bool(self.emit_diagnostic_file_indexes)

            self.query_one("#project", Static).update(
                f"{self.label('Project')} {self.value(project.get('root') or self.root.as_posix())}\n"
                f"{self.label('Index')}   {self.value(project.get('indexRoot') or self.index_root.as_posix())}"
            )
            self.query_one("#server", Static).update(
                f"{self.label('Server')}  "
                f"{self.state_value(self.http_url if http_connected else 'not connected', http_connected)}   "
                f"{self.label('PID')} {self.value(server.get('pid') or server_process.get('pid') or '-')}   "
                f"{self.label('RAM')} {self.value(fmt_bytes(server_process.get('rssBytes')))}   "
                f"{self.label('CPU')} {self.value(self.process_cpu_percent_text(server_process))}   "
                f"{self.label('Uptime')} {self.value(self.server_uptime_text(server))}"
            )
            self.query_one("#watcher", Static).update(
                f"{self.label('Watcher')} "
                f"{self.state_value('running' if watcher_running else 'stopped', watcher_running)}   "
                f"{self.label('lock')} {self.state_value('held' if lock_held else 'not-held', lock_held)}   "
                f"{self.label('last')} {self.value(watcher.get('lastUpdateResult') or '-')}"
            )
            self.query_one("#counts", Static).update(
                f"{self.label('Index')}   "
                f"{self.label('files')} {self.value(fmt_count(counts.get('files')))}   "
                f"{self.label('symbols')} {self.value(fmt_count(counts.get('symbols')))}   "
                f"{self.label('data')} {self.value(fmt_count(counts.get('data')))}   "
                f"{self.label('modules')} {self.value(fmt_count(counts.get('modules')))}   "
                f"{self.label('diagnostics')} {self.warn_value(fmt_count(counts.get('diagnostics')))}"
            )
            self.query_one("#stats", Static).update(
                f"{self.label('Stats')}   "
                f"{self.label('code lines')} {self.value(fmt_count(stats.get('totalCodeLines')))}   "
                f"{self.label('tokens')} {self.value(fmt_count(stats.get('totalTokens')))}   "
                f"{self.label('cpu time')} {self.value(self.process_cpu_time_text(server_process))}   "
                f"{self.label('threads')} {self.value(fmt_count(server_process.get('threads')))}"
            )
            self.query_one("#locks", Static).update(
                f"{self.label('Locks')}   "
                f"{self.label('updateFile')} {self.state_value(str(update_lock_file), update_lock_file)}   "
                f"{self.label('watcherFile')} {self.state_value(str(watcher_lock_file), watcher_lock_file)}"
            )
            self.query_one("#mode", Static).update(
                f"{self.label('Mode')}    "
                f"{self.label('diagnostic file sections')} {self.state_value('ON' if diagnostics_enabled else 'OFF', diagnostics_enabled)}   "
                f"{self.label('mgmt')} {self.state_value('ON' if self.management_api_enabled else 'OFF', self.management_api_enabled)}   "
                f"{self.label('jobs')} {self.value(self.jobs)}   "
                f"{self.label('theme')} {self.value(self.current_theme_name())}"
            )
            self.query_one("#topbar", Static).update(
                f"{self.value('mcp-cpp-project-indexer')}   "
                f"{self.label('HTTP:')} {self.state_value('connected' if http_connected else 'offline', http_connected)}   "
                f"{self.label('Watcher:')} {self.state_value('running' if watcher_running else 'stopped', watcher_running)}   "
                f"{self.label('Diagnostics:')} {self.warn_value(fmt_count(counts.get('diagnostics')))}   "
                f"{self.label('Mgmt:')} {self.state_value('on' if self.management_api_enabled else 'off', self.management_api_enabled)}   "
                f"{self.label('SQLite:')} {self.state_value('yes' if (self.index_root / 'index.sqlite').exists() else 'no', (self.index_root / 'index.sqlite').exists())}   "
                f"{self.label('Jobs:')} {self.value(self.jobs)}"
            )
            running = "running" if self.runner.running else "idle"
            self.query_one("#statusbar", Static).update(
                f" {self.label('F1')} Help | {self.label('B')} Build | {self.label('C')} Clean | {self.label('U')} Update | "
                f"{self.label('H')} HTTP+Watcher | {self.label('X')} Stop | {self.label('Q')} Quit "
                f"| {self.state_value(running, self.runner.running)} | "
                f"{self.label('source=')}{self.value(self.status_source)}"
            )

        def write_log(self, text: str) -> None:
            try:
                self.query_one("#log", RichLog).write(text)
            except Exception:
                pass

        @staticmethod
        def label(text: Any) -> str:
            return f"[dim]{text}[/dim]"

        @staticmethod
        def value(text: Any) -> str:
            return f"[bold cyan]{text}[/bold cyan]"

        @staticmethod
        def warn_value(text: Any) -> str:
            try:
                number = int(str(text).replace(",", ""))
            except ValueError:
                number = 0
            style = "yellow" if number else "green"
            return f"[bold {style}]{text}[/bold {style}]"

        @staticmethod
        def state_value(text: Any, active: bool) -> str:
            style = "green" if active else "dim"
            return f"[bold {style}]{text}[/bold {style}]"

        def local_process_stats(self) -> dict[str, Any]:
            if self.runner.process is None or self.runner.process.poll() is not None:
                return {}

            return process_stats(self.runner.process.pid)

        def resolve_process_stats(self, stats: Any) -> dict[str, Any]:
            resolved = dict(stats) if isinstance(stats, dict) else {}
            local = self.local_process_stats()

            if local and (
                not resolved
                or resolved.get("pid") in {None, local.get("pid")}
            ):
                for key, value in local.items():
                    if resolved.get(key) in {None, "", "-"}:
                        resolved[key] = value

            pid = resolved.get("pid")
            if pid is not None and (
                resolved.get("rssBytes") is None
                or resolved.get("threads") is None
            ):
                extra = process_stats(pid)
                for key, value in extra.items():
                    if resolved.get(key) in {None, "", "-"}:
                        resolved[key] = value

            return resolved

        def process_cpu_time_text(self, stats: dict[str, Any]) -> str:
            user = stats.get("cpuUserSeconds")
            system = stats.get("cpuSystemSeconds")
            try:
                return f"{float(user) + float(system):.1f}s"
            except (TypeError, ValueError):
                if self.runner.process is not None:
                    local_stats = self.local_process_stats()
                    local_user = local_stats.get("cpuUserSeconds")
                    local_system = local_stats.get("cpuSystemSeconds")
                    try:
                        return f"{float(local_user) + float(local_system):.1f}s"
                    except (TypeError, ValueError):
                        return "-"

                return "-"

        def process_cpu_percent_text(self, stats: dict[str, Any]) -> str:
            uptime = self.server_uptime_seconds()
            if uptime is None or uptime <= 0:
                return "-"

            user = stats.get("cpuUserSeconds")
            system = stats.get("cpuSystemSeconds")
            try:
                percent = ((float(user) + float(system)) / uptime) * 100.0
            except (TypeError, ValueError):
                return "-"

            cpu_count = os.cpu_count() or 1
            return f"{percent / 100.0:.2f}c / {percent / cpu_count:.1f}%"

        def server_uptime_seconds(self) -> float | None:
            server = self.status.get("server", {})
            uptime = iso_age_seconds(server.get("startedAt"))
            if uptime is not None:
                return uptime

            process = self.resolve_process_stats(server.get("process", {}))
            create_time = process.get("createTime")
            try:
                return max(0.0, time.time() - float(create_time))
            except (TypeError, ValueError):
                pass

            return None

        def server_uptime_text(self, server: dict[str, Any]) -> str:
            return fmt_duration(self.server_uptime_seconds())

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

        def action_clean_index(self) -> None:
            if self.runner.running:
                self.write_log("Stop the running command before cleaning the index.")
                return

            now = time.monotonic()
            if now > self.clean_confirm_until:
                self.clean_confirm_until = now + 8.0
                self.write_log(
                    "Clean index requested. Press Clean/C again within 8 seconds to delete index files."
                )
                return

            self.clean_confirm_until = 0.0
            removed = self.clean_index_directory()
            self.write_log(f"Cleaned index directory: removed {removed} item(s).")
            self.refresh_status()

        def clean_index_directory(self) -> int:
            index_root = self.index_root.resolve()
            project_root = self.root.resolve()

            if index_root == project_root:
                self.write_log("Refusing to clean: index root equals project root.")
                return 0

            if not index_root.exists():
                index_root.mkdir(parents=True, exist_ok=True)
                return 0

            marker_names = {
                "manifest.json",
                "index.sqlite",
                "symbols.jsonl",
                "names.json",
                "data.jsonl",
                "data_names.json",
                "update_state.json",
                "modules.json",
                "diagnostics.json",
            }
            has_index_marker = any((index_root / name).exists() for name in marker_names) or (index_root / "files").exists()

            if not has_index_marker:
                self.write_log("Refusing to clean: index root does not look like an index directory.")
                return 0

            removed = 0
            for child in index_root.iterdir():
                child_path = child.resolve()

                try:
                    child_path.relative_to(index_root)
                except ValueError:
                    self.write_log(f"Skipping unexpected path outside index root: {child_path}")
                    continue

                if child.is_dir():
                    shutil.rmtree(child_path)
                else:
                    child.unlink()

                removed += 1

            return removed

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

        def action_management_server(self) -> None:
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
                "--enable-management-api",
            ]

            if self.management_token:
                args.extend(["--management-token", self.management_token])
            else:
                self.write_log(
                    "Warning: management API is starting without a token. "
                    "Use managementToken in the TUI settings file for external UIs."
                )

            if self.emit_diagnostic_file_indexes:
                args.append("--watch-emit-diagnostic-file-indexes")

            self.management_api_enabled = True
            self.save_settings()
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
            self.runner.stop_async()

        def action_quit(self) -> None:
            self.shutdown_processes()
            self.exit()

        def shutdown_processes(self) -> None:
            if self.shutdown_done:
                return

            self.shutdown_done = True
            self.runner.shutdown()
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
                management_api_enabled=self.management_api_enabled,
                management_token=self.management_token,
            )

        def action_refresh_status(self) -> None:
            self.refresh_status()
            self.write_log("Status refreshed.")

        def show_help(self) -> None:
            self.write_log("Project root: " + str(self.root))
            self.write_log("Index root: " + str(self.index_root))
            self.write_log("HTTP URL: " + self.http_url)
            self.write_log("Management API: " + ("enabled" if self.management_api_enabled else "disabled"))
            self.write_log(
                "Management token: "
                + ("configured" if self.management_token else "not configured")
            )
            self.write_log(
                "Settings file: "
                + str(
                    ui_settings_path(
                        indexer_root=self.indexer_root,
                        root=self.root,
                        index_root=self.index_root,
                    )
                )
            )
            self.write_log("Theme: " + self.current_theme_name())
            self.write_log(
                "Shortcuts: B build, U update, F fast update, M module map, "
                "C clean index, H HTTP+watcher, G HTTP+management, W watcher, "
                "S diagnostics, X stop, R refresh, Q quit."
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
        default=None,
        help="Worker process count for launched commands.",
    )
    parser.add_argument(
        "--http-url",
        default="http://127.0.0.1:8765",
        help="HTTP server base URL used for live status.",
    )
    parser.add_argument(
        "--enable-management-api",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable the TUI's HTTP + management launch mode.",
    )
    parser.add_argument(
        "--management-token",
        default=None,
        help="Management API token used when launching HTTP + management.",
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
    jobs = int(args.jobs if args.jobs is not None else settings.get("jobs") or 1)
    management_api_enabled = bool(
        args.enable_management_api
        if args.enable_management_api is not None
        else settings.get("managementApiEnabled", False)
    )
    management_token = str(
        args.management_token
        if args.management_token is not None
        else settings.get("managementToken") or ""
    )
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
        management_api_enabled=management_api_enabled,
        management_token=management_token,
        emit_diagnostic_file_indexes=emit_diagnostic_file_indexes,
        theme=theme,
    )
    try:
        app.run()
    finally:
        app.shutdown_processes()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
