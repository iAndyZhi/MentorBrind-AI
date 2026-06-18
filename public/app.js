const messages = document.querySelector("#messages");
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const accessStatus = document.querySelector("#accessStatus");
const accessPanel = document.querySelector("#accessPanel");
const accessCodeInput = document.querySelector("#accessCodeInput");
const accessError = document.querySelector("#accessError");
const unlockApp = document.querySelector("#unlockApp");
const healthStatus = document.querySelector("#healthStatus");
const healthMessage = document.querySelector("#healthMessage");
const tokenStatus = document.querySelector("#tokenStatus");
const oauthStatus = document.querySelector("#oauthStatus");
const openaiStatus = document.querySelector("#openaiStatus");
const storageStatus = document.querySelector("#storageStatus");
const indexStatus = document.querySelector("#indexStatus");
const indexRefreshIn = document.querySelector("#indexRefreshIn");
const indexDelta = document.querySelector("#indexDelta");
const scannedCount = document.querySelector("#scannedCount");
const chunkCount = document.querySelector("#chunkCount");
const skippedCount = document.querySelector("#skippedCount");
const mode = document.querySelector("#mode");
const connectDrive = document.querySelector("#connectDrive");
const disconnectDrive = document.querySelector("#disconnectDrive");
const refreshIndex = document.querySelector("#refreshIndex");

let sourceState = null;
let accessState = { enabled: false, granted: true };
let healthState = null;
const CHAT_TIMEOUT_MS = 75000;
const SOURCE_EXCERPT_CHARS = 100;
let indexPollTimer = null;

function truncateText(value, limit) {
  const characters = Array.from(String(value));
  if (characters.length <= limit) return characters.join("");
  return `${characters.slice(0, Math.max(0, limit - 3)).join("").trimEnd()}...`;
}

function addMessage(role, text, sources = [], skipped = [], aiJudgment = null) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.innerHTML = role === "assistant" ? `<strong>Brind Mentor</strong>\n${escapeHtml(text)}` : escapeHtml(text);

  if (aiJudgment?.topic) {
    const judgment = document.createElement("div");
    judgment.className = "source topic";
    const coverage = aiJudgment.noteCoverage?.level || "unknown";
    judgment.textContent = `AI topic: ${aiJudgment.topic} - confidence: ${aiJudgment.confidence || "low"} - note coverage: ${coverage}`;
    node.appendChild(judgment);
  }

  if (sources.length) {
    const list = document.createElement("div");
    list.className = "sources";
    for (const source of sources) {
      list.appendChild(renderSource(source));
    }
    node.appendChild(list);
  }

  if (skipped.length) {
    const list = document.createElement("details");
    list.className = "skipped";
    list.innerHTML = `<summary>Skipped files: ${skipped.length}</summary>`;
    for (const file of skipped.slice(0, 10)) {
      const item = document.createElement("div");
      item.textContent = `${file.path || file.name}: ${file.reason}`;
      list.appendChild(item);
    }
    node.appendChild(list);
  }

  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
}

function renderSource(source) {
  const card = document.createElement("details");
  card.className = "source source-card";
  card.open = true;

  const summary = document.createElement("summary");
  summary.textContent = `[${source.citation}] ${source.title || source.label || "Protected source"}`;
  card.appendChild(summary);

  if (source.source) {
    const path = document.createElement("div");
    path.className = "source-path";
    path.textContent = source.source;
    card.appendChild(path);
  }

  if (source.modifiedTime) {
    const modified = document.createElement("div");
    modified.className = "source-meta";
    modified.textContent = `Modified: ${source.modifiedTime}`;
    card.appendChild(modified);
  }

  if (source.excerpt) {
    const excerpt = document.createElement("p");
    excerpt.className = "source-excerpt";
    excerpt.textContent = truncateText(source.excerpt, SOURCE_EXCERPT_CHARS);
    card.appendChild(excerpt);
  }

  return card;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function readJsonResponse(res, fallbackMessage) {
  const text = await res.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(`${fallbackMessage}: HTTP ${res.status}. ${text.slice(0, 180)}`);
    }
  }
  if (!res.ok || data.error) {
    throw new Error(data.error || `${fallbackMessage}: HTTP ${res.status}`);
  }
  return data;
}

