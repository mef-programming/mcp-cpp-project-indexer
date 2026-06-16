const $ = (selector) => document.querySelector(selector);
const MANAGEMENT_TOKEN_STORAGE_KEY = "managedMcp.managementToken";

const state = {
  statusTimer: null,
  apiToken: sessionStorage.getItem(MANAGEMENT_TOKEN_STORAGE_KEY) ||
    sessionStorage.getItem("cppIndexer.managementToken") ||
    "",
  commandSince: 0,
  serverSince: 0,
  commandEvents: [],
  serverEvents: [],
  previousProcessStats: null,
};

function initializeTokenFromHash() {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const token = params.get("token");
  if (!token) return;
  state.apiToken = token;
  sessionStorage.setItem(MANAGEMENT_TOKEN_STORAGE_KEY, token);
  sessionStorage.setItem("cppIndexer.managementToken", token);
  history.replaceState(null, document.title, window.location.pathname);
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(state.apiToken ? { "x-api-key": state.apiToken } : {}),
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

function setText(selector, value) {
  const element = $(selector);
  if (element) {
    element.textContent = value ?? "-";
  }
}

function formatNumber(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return Number(value).toLocaleString();
}

function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value)) return "-";
  const mib = value / 1024 / 1024;
  return `${mib.toFixed(mib >= 100 ? 0 : 1)} MiB`;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function normalizeProcess(status) {
  const dashboardServer = (status.dashboard && status.dashboard.server) || {};
  const server = status.server || {};
  const process = server.process || {};
  const cpuUser = Number(process.cpuUserSeconds);
  const cpuSystem = Number(process.cpuSystemSeconds);
  const cpuTimeSeconds = Number.isFinite(Number(dashboardServer.cpuTimeSeconds))
    ? Number(dashboardServer.cpuTimeSeconds)
    : Number.isFinite(cpuUser) || Number.isFinite(cpuSystem)
      ? (Number.isFinite(cpuUser) ? cpuUser : 0) + (Number.isFinite(cpuSystem) ? cpuSystem : 0)
      : NaN;
  const cpuCores = Number.isFinite(Number(dashboardServer.cpuCoresAverage))
    ? Number(dashboardServer.cpuCoresAverage)
    : Number.isFinite(Number(process.cpuCoresAverage))
      ? Number(process.cpuCoresAverage)
      : NaN;
  const uptimeSeconds = Number.isFinite(Number(dashboardServer.uptimeSeconds))
    ? Number(dashboardServer.uptimeSeconds)
    : Number.isFinite(Number(process.createTime))
      ? Math.max(0, Date.now() / 1000 - Number(process.createTime))
      : NaN;

  return {
    pid: server.pid || dashboardServer.pid || process.pid,
    ramBytes: Number.isFinite(Number(dashboardServer.ramBytes)) ? Number(dashboardServer.ramBytes) : Number(process.rssBytes),
    ramText: dashboardServer.ramText || formatBytes(process.rssBytes),
    heapBytes: Number.isFinite(Number(dashboardServer.heapBytes))
      ? Number(dashboardServer.heapBytes)
      : Number.isFinite(Number(process.heapBytes))
        ? Number(process.heapBytes)
        : Number.isFinite(Number(process.heapUsedBytes))
          ? Number(process.heapUsedBytes)
          : Number(process.rssBytes),
    heapText: dashboardServer.heapText || formatBytes(process.heapBytes || process.heapUsedBytes || process.rssBytes),
    cpuCores,
    cpuText: dashboardServer.cpuText || (Number.isFinite(cpuCores) ? `${cpuCores.toFixed(2)}c` : "-"),
    cpuTimeSeconds,
    cpuTimeText: dashboardServer.cpuTimeText || (Number.isFinite(cpuTimeSeconds) ? `${cpuTimeSeconds.toFixed(1)}s` : "-"),
    uptimeSeconds,
    uptimeText: dashboardServer.uptimeText || (Number.isFinite(uptimeSeconds) ? formatDuration(uptimeSeconds) : "-"),
    threads: Number.isFinite(Number(dashboardServer.threads)) ? Number(dashboardServer.threads) : Number(process.threads),
    threadsText: dashboardServer.threadsText || formatNumber(process.threads),
  };
}

function pulseProcessBadge(key, currentValue) {
  const badge = document.querySelector(`[data-process-key="${key}"]`);
  if (!badge || !Number.isFinite(currentValue)) return;
  const previous = state.previousProcessStats ? state.previousProcessStats[key] : undefined;
  badge.classList.remove("trend-up", "trend-down", "trend-same", "trend-live");
  if (key === "uptimeSeconds") {
    badge.classList.add("trend-live");
    window.setTimeout(() => badge.classList.remove("trend-live"), 900);
    return;
  }
  if (Number.isFinite(previous)) {
    const epsilon = key === "ramBytes" ? 1024 * 32 : 0.001;
    const delta = currentValue - previous;
    badge.classList.add(Math.abs(delta) <= epsilon ? "trend-same" : delta > 0 ? "trend-up" : "trend-down");
    window.setTimeout(() => badge.classList.remove("trend-up", "trend-down", "trend-same"), 900);
  }
}

