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
    judgment.className = "source";
    judgment.textContent = `AI 判断主题：${aiJudgment.topic} · 置信度：${aiJudgment.confidence || "low"}`;
    node.appendChild(judgment);
  }

  if (sources.length) {
    const list = document.createElement("div");
    list.className = "sources";
    for (const source of sources) {
      const item = document.createElement("div");
      item.className = "source";
      item.textContent = source.source || source.title || "来源";
      list.appendChild(item);
    }
    node.appendChild(list);
  }

  if (skipped.length) {
    const list = document.createElement("details");
    list.className = "skipped";
    list.innerHTML = `<summary>跳过文件 ${skipped.length} 个</summary>`;
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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function updateAuthControls(data) {
  sourceState = data;
  tokenStatus.textContent = data.hasGoogleToken ? "已连接" : "未连接";
  oauthStatus.textContent = data.hasGoogleOAuthConfig ? "可用" : "未配置";
  openaiStatus.textContent = data.hasOpenAIKey ? "已配置" : "未配置";
  storageStatus.textContent = data.storesLocalDocuments ? "是" : "否";

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
    addMessage("assistant", "还没有连接 Google Drive。请先配置 GOOGLE_ACCESS_TOKEN，或使用左侧按钮完成 Google OAuth 登录。");
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
    addMessage("assistant", `${data.answer}\n\n模型：${data.model}`, data.sources, data.skipped, data.aiJudgment);
  } catch (error) {
    addMessage("assistant", `出错了：${error.message}`);
  } finally {
    form.querySelector("button").disabled = false;
    input.focus();
  }
});

loadSources();