function updateAuthControls(data) {
  sourceState = data;
  tokenStatus.textContent = data.hasGoogleToken ? "Connected" : "Not connected";
  oauthStatus.textContent = data.hasGoogleOAuthConfig ? "Available" : "Not configured";
  openaiStatus.textContent = data.hasOpenAIKey ? "Configured" : "Missing";
  storageStatus.textContent = data.storesLocalDocuments ? "Yes" : "No";

  connectDrive.hidden = data.hasGoogleToken || !data.hasGoogleOAuthConfig;
  disconnectDrive.hidden = !data.hasGoogleToken || data.hasGoogleEnvToken || data.hasGoogleServiceAccount;
  updateIndexControls(data.driveIndex);
}

function updateAccessControls(data) {
  accessState = data;
  accessStatus.textContent = data.enabled ? (data.granted ? "Unlocked" : "Locked") : "Open";
  accessPanel.hidden = !data.enabled || data.granted;
}

function updateHealthControls(data) {
  healthState = data;
  healthStatus.textContent = data.ok ? "Ready" : "Setup needed";
  healthMessage.textContent = data.ok ? "Drive and AI configuration look ready." : (data.actions || []).join(" ");
  updateIndexControls(data.config?.driveIndex);
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  const value = Math.max(0, Number(seconds) || 0);
  if (value < 60) return `${value}s`;
  const minutes = Math.floor(value / 60);
  const remainder = value % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function formatMs(ms) {
  if (ms === null || ms === undefined) return "-";
  const value = Number(ms) || 0;
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(1)}s`;
}

function renderTiming(timings) {
  if (!timings) return "";
  return `Timing: total ${formatMs(timings.totalMs)}; index ${formatMs(timings.indexMs)}; rank ${formatMs(timings.rankMs)}; judge ${formatMs(timings.judgeMs)}; answer ${formatMs(timings.answerMs)}`;
}

function renderIndexDelta(stats) {
  if (!stats || Object.keys(stats).length === 0) return "-";
  const read = stats.readFiles ?? 0;
  const reused = stats.reusedFiles ?? 0;
  const changed = stats.changedFiles ?? 0;
  const removed = stats.removedFiles ?? 0;
  const failed = stats.unsupportedOrFailedFiles ?? 0;
  return `read ${read} / reused ${reused} / changed ${changed} / removed ${removed} / skipped ${failed}`;
}

function updateIndexControls(index) {
  if (!index) return;
  indexStatus.textContent = index.refreshRunning ? "Building" : (index.cached ? "Cached" : "Empty");
  indexRefreshIn.textContent = index.cached ? formatDuration(index.expiresInSeconds) : "-";
  indexDelta.textContent = renderIndexDelta(index.refreshRunning ? index.refreshProgress : index.refreshStats);
  scannedCount.textContent = index.scannedFiles ?? "-";
  chunkCount.textContent = index.textChunks ?? "-";
  skippedCount.textContent = index.skippedFiles ?? "-";
  refreshIndex.disabled = Boolean(index.refreshRunning);
  if (index.refreshError) {
    healthStatus.textContent = "Setup needed";
    healthMessage.textContent = index.refreshError;
  }
}

async function loadAccessStatus() {
  const res = await fetch("/api/access/status");
  const data = await readJsonResponse(res, "Access status failed");
  updateAccessControls(data);
  return data;
}

async function loadHealth() {
  const res = await fetch("/api/health");
  const data = await readJsonResponse(res, "Health check failed");
  updateHealthControls(data);
  return data;
}

async function loadSources() {
  const res = await fetch("/api/sources");
  const data = await readJsonResponse(res, "Source status failed");
  updateAuthControls(data);
}

async function loadIndexStatus() {
  if (accessState.enabled && !accessState.granted) return null;
  const res = await fetch("/api/index/status");
  const data = await readJsonResponse(res, "Index status failed");
  updateIndexControls(data);
  return data;
}

function stopIndexPolling() {
  if (indexPollTimer) {
    window.clearInterval(indexPollTimer);
    indexPollTimer = null;
  }
}

function startIndexPolling() {
  stopIndexPolling();
  indexPollTimer = window.setInterval(async () => {
    try {
      const status = await loadIndexStatus();
      if (!status?.refreshRunning) {
        stopIndexPolling();
        await loadHealth();
      }
    } catch (error) {
      stopIndexPolling();
      healthStatus.textContent = "Setup needed";
      healthMessage.textContent = error.message;
    }
  }, 2000);
}

unlockApp.addEventListener("click", async () => {
  accessError.textContent = "";
  unlockApp.disabled = true;
  try {
    const res = await fetch("/api/access/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: accessCodeInput.value.trim() })
    });
    const data = await readJsonResponse(res, "Unlock failed");
    accessCodeInput.value = "";
    await loadAccessStatus();
    await loadSources();
    await loadHealth();
  } catch (error) {
    accessError.textContent = error.message;
  } finally {
    unlockApp.disabled = false;
  }
});

accessCodeInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    unlockApp.click();
  }
});

connectDrive.addEventListener("click", () => {
  if (accessState.enabled && !accessState.granted) {
    accessCodeInput.focus();
    return;
  }
  window.location.href = "/api/auth/google/start";
});

disconnectDrive.addEventListener("click", async () => {
  await fetch("/api/auth/google/logout", { method: "POST" });
  await loadSources();
  await loadHealth();
});

refreshIndex.addEventListener("click", async () => {
  if (accessState.enabled && !accessState.granted) {
    accessCodeInput.focus();
    return;
  }

  refreshIndex.disabled = true;
  try {
    const res = await fetch("/api/index/refresh", { method: "POST" });
    const data = await readJsonResponse(res, "Refresh failed");
    updateIndexControls(data.cache);
    healthStatus.textContent = "Ready";
    healthMessage.textContent = data.started ? "Building the Drive index in the background." : "Drive index is already building.";
    startIndexPolling();
  } catch (error) {
    healthStatus.textContent = "Setup needed";
    healthMessage.textContent = error.message;
  } finally {
    if (!indexPollTimer) refreshIndex.disabled = false;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  if (accessState.enabled && !accessState.granted) {
    addMessage("assistant", "This app is locked. Enter the access code in the sidebar first.");
    accessCodeInput.focus();
    return;
  }

  if (sourceState && !sourceState.hasGoogleToken) {
    addMessage("assistant", "Google Drive is not connected yet. Set GOOGLE_ACCESS_TOKEN or use the Connect Google Drive button in the sidebar.");
    return;
  }

  input.value = "";
  form.querySelector("button").disabled = true;
  addMessage("user", text);
  addMessage("assistant", "Working on it. I am checking the in-memory Drive index, selecting sources, and asking the model.");

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, mode: mode.value }),
      signal: controller.signal
    });
    const data = await readJsonResponse(res, "Chat failed");
    updateIndexControls(data.stats?.cache || {
      cached: true,
      scannedFiles: data.stats?.scannedFiles,
      textChunks: data.stats?.textChunks,
      skippedFiles: data.stats?.skippedFiles
    });
    const timingLine = renderTiming(data.stats?.timings);
    const footer = timingLine ? `Mode: ${data.answerMode || mode.value}\nModel: ${data.model}\n${timingLine}` : `Mode: ${data.answerMode || mode.value}\nModel: ${data.model}`;
    addMessage("assistant", `${data.answer}\n\n${footer}`, data.sources, data.skipped, data.aiJudgment);
  } catch (error) {
    const message = error.name === "AbortError"
      ? "Chat timed out after 75 seconds. The backend may still be waiting on Google Drive or OpenAI. Try Refresh, then send again."
      : `Error: ${error.message}`;
    addMessage("assistant", message);
  } finally {
    window.clearTimeout(timeoutId);
    form.querySelector("button").disabled = false;
    input.focus();
  }
});

loadAccessStatus()
  .then(loadSources)
  .then(loadHealth)
  .then(loadIndexStatus)
  .then((status) => {
    if (status?.refreshRunning) startIndexPolling();
  });
