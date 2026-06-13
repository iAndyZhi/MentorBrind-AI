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

function addMessage(role, text, sources = [], skipped = [], aiJudgment = null) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.innerHTML = role === "assistant" ? `<strong>Brind Mentor</strong>\n${escapeHtml(text)}` : escapeHtml(text);

  if (aiJudgment?.topic) {
    const judgment = document.createElement("div");
    judgment.className = "source topic";
    judgment.textContent = `AI topic: ${aiJudgment.topic} - confidence: ${aiJudgment.confidence || "low"}`;
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
    excerpt.textContent = source.excerpt;
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

function updateAuthControls(data) {
  sourceState = data;
  tokenStatus.textContent = data.hasGoogleToken ? "Connected" : "Not connected";
  oauthStatus.textContent = data.hasGoogleOAuthConfig ? "Available" : "Not configured";
  openaiStatus.textContent = data.hasOpenAIKey ? "Configured" : "Missing";
  storageStatus.textContent = data.storesLocalDocuments ? "Yes" : "No";

  connectDrive.hidden = data.hasGoogleToken || !data.hasGoogleOAuthConfig;
  disconnectDrive.hidden = !data.hasGoogleToken || data.hasGoogleEnvToken;
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

function updateIndexControls(index) {
  if (!index) return;
  indexStatus.textContent = index.cached ? "Cached" : "Empty";
  indexRefreshIn.textContent = index.cached ? formatDuration(index.expiresInSeconds) : "-";
  scannedCount.textContent = index.scannedFiles ?? "-";
  chunkCount.textContent = index.textChunks ?? "-";
  skippedCount.textContent = index.skippedFiles ?? "-";
}

async function loadAccessStatus() {
  const res = await fetch("/api/access/status");
  const data = await res.json();
  updateAccessControls(data);
  return data;
}

async function loadHealth() {
  const res = await fetch("/api/health");
  const data = await res.json();
  updateHealthControls(data);
  return data;
}

async function loadSources() {
  const res = await fetch("/api/sources");
  const data = await res.json();
  updateAuthControls(data);
}

async function loadIndexStatus() {
  if (accessState.enabled && !accessState.granted) return null;
  const res = await fetch("/api/index/status");
  const data = await res.json();
  if (res.ok) updateIndexControls(data);
  return data;
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
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Unlock failed.");
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
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Refresh failed.");
    updateIndexControls(data.cache);
    await loadHealth();
  } catch (error) {
    healthStatus.textContent = "Setup needed";
    healthMessage.textContent = error.message;
  } finally {
    refreshIndex.disabled = false;
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

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: `[${mode.value}] ${text}` })
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    updateIndexControls(data.stats?.cache || {
      cached: true,
      scannedFiles: data.stats?.scannedFiles,
      textChunks: data.stats?.textChunks,
      skippedFiles: data.stats?.skippedFiles
    });
    addMessage("assistant", `${data.answer}\n\nModel: ${data.model}`, data.sources, data.skipped, data.aiJudgment);
  } catch (error) {
    addMessage("assistant", `Error: ${error.message}`);
  } finally {
    form.querySelector("button").disabled = false;
    input.focus();
  }
});

loadAccessStatus()
  .then(loadSources)
  .then(loadHealth)
  .then(loadIndexStatus);