function renderProcessStats(status) {
  const processStats = normalizeProcess(status);
  setText("#ramValue", processStats.ramText || "-");
  setText("#heapValue", processStats.heapText || "-");
  setText("#cpuValue", processStats.cpuText || "-");
  setText("#cpuTimeValue", processStats.cpuTimeText || "-");
  setText("#uptimeValue", processStats.uptimeText || "-");
  setText("#threadsValue", processStats.threadsText || "-");
  for (const key of ["ramBytes", "heapBytes", "cpuCores", "cpuTimeSeconds", "uptimeSeconds", "threads"]) {
    pulseProcessBadge(key, processStats[key]);
  }
  state.previousProcessStats = processStats;
}

function renderDetails(status) {
  const server = status.server || {};
  const security = status.security || {};
  const dashboard = status.dashboard || {};
  const processStats = normalizeProcess(status);
  const counts = dashboard.counts || {};
  const stats = dashboard.stats || {};
  const watcher = dashboard.watcher || {};
  const details = [
    ["Server", server.name || "mcp-cpp-project-indexer"],
    ["Version", server.version || "-"],
    ["Started", server.startedAt || "-"],
    ["PID", processStats.pid || "-"],
    ["RAM", processStats.ramText || "-"],
    ["Heap", processStats.heapText || "-"],
    ["CPU", processStats.cpuText || "-"],
    ["CPU time", processStats.cpuTimeText || "-"],
    ["Uptime", processStats.uptimeText || "-"],
    ["Threads", processStats.threadsText || "-"],
    ["TLS", security.tlsMode || "-"],
    ["Auth", security.authMode || (status.requiresToken ? "token" : "none")],
    ["Watcher updates", watcher.updateCountText || formatNumber(watcher.updateCount)],
    ["Files", counts.filesText || counts.files || "-"],
    ["Symbols", counts.symbolsText || counts.symbols || "-"],
    ["Data", counts.dataText || counts.data || "-"],
    ["Modules", counts.modulesText || counts.modules || "-"],
    ["Code lines", stats.codeLinesText || formatNumber(stats.codeLines)],
    ["Tokens", stats.tokensText || formatNumber(stats.tokens)],
  ];
  $("#detailsList").innerHTML = details
    .map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`)
    .join("");
  syncServerLogHeight();
}

function syncServerLogHeight() {
  const detailsPanel = $("#detailsPanel");
  const serverLogPanel = $("#serverLogPanel");
  if (!detailsPanel || !serverLogPanel) return;
  if (window.matchMedia("(max-width: 900px)").matches) {
    serverLogPanel.style.height = "";
    return;
  }
  serverLogPanel.style.height = `${detailsPanel.offsetHeight}px`;
}

function renderStatus(status) {
  const dashboard = status.dashboard || {};
  const counts = dashboard.counts || {};
  const stats = dashboard.stats || {};
  const watcher = dashboard.watcher || {};
  const runner = status.runner || {};
  const watcherText = watcher.runningText || (watcher.running ? "running" : "stopped");
  const commandText = runner.running ? "running" : "idle";

  $("#statusPill").textContent = "online";
  $("#statusPill").classList.add("online");
  setText("#filesValue", counts.filesText || formatNumber(counts.files));
  setText("#symbolsValue", counts.symbolsText || formatNumber(counts.symbols));
  setText("#dataValue", counts.dataText || formatNumber(counts.data));
  setText("#modulesValue", counts.modulesText || formatNumber(counts.modules));
  setText("#linesValue", stats.codeLinesText || formatNumber(stats.codeLines));
  setText("#diagnosticsValue", counts.diagnosticsText || formatNumber(counts.diagnostics));
  setText("#watcherValue", watcherText);
  setText("#updatesValue", watcher.updateCountText || formatNumber(watcher.updateCount));
  setText("#commandValue", commandText);
  renderProcessStats(status);
  setText(
    "#commandState",
    runner.running
      ? `Command running: ${runner.lastCommand || "-"}`
      : `Command idle${runner.lastExitCode !== null && runner.lastExitCode !== undefined ? `, last exit ${runner.lastExitCode}` : ""}`,
  );
  renderDetails(status);
}

async function refreshStatus() {
  try {
    renderStatus(await requestJson("/server/management/status"));
  } catch (error) {
    $("#statusPill").textContent = "offline";
    $("#statusPill").classList.remove("online");
    setText("#commandState", error.message);
  }
}

function eventLine(event) {
  const timestamp = event.timestamp || event.time || "";
  const level = event.level || event.outcome || "info";
  const message = event.message || event.summary || event.path || JSON.stringify(event);
  return `${timestamp}  ${level}  ${message}`;
}

function matchesServerLogFilters(event) {
  const levelFilter = $("#serverLogLevelFilter")?.value || "all";
  const textFilter = ($("#serverLogTextFilter")?.value || "").trim().toLowerCase();
  const level = String(event.level || event.outcome || "info").toLowerCase();
  const line = eventLine(event).toLowerCase();
  if (levelFilter !== "all") {
    const isWarning = level.includes("warn") || line.includes(" warning ") || line.includes(" 4");
    const isError = level.includes("error") || line.includes(" error ") || line.includes(" 5");
    const isInfo = level.includes("info");
    if (levelFilter === "warning" && !isWarning) return false;
    if (levelFilter === "error" && !isError) return false;
    if (levelFilter === "info" && !isInfo) return false;
  }
  return !textFilter || line.includes(textFilter);
}

function renderLogs() {
  $("#commandLog").textContent = state.commandEvents.map(eventLine).join("\n");
  $("#serverLog").textContent = state.serverEvents.filter(matchesServerLogFilters).map(eventLine).join("\n");
  syncServerLogHeight();
}

async function refreshLogs() {
  try {
    const commandPayload = await requestJson(`/server/management/log?since=${state.commandSince}&limit=200`);
    const serverPayload = await requestJson(`/server/management/server-log?since=${state.serverSince}&limit=200`);
    state.commandEvents.push(...(commandPayload.events || []));
    state.serverEvents.push(...(serverPayload.events || []));
    state.commandEvents = state.commandEvents.slice(-500);
    state.serverEvents = state.serverEvents.slice(-500);
    state.commandSince = commandPayload.nextLogEventId || state.commandSince;
    state.serverSince = serverPayload.nextLogEventId || state.serverSince;
    renderLogs();
  } catch {
    // Status polling already surfaces connectivity/auth failures.
  }
}

async function runCommand(command) {
  const jobs = Number($("#jobsInput").value) || 20;
  setText("#commandState", `Starting ${command}...`);
  try {
    await requestJson("/server/management/command", {
      method: "POST",
      body: JSON.stringify({ command, jobs }),
    });
    setText("#commandState", `${command} accepted`);
    await refreshStatus();
    await refreshLogs();
  } catch (error) {
    setText("#commandState", error.message);
  }
}

function reportHostHeight() {
  if (window.parent === window) return;
  const body = document.body;
  const root = document.documentElement;
  const height = Math.max(
    body?.scrollHeight || 0,
    body?.offsetHeight || 0,
    root?.scrollHeight || 0,
    root?.offsetHeight || 0,
    root?.clientHeight || 0,
  );
  if (!height) return;
  window.parent.postMessage(
    {
      type: "managed-mcp-ui-height",
      height,
      contentHeight: height,
      scrollHeight: height,
    },
    "*",
  );
}

function installHostResizeReporter() {
  reportHostHeight();
  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(reportHostHeight);
    observer.observe(document.body);
    observer.observe(document.documentElement);
  }
  window.addEventListener("load", reportHostHeight);
  window.addEventListener("resize", reportHostHeight);
  for (const delay of [100, 300, 800, 1500, 3000]) {
    window.setTimeout(reportHostHeight, delay);
  }
}

window.addEventListener("message", (event) => {
  const data = event.data || {};
  if (
    !["managed-mcp-management-token", "cpp-indexer-management-token"].includes(data.type) ||
    typeof data.token !== "string"
  ) {
    return;
  }
  state.apiToken = data.token;
  sessionStorage.setItem(MANAGEMENT_TOKEN_STORAGE_KEY, data.token);
  void refreshStatus();
  void refreshLogs();
});

document.querySelectorAll("[data-command]").forEach((button) => {
  button.addEventListener("click", () => {
    void runCommand(button.dataset.command);
  });
});

$("#refreshButton").addEventListener("click", () => {
  void refreshStatus();
  void refreshLogs();
});

$("#clearCommandLog").addEventListener("click", () => {
  state.commandEvents = [];
  renderLogs();
});

$("#clearServerLog").addEventListener("click", () => {
  state.serverEvents = [];
  renderLogs();
});

$("#serverLogLevelFilter").addEventListener("change", renderLogs);
$("#serverLogTextFilter").addEventListener("input", renderLogs);

function startPolling() {
  window.clearInterval(state.statusTimer);
  void refreshStatus();
  void refreshLogs();
  state.statusTimer = window.setInterval(() => {
    void refreshStatus();
    void refreshLogs();
  }, document.hidden ? 5000 : 1500);
}

document.addEventListener("visibilitychange", startPolling);
window.addEventListener("resize", syncServerLogHeight);
initializeTokenFromHash();
startPolling();
installHostResizeReporter();
