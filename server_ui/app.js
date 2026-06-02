const $ = (selector) => document.querySelector(selector);

const state = {
  statusTimer: null,
  apiToken: sessionStorage.getItem("cppIndexer.managementToken") || "",
  commandSince: 0,
  serverSince: 0,
  commandEvents: [],
  serverEvents: [],
};

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

function renderDetails(status) {
  const server = status.server || {};
  const security = status.security || {};
  const dashboard = status.dashboard || {};
  const counts = dashboard.counts || {};
  const details = [
    ["Server", server.name || "mcp-cpp-project-indexer"],
    ["Version", server.version || "-"],
    ["Started", server.startedAt || "-"],
    ["TLS", security.tlsMode || "-"],
    ["Auth", security.authMode || (status.requiresToken ? "token" : "none")],
    ["Files", counts.filesText || counts.files || "-"],
    ["Symbols", counts.symbolsText || counts.symbols || "-"],
    ["Data", counts.dataText || counts.data || "-"],
    ["Modules", counts.modulesText || counts.modules || "-"],
  ];
  $("#detailsList").innerHTML = details
    .map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`)
    .join("");
}

function renderStatus(status) {
  const dashboard = status.dashboard || {};
  const counts = dashboard.counts || {};
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
  setText("#diagnosticsValue", counts.diagnosticsText || formatNumber(counts.diagnostics));
  setText("#watcherValue", watcherText);
  setText("#commandValue", commandText);
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

function renderLogs() {
  $("#commandLog").textContent = state.commandEvents.map(eventLine).join("\n");
  $("#serverLog").textContent = state.serverEvents.map(eventLine).join("\n");
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

window.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type !== "cpp-indexer-management-token" || typeof data.token !== "string") {
    return;
  }
  state.apiToken = data.token;
  sessionStorage.setItem("cppIndexer.managementToken", data.token);
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
startPolling();
