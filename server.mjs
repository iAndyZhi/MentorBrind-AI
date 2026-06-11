import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const portArgIndex = process.argv.indexOf("--port");
const PORT = Number(
  portArgIndex >= 0 && process.argv[portArgIndex + 1]
    ? process.argv[portArgIndex + 1]
    : process.env.PORT || 4173
);

const DRIVE_FOLDER_ID = process.env.GOOGLE_DRIVE_FOLDER_ID || "1qSD6wwFWTaJtZLVZ-pEHnLOjJXJbS8OC";
const GOOGLE_ACCESS_TOKEN = process.env.GOOGLE_ACCESS_TOKEN || "";
const OPENAI_API_KEY = process.env.OPENAI_API_KEY || "";
const OPENAI_MODEL = process.env.OPENAI_MODEL || "gpt-5.4-mini";
const MAX_FILES_PER_QUERY = Number(process.env.MAX_FILES_PER_QUERY || 120);
const MAX_CHUNKS_FOR_MODEL = Number(process.env.MAX_CHUNKS_FOR_MODEL || 8);

const MIME_FOLDER = "application/vnd.google-apps.folder";
const MIME_DOC = "application/vnd.google-apps.document";
const TEXT_MIMES = new Set(["text/plain", "text/markdown"]);

function tokenize(text) {
  const normalized = String(text).toLowerCase();
  const wordTokens = normalized
    .replace(/[^\p{Letter}\p{Number}]+/gu, " ")
    .split(/\s+/)
    .filter((token) => token.length > 1);
  const cjkChars = Array.from(normalized.matchAll(/\p{Script=Han}/gu)).map((match) => match[0]);
  const cjkBigrams = cjkChars.slice(0, -1).map((char, index) => `${char}${cjkChars[index + 1]}`);
  return Array.from(new Set([...wordTokens, ...cjkBigrams]));
}

function driveUrl(pathname, params = {}) {
  const url = new URL(`https://www.googleapis.com/drive/v3/${pathname}`);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) url.searchParams.set(key, value);
  }
  return url;
}

async function driveFetch(url) {
  if (!GOOGLE_ACCESS_TOKEN) {
    throw new Error("Missing GOOGLE_ACCESS_TOKEN. Set a Google OAuth access token with Drive read scope.");
  }

  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${GOOGLE_ACCESS_TOKEN}` }
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Google Drive API ${response.status}: ${detail}`);
  }

  return response;
}

async function listChildren(parentId) {
  const files = [];
  let pageToken = null;

  do {
    const url = driveUrl("files", {
      q: `'${parentId}' in parents and trashed = false`,
      fields: "nextPageToken, files(id, name, mimeType, modifiedTime, size)",
      pageToken,
      pageSize: "1000",
      supportsAllDrives: "true",
      includeItemsFromAllDrives: "true"
    });
    const response = await driveFetch(url);
    const data = await response.json();
    files.push(...(data.files || []));
    pageToken = data.nextPageToken;
  } while (pageToken);

  return files;
}

async function listDriveTree(parentId, relativePath = "") {
  const children = await listChildren(parentId);
  const files = [];

  for (const file of children) {
    const filePath = relativePath ? `${relativePath}/${file.name}` : file.name;
    if (file.mimeType === MIME_FOLDER) {
      files.push(...(await listDriveTree(file.id, filePath)));
    } else {
      files.push({ ...file, path: filePath });
    }

    if (files.length >= MAX_FILES_PER_QUERY) break;
  }

  return files.slice(0, MAX_FILES_PER_QUERY);
}

async function exportFileText(file) {
  if (file.mimeType === MIME_DOC) {
    const url = driveUrl(`files/${file.id}/export`, { mimeType: "text/plain" });
    return await (await driveFetch(url)).text();
  }

  if (TEXT_MIMES.has(file.mimeType) || file.name.endsWith(".md") || file.name.endsWith(".txt")) {
    const url = driveUrl(`files/${file.id}`, {
      alt: "media",
      supportsAllDrives: "true"
    });
    return await (await driveFetch(url)).text();
  }

  return null;
}

