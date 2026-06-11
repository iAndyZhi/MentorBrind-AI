from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"

PORT = int(os.getenv("PORT", "4173"))
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "1qSD6wwFWTaJtZLVZ-pEHnLOjJXJbS8OC")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
MAX_FILES_PER_QUERY = int(os.getenv("MAX_FILES_PER_QUERY", "160"))
MAX_CHUNKS_FOR_MODEL = int(os.getenv("MAX_CHUNKS_FOR_MODEL", "8"))

MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_DOC = "application/vnd.google-apps.document"
MIME_TEXT = {"text/plain", "text/markdown"}
MIME_PDF = "application/pdf"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_DOC_LEGACY = "application/msword"
MIME_RTF = "application/rtf"


class AppError(Exception):
    pass


def json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def drive_request(path: str, params: dict[str, str | None] | None = None) -> bytes:
    if not GOOGLE_ACCESS_TOKEN:
        raise AppError("Missing GOOGLE_ACCESS_TOKEN. Set a Google OAuth access token with Drive read scope.")

    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"https://www.googleapis.com/drive/v3/{path}"
    if query:
        url = f"{url}?{query}"

    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AppError(f"Google Drive API {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AppError(f"Google Drive connection failed: {exc.reason}") from exc


def list_children(parent_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        payload = drive_request(
            "files",
            {
                "q": f"'{parent_id}' in parents and trashed = false",
                "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                "pageToken": page_token,
                "pageSize": "1000",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )
        data = json.loads(payload.decode("utf-8"))
        files.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            return files


def list_drive_tree(parent_id: str, relative_path: str = "") -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for file in list_children(parent_id):
        file_path = f"{relative_path}/{file['name']}" if relative_path else file["name"]
        if file["mimeType"] == MIME_FOLDER:
            files.extend(list_drive_tree(file["id"], file_path))
        else:
            files.append({**file, "path": file_path})

        if len(files) >= MAX_FILES_PER_QUERY:
            break

    return files[:MAX_FILES_PER_QUERY]


def download_file(file: dict[str, Any]) -> bytes:
    return drive_request(f"files/{file['id']}", {"alt": "media", "supportsAllDrives": "true"})


def export_google_doc(file: dict[str, Any]) -> str:
    payload = drive_request(f"files/{file['id']}/export", {"mimeType": "text/plain"})
    return payload.decode("utf-8", errors="replace")


def extract_docx(data: bytes) -> str:
    lines: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")

    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    for paragraph in root.findall(".//w:p", ns):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            lines.append(text)
    return "\n\n".join(lines)


def extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise AppError("PDF support requires `pip install -r requirements.txt`") from exc

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(page for page in pages if page)


def extract_rtf(data: bytes) -> str:
    raw = data.decode("utf-8", errors="ignore")
    raw = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", raw)
    raw = raw.replace("{", " ").replace("}", " ")
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]{2,}", " ", raw)).strip()


def extract_legacy_doc(data: bytes) -> str:
    utf16 = data.decode("utf-16le", errors="ignore")
    utf16_runs = re.findall(r"[\w\s\u4e00-\u9fff，。！？；：、“”‘’（）《》]{8,}", utf16)
    latin = data.decode("latin-1", errors="ignore")
    latin_runs = re.findall(r"[A-Za-z0-9 ,.;:!?()'\-]{12,}", latin)
    text = "\n".join(run.strip() for run in [*utf16_runs, *latin_runs] if run.strip())
    if len(text) < 80:
        raise AppError("Legacy .doc text extraction failed. Convert to .docx or Google Docs for reliable parsing.")
    return text


def export_file_text(file: dict[str, Any]) -> str | None:
    name = file["name"].lower()
    mime = file["mimeType"]

    if mime == MIME_DOC:
        return export_google_doc(file)
    if mime in MIME_TEXT or name.endswith((".txt", ".md", ".markdown")):
        return download_file(file).decode("utf-8", errors="replace")
    if mime == MIME_DOCX or name.endswith(".docx"):
        return extract_docx(download_file(file))
    if mime == MIME_PDF or name.endswith(".pdf"):
        return extract_pdf(download_file(file))
    if mime == MIME_RTF or name.endswith(".rtf"):
        return extract_rtf(download_file(file))
    if mime == MIME_DOC_LEGACY or name.endswith(".doc"):
        return extract_legacy_doc(download_file(file))
    return None


def infer_topic(content: str) -> str:
    text = content[:1200]
    if re.search(r"股票|交易|金融|市场|美股|投资|仓位", text):
        return "股票金融"
    if re.search(r"医学|免疫|药|疾病|医院|癌|肿瘤|细胞", text):
        return "医学医药"
    if re.search(r"心理|创伤|情绪|自我|意识|欲望", text):
        return "心理认知"
    if re.search(r"AI|Claude|OpenAI|DeepSeek|模型", text, re.IGNORECASE):
        return "AI"
    if re.search(r"美国|中国|帝国|民主|法治|战争", text):
        return "政治历史"
    return "未分类"


def split_into_chunks(file: dict[str, Any], content: str) -> list[dict[str, Any]]:
    normalized = re.sub(r"\n{3,}", "\n\n", content.replace("\r\n", "\n")).strip()
    if not normalized:
        return []

    chunks: list[dict[str, Any]] = []
    buffer = ""
    index = 0
    for paragraph in re.split(r"\n\s*\n", normalized):
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) > 1800 and buffer:
            chunks.append(make_chunk(file, buffer, index))
            index += 1
            buffer = paragraph
        else:
            buffer = candidate
    if buffer:
        chunks.append(make_chunk(file, buffer, index))
    return chunks


def make_chunk(file: dict[str, Any], content: str, index: int) -> dict[str, Any]:
    return {
        "id": f"{file['id']}#{index}",
        "title": file["name"],
        "source": file["path"],
        "driveId": file["id"],
        "modifiedTime": file.get("modifiedTime"),
        "topic": infer_topic(content),
        "content": content,
    }


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    word_tokens = [token for token in re.split(r"[^\w]+", normalized, flags=re.UNICODE) if len(token) > 1]
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    cjk_bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    return list(dict.fromkeys([*word_tokens, *cjk_bigrams]))


def score_chunks(query: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms = tokenize(query)
    raw_query = query.lower()
    topic_hints = [
        {"topic": "AI", "words": ["ai", "claude", "openai", "deepseek", "模型", "谄媚", "顺从"]},
        {"topic": "医学", "words": ["医学", "药", "用药", "医院", "免疫", "疾病", "癌", "肿瘤"]},
        {"topic": "股票金融", "words": ["股票", "金融", "交易", "市场", "美股", "投资", "仓位"]},
        {"topic": "心理", "words": ["心理", "情绪", "创伤", "自我", "意识", "欲望"]},
        {"topic": "政治历史", "words": ["美国", "中国", "帝国", "民主", "战争", "民粹"]},
    ]

    scored = []
    for item in chunks:
        haystack = f"{item.get('title', '')} {item.get('topic', '')} {item.get('content', '')}".lower()
        term_score = sum(2 for term in terms if term in haystack)
        phrase_score = 8 if raw_query and raw_query in haystack else 0
        hint_score = 0
        for hint in topic_hints:
            query_matches = any(word.lower() in raw_query for word in hint["words"])
            item_matches = hint["topic"].lower() in f"{item.get('topic', '')} {item.get('content', '')}".lower()
            if query_matches and item_matches:
                hint_score += 8
        score = term_score + phrase_score + hint_score
        if score > 0:
            scored.append({**item, "score": score})

    return sorted(scored, key=lambda item: item["score"], reverse=True)[:MAX_CHUNKS_FOR_MODEL]


def collect_drive_context(query: str) -> dict[str, Any]:
    files = list_drive_tree(DRIVE_FOLDER_ID)
    skipped: list[dict[str, str]] = []
    chunks: list[dict[str, Any]] = []

    for file in files:
        try:
            text = export_file_text(file)
            if not text:
                skipped.append({"name": file["name"], "path": file["path"], "mimeType": file["mimeType"], "reason": "Unsupported live export type"})
                continue
            chunks.extend(split_into_chunks(file, text))
        except Exception as exc:
            skipped.append({"name": file["name"], "path": file["path"], "mimeType": file["mimeType"], "reason": str(exc)})

    return {
        "scannedFiles": len(files),
        "textChunks": len(chunks),
        "skipped": skipped,
        "matches": score_chunks(query, chunks),
    }


def fallback_answer(query: str, matches: list[dict[str, Any]], skipped: list[dict[str, str]]) -> str:
    if not matches:
        lines = ["我已经实时读取了 Google Drive，但没有找到足够相关的可导出文本片段。"]
        if skipped:
            lines.append(f"有 {len(skipped)} 个文件因为格式或解析原因未参与本次检索。")
        lines.append("如果关键资料在图片或扫描件里，下一步需要接 OCR；PDF/DOCX/RTF 已经尝试在内存中解析。")
        return "\n".join(lines)

    themes = "、".join([item["topic"] for item in matches if item.get("topic")][:4])
    return "\n".join([
        f"从实时读取的 Drive 笔记看，这个问题主要落在：{themes or '相关课程片段'}。",
        "",
        "我会先抓住底层判断：不要急着站队，也不要只看眼前利益，而是回到机制、约束和风险。Brind 的表达习惯里，一个常见动作是先拆掉直觉，再问“这个现象背后的动力是什么”。",
        "",
        f"针对你的问题：“{query}”，当前未配置 OpenAI API，所以这里只返回本地检索式摘要。配置 OPENAI_API_KEY 后，会基于这些实时 Drive 片段生成完整 mentor 式回答。",
    ])


def build_prompt(query: str, matches: list[dict[str, Any]]) -> str:
    context = "\n\n---\n\n".join(
        "\n".join([
            f"[{index + 1}] {item.get('title', 'Untitled')}",
            f"source: {item.get('source', 'unknown')}",
            f"modified: {item.get('modifiedTime', 'unknown')}",
            f"topic: {item.get('topic', 'unknown')}",
            item.get("content", ""),
        ])
        for index, item in enumerate(matches)
    )
    return "\n".join([
        "你是一个基于 Brind 课程笔记构建的私人 AI mentor。",
        "你不能声称自己就是 Brind。你要参考资料中的观点、语言节奏和思考方式，但必须保持诚实。",
        "回答规则：",
        "1. 先给结论，再解释推理链。",
        "2. 如果资料不足，明确说不足，不要编造。",
        "3. 股票、金融、医疗问题只能做教育性分析，不能给确定性买卖或用药指令。",
        "4. 回答末尾列出引用来源编号。",
        "",
        f"用户问题：{query}",
        "",
        "实时从 Google Drive 读取到的资料：",
        context or "无",
    ])


def openai_response(query: str, matches: list[dict[str, Any]]) -> str | None:
    if not OPENAI_API_KEY:
        return None

    payload = json.dumps({"model": OPENAI_MODEL, "input": build_prompt(query, matches)}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=payload,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AppError(f"OpenAI API {exc.code}: {detail}") from exc

    if data.get("output_text"):
        return data["output_text"]
    return "\n".join(
        content.get("text", "")
        for output in data.get("output", [])
        for content in output.get("content", [])
        if content.get("text")
    )


def serve_static(handler: BaseHTTPRequestHandler, request_path: str) -> None:
    normalized = "/index.html" if request_path == "/" else request_path
    target = (PUBLIC / normalized.lstrip("/")).resolve()
    if not str(target).startswith(str(PUBLIC.resolve())):
        handler.send_error(403)
        return
    if not target.exists():
        handler.send_error(404)
        return

    content_type = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(target.suffix, "application/octet-stream")
    body = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/sources":
            json_response(self, {
                "mode": "python-drive-live-stateless",
                "folderId": DRIVE_FOLDER_ID,
                "hasGoogleToken": bool(GOOGLE_ACCESS_TOKEN),
                "hasOpenAIKey": bool(OPENAI_API_KEY),
                "storesLocalDocuments": False,
                "supports": ["Google Docs", "txt", "markdown", "pdf", "docx", "rtf", "legacy doc best-effort"],
            })
            return
        serve_static(self, urllib.parse.urlparse(self.path).path)

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            message = json.loads(body or "{}").get("message", "")
            context = collect_drive_context(message)
            answer = openai_response(message, context["matches"]) or fallback_answer(message, context["matches"], context["skipped"])
            json_response(self, {
                "answer": answer,
                "sources": context["matches"],
                "skipped": context["skipped"][:20],
                "stats": {
                    "scannedFiles": context["scannedFiles"],
                    "textChunks": context["textChunks"],
                    "skippedFiles": len(context["skipped"]),
                },
                "model": OPENAI_MODEL if OPENAI_API_KEY else "python-drive-live-demo",
            })
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def log_message(self, format: str, *args: Any) -> None:
        return


if __name__ == "__main__":
    print(f"Brind Mentor Python backend running at http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
