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
MAX_CANDIDATES_FOR_AI = int(os.getenv("MAX_CANDIDATES_FOR_AI", "30"))

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


def openai_request(input_text: str) -> str:
    if not OPENAI_API_KEY:
        raise AppError("Missing OPENAI_API_KEY. AI judgment requires an OpenAI API key.")

    payload = json.dumps({"model": OPENAI_MODEL, "input": input_text}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=payload,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
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


def make_chunk(file: dict[str, Any], content: str, index: int) -> dict[str, Any]:
    return {
        "id": f"{file['id']}#{index}",
        "title": file["name"],
        "source": file["path"],
        "driveId": file["id"],
        "modifiedTime": file.get("modifiedTime"),
        "content": content,
    }


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


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    word_tokens = [token for token in re.split(r"[^\w]+", normalized, flags=re.UNICODE) if len(token) > 1]
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    cjk_bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    return list(dict.fromkeys([*word_tokens, *cjk_bigrams]))


def rough_rank_chunks(query: str, chunks: list[dict[str, Any]], limit: int = MAX_CANDIDATES_FOR_AI) -> list[dict[str, Any]]:
    """Only a coarse candidate reducer. It does not judge topic."""
    terms = tokenize(query)
    raw_query = query.lower()
    scored = []
    for item in chunks:
        haystack = f"{item.get('title', '')} {item.get('content', '')}".lower()
        term_score = sum(2 for term in terms if term in haystack)
        phrase_score = 8 if raw_query and raw_query in haystack else 0
        score = term_score + phrase_score
        if score > 0:
            scored.append({**item, "roughScore": score})
    if not scored:
        scored = [{**item, "roughScore": 0} for item in chunks[:limit]]
    return sorted(scored, key=lambda item: item["roughScore"], reverse=True)[:limit]


def parse_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def ai_select_context(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        fallback = candidates[:MAX_CHUNKS_FOR_MODEL]
        for item in fallback:
            item["aiTopic"] = "AI未配置，未判断主题"
        return {
            "topic": "AI未配置，未判断主题",
            "confidence": "low",
            "selected": fallback,
            "reason": "OPENAI_API_KEY is not set; using coarse text match only.",
        }

    compact_candidates = [
        {
            "id": item["id"],
            "title": item["title"],
            "source": item["source"],
            "excerpt": item["content"][:900],
        }
        for item in candidates
    ]
    prompt = "\n".join([
        "你是知识库检索裁判。不要用关键词粗暴判断主题，要根据语义、问题意图和候选片段内容来判断。",
        "任务：从候选片段中选择最多 8 个真正能回答用户问题的片段，并给出这个问题的自然语言主题。",
        "如果候选片段都不相关，selected_ids 返回空数组。",
        "只返回 JSON，不要 Markdown。",
        "JSON 格式：{\"topic\":\"...\",\"confidence\":\"high|medium|low\",\"selected_ids\":[\"...\"],\"reason\":\"...\"}",
        "",
        f"用户问题：{query}",
        "",
        "候选片段：",
        json.dumps(compact_candidates, ensure_ascii=False),
    ])
    try:
        judgment = parse_json_object(openai_request(prompt))
    except Exception as exc:
        fallback = candidates[:MAX_CHUNKS_FOR_MODEL]
        for item in fallback:
            item["aiTopic"] = "AI判断失败"
        return {
            "topic": "AI判断失败",
            "confidence": "low",
            "selected": fallback,
            "reason": str(exc),
        }

    selected_ids = set(judgment.get("selected_ids", []))
    selected = [item for item in candidates if item["id"] in selected_ids]
    if not selected and candidates:
        selected = candidates[: min(3, MAX_CHUNKS_FOR_MODEL)]
    topic = judgment.get("topic") or "未判断"
    for item in selected:
        item["aiTopic"] = topic
    return {
        "topic": topic,
        "confidence": judgment.get("confidence", "low"),
        "selected": selected[:MAX_CHUNKS_FOR_MODEL],
        "reason": judgment.get("reason", ""),
    }


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

    candidates = rough_rank_chunks(query, chunks)
    ai_judgment = ai_select_context(query, candidates)
    return {
        "scannedFiles": len(files),
        "textChunks": len(chunks),
        "skipped": skipped,
        "matches": ai_judgment["selected"],
        "aiJudgment": {
            "topic": ai_judgment["topic"],
            "confidence": ai_judgment["confidence"],
            "reason": ai_judgment["reason"],
        },
    }


def fallback_answer(query: str, matches: list[dict[str, Any]], skipped: list[dict[str, str]], ai_judgment: dict[str, Any]) -> str:
    if not OPENAI_API_KEY:
        return "\n".join([
            "当前没有配置 OPENAI_API_KEY，所以我不能让 AI 判断主题或生成最终回答。",
            "我只做了粗略候选片段筛选，不会再声明这个问题属于某个主题。",
            f"候选片段数：{len(matches)}；跳过文件数：{len(skipped)}。",
            "配置 OpenAI key 后，系统会先让 AI 判断主题和引用片段，再生成回答。",
        ])

    if not matches:
        lines = ["AI 判断后认为没有找到足够相关的 Drive 片段。"]
        if skipped:
            lines.append(f"有 {len(skipped)} 个文件因为格式或解析原因未参与本次检索。")
        return "\n".join(lines)

    return "\n".join([
        f"AI 判断主题：{ai_judgment.get('topic', '未判断')}（置信度：{ai_judgment.get('confidence', 'low')}）",
        "",
        "已选出相关来源，但最终回答生成失败或未返回文本。请稍后重试。",
    ])


def build_answer_prompt(query: str, matches: list[dict[str, Any]], ai_judgment: dict[str, Any]) -> str:
    context = "\n\n---\n\n".join(
        "\n".join([
            f"[{index + 1}] {item.get('title', 'Untitled')}",
            f"source: {item.get('source', 'unknown')}",
            f"modified: {item.get('modifiedTime', 'unknown')}",
            item.get("content", ""),
        ])
        for index, item in enumerate(matches)
    )
    return "\n".join([
        "你是一个基于 Brind 课程笔记构建的私人 AI mentor。",
        "你不能声称自己就是 Brind。你要参考资料中的观点、语言节奏和思考方式，但必须保持诚实。",
        "不要根据关键词武断分类。主题判断以之前 AI 裁判结果为准。",
        "回答规则：",
        "1. 先给结论，再解释推理链。",
        "2. 如果资料不足，明确说不足，不要编造。",
        "3. 股票、金融、医疗问题只能做教育性分析，不能给确定性买卖或用药指令。",
        "4. 回答末尾列出引用来源编号。",
        "",
        f"AI 判断主题：{ai_judgment.get('topic', '未判断')}",
        f"用户问题：{query}",
        "",
        "实时从 Google Drive 读取并由 AI 选择的资料：",
        context or "无",
    ])


def openai_answer(query: str, matches: list[dict[str, Any]], ai_judgment: dict[str, Any]) -> str | None:
    if not OPENAI_API_KEY:
        return None
    return openai_request(build_answer_prompt(query, matches, ai_judgment))


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
                "mode": "python-drive-live-ai-judged",
                "folderId": DRIVE_FOLDER_ID,
                "hasGoogleToken": bool(GOOGLE_ACCESS_TOKEN),
                "hasOpenAIKey": bool(OPENAI_API_KEY),
                "storesLocalDocuments": False,
                "topicJudgment": "openai" if OPENAI_API_KEY else "disabled",
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
            answer = openai_answer(message, context["matches"], context["aiJudgment"]) or fallback_answer(
                message,
                context["matches"],
                context["skipped"],
                context["aiJudgment"],
            )
            json_response(self, {
                "answer": answer,
                "sources": context["matches"],
                "skipped": context["skipped"][:20],
                "aiJudgment": context["aiJudgment"],
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
