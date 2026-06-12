const messages = document.querySelector("#messages");
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const tokenStatus = document.querySelector("#tokenStatus");
const oauthStatus = document.querySelector("#oauthStatus");
const openaiStatus = document.querySelector("#openaiStatus");
const storageStatus = document.querySelector("#storageStatus");
const scannedCount = document.querySelector("#scannedCount");
const chunkCount = document.querySelector("#chunkCount");
const skippedCount = document.querySelector("#skippedCount");
const mode = document.querySelector("#mode");
const connectDrive = document.querySelector("#connectDrive");
const disconnectDrive = document.querySelector("#disconnectDrive");

let sourceState = null;

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
  summary.textContent = `[${source.citation}] ${source.title || "Untitled"}`;
  card.appendChild(summary);

  const path = document.createElement("div");
  path.className = "source-path";
  path.textContent = source.source || "unknown source";
  card.appendChild(path);

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
}

async function loadSources() {
  const res = await fetch("/api/sources");
  const data = await res.json();
  updateAuthControls(data);
}

connectDrive.addEventListener("click", () => {
  window.location.href = "/api/auth/google/start";
});

disconnectDrive.addEventListener("click", async () => {
  await fetch("/api/auth/google/logout", { method: "POST" });
  await loadSources();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;

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
    scannedCount.textContent = data.stats?.scannedFiles ?? "-";
    chunkCount.textContent = data.stats?.textChunks ?? "-";
    skippedCount.textContent = data.stats?.skippedFiles ?? "-";
    addMessage("assistant", `${data.answer}\n\nModel: ${data.model}`, data.sources, data.skipped, data.aiJudgment);
  } catch (error) {
    addMessage("assistant", `Error: ${error.message}`);
  } finally {
    form.querySelector("button").disabled = false;
    input.focus();
  }
});

loadSources();