function splitIntoChunks(file, content) {
  const normalized = content.replace(/\r\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  if (!normalized) return [];

  const paragraphs = normalized.split(/\n\s*\n/);
  const chunks = [];
  let buffer = "";
  let index = 0;

  for (const paragraph of paragraphs) {
    if ((buffer + "\n\n" + paragraph).length > 1800 && buffer) {
      chunks.push(makeChunk(file, buffer, index++));
      buffer = paragraph;
    } else {
      buffer = buffer ? `${buffer}\n\n${paragraph}` : paragraph;
    }
  }

  if (buffer) chunks.push(makeChunk(file, buffer, index));
  return chunks;
}

function makeChunk(file, content, index) {
  return {
    id: `${file.id}#${index}`,
    title: file.name,
    source: file.path,
    driveId: file.id,
    modifiedTime: file.modifiedTime,
    topic: inferTopic(content),
    content
  };
}

function inferTopic(content) {
  const text = content.slice(0, 1200);
  if (/股票|交易|金融|市场|美股|投资|仓位/.test(text)) return "股票金融";
  if (/医学|免疫|药|疾病|医院|癌|肿瘤|细胞/.test(text)) return "医学医药";
  if (/心理|创伤|情绪|自我|意识|欲望/.test(text)) return "心理认知";
  if (/AI|Claude|OpenAI|DeepSeek|模型/.test(text)) return "AI";
  if (/美国|中国|帝国|民主|法治|战争/.test(text)) return "政治历史";
  return "未分类";
}

function scoreChunks(query, chunks, limit = MAX_CHUNKS_FOR_MODEL) {
  const terms = tokenize(query);
  const rawQuery = String(query).toLowerCase();
  const topicHints = [
    { topic: "AI", words: ["ai", "claude", "openai", "deepseek", "模型", "谄媚", "顺从"] },
    { topic: "医学", words: ["医学", "药", "用药", "医院", "免疫", "疾病", "癌", "肿瘤"] },
    { topic: "股票金融", words: ["股票", "金融", "交易", "市场", "美股", "投资", "仓位"] },
    { topic: "心理", words: ["心理", "情绪", "创伤", "自我", "意识", "欲望"] },
    { topic: "政治历史", words: ["美国", "中国", "帝国", "民主", "战争", "民粹"] }
  ];

  return chunks
    .map((item) => {
      const haystack = `${item.title || ""} ${item.topic || ""} ${item.content}`.toLowerCase();
      const termScore = terms.reduce((score, term) => score + (haystack.includes(term) ? 2 : 0), 0);
      const phraseScore = rawQuery && haystack.includes(rawQuery) ? 8 : 0;
      const hintScore = topicHints.reduce((score, hint) => {
        const queryMatches = hint.words.some((word) => rawQuery.includes(word.toLowerCase()));
        const itemMatches = `${item.topic || ""} ${item.content}`.toLowerCase().includes(hint.topic.toLowerCase());
        return score + (queryMatches && itemMatches ? 8 : 0);
      }, 0);
      return { item, score: termScore + phraseScore + hintScore };
    })
    .filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(({ item, score }) => ({ ...item, score }));
}

async function collectDriveContext(query) {
  const files = await listDriveTree(DRIVE_FOLDER_ID);
  const skipped = [];
  const chunks = [];

  for (const file of files) {
    try {
      const text = await exportFileText(file);
      if (!text) {
        skipped.push({ name: file.name, path: file.path, mimeType: file.mimeType, reason: "Unsupported live export type" });
        continue;
      }
      chunks.push(...splitIntoChunks(file, text));
    } catch (error) {
      skipped.push({ name: file.name, path: file.path, mimeType: file.mimeType, reason: error.message });
    }
  }

  return {
    scannedFiles: files.length,
    textChunks: chunks.length,
    skipped,
    matches: scoreChunks(query, chunks)
  };
}

function fallbackAnswer(query, matches, skipped) {
  if (!matches.length) {
    return [
      "我已经实时读取了 Google Drive，但没有找到足够相关的可导出文本片段。",
      skipped.length ? `有 ${skipped.length} 个文件因为格式原因未参与本次检索。` : "",
      "如果关键资料在 PDF、DOCX、图片或扫描件里，建议后续接 Google Drive 转换、OCR，或改用云端向量库。"
    ].filter(Boolean).join("\n");
  }

  const themes = matches.map((m) => m.topic).filter(Boolean).slice(0, 4).join("、");
  return [
    `从实时读取的 Drive 笔记看，这个问题主要落在：${themes || "相关课程片段"}。`,
    "",
    "我会先抓住底层判断：不要急着站队，也不要只看眼前利益，而是回到机制、约束和风险。Brind 的表达习惯里，一个常见动作是先拆掉直觉，再问“这个现象背后的动力是什么”。",
    "",
    `针对你的问题：“${query}”，当前未配置 OpenAI API，所以这里只返回本地检索式摘要。配置 OPENAI_API_KEY 后，会基于这些实时 Drive 片段生成完整 mentor 式回答。`
  ].join("\n");
}

function buildPrompt(query, matches) {
  const context = matches
    .map((m, index) => {
      return [
        `[${index + 1}] ${m.title || "Untitled"}`,
        `source: ${m.source || "unknown"}`,
        `modified: ${m.modifiedTime || "unknown"}`,
        `topic: ${m.topic || "unknown"}`,
        m.content
      ].join("\n");
    })
    .join("\n\n---\n\n");

  return [
    "你是一个基于 Brind 课程笔记构建的私人 AI mentor。",
    "你不能声称自己就是 Brind。你要参考资料中的观点、语言节奏和思考方式，但必须保持诚实。",
    "回答规则：",
    "1. 先给结论，再解释推理链。",
    "2. 如果资料不足，明确说不足，不要编造。",
    "3. 股票、金融、医疗问题只能做教育性分析，不能给确定性买卖或用药指令。",
    "4. 回答末尾列出引用来源编号。",
    "",
    `用户问题：${query}`,
    "",
    "实时从 Google Drive 读取到的资料：",
    context || "无"
  ].join("\n");
}

async function callOpenAI(query, matches) {
  if (!OPENAI_API_KEY) return null;

  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${OPENAI_API_KEY}`
    },
    body: JSON.stringify({
      model: OPENAI_MODEL,
      input: buildPrompt(query, matches)
    })
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`OpenAI API error ${response.status}: ${detail}`);
  }

  const data = await response.json();
  if (data.output_text) return data.output_text;

  return (data.output || [])
    .flatMap((part) => part.content || [])
    .map((content) => content.text || "")
    .filter(Boolean)
    .join("\n");
}

function sendJson(res, value, status = 200) {
  const body = JSON.stringify(value);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body)
  });
  res.end(body);
}

async function sendStatic(res, requestPath) {
  const normalized = requestPath === "/" ? "/index.html" : requestPath;
  const target = path.join(__dirname, "public", normalized);
  if (!target.startsWith(path.join(__dirname, "public"))) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  try {
    const file = await readFile(target);
    const ext = path.extname(target);
    const type = {
      ".html": "text/html; charset=utf-8",
      ".css": "text/css; charset=utf-8",
      ".js": "text/javascript; charset=utf-8"
    }[ext] || "application/octet-stream";
    res.writeHead(200, { "Content-Type": type });
    res.end(file);
  } catch {
    res.writeHead(404);
    res.end("Not found");
  }
}

createServer(async (req, res) => {
  const url = new URL(req.url || "/", `http://${req.headers.host}`);

  try {
    if (req.method === "GET" && url.pathname === "/api/sources") {
      sendJson(res, {
        mode: "drive-live-stateless",
        folderId: DRIVE_FOLDER_ID,
        hasGoogleToken: Boolean(GOOGLE_ACCESS_TOKEN),
        hasOpenAIKey: Boolean(OPENAI_API_KEY),
        storesLocalDocuments: false
      });
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/chat") {
      let body = "";
      for await (const chunk of req) body += chunk;
      const { message } = JSON.parse(body || "{}");
      const context = await collectDriveContext(message || "");
      const modelAnswer = await callOpenAI(message || "", context.matches);
      sendJson(res, {
        answer: modelAnswer || fallbackAnswer(message || "", context.matches, context.skipped),
        sources: context.matches,
        skipped: context.skipped.slice(0, 20),
        stats: {
          scannedFiles: context.scannedFiles,
          textChunks: context.textChunks,
          skippedFiles: context.skipped.length
        },
        model: modelAnswer ? OPENAI_MODEL : "drive-live-demo"
      });
      return;
    }

    if (req.method === "GET") {
      await sendStatic(res, decodeURIComponent(url.pathname));
      return;
    }

    res.writeHead(405);
    res.end("Method not allowed");
  } catch (error) {
    sendJson(res, { error: error.message }, 500);
  }
}).listen(PORT, () => {
  console.log(`Brind Mentor Drive Live running at http://localhost:${PORT}`);
});
