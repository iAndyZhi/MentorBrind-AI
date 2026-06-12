from __future__ import annotations

import http.cookies
import io
import json
import os
import re
import secrets
import time
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
APP_BASE_URL = os.getenv("APP_BASE_URL", f"http://localhost:{PORT}")
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "1qSD6wwFWTaJtZLVZ-pEHnLOjJXJbS8OC")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", f"{APP_BASE_URL}/api/auth/google/callback")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
APP_ACCESS_CODE = os.getenv("APP_ACCESS_CODE", "")
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
SESSION_COOKIE = "mentorbrind_session"
ACCESS_COOKIE = "mentorbrind_access"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", str(30 * 24 * 60 * 60)))

SESSIONS: dict[str, dict[str, Any]] = {}
ACCESS_SESSIONS: dict[str, float] = {}
OAUTH_STATES: dict[str, float] = {}


class AppError(Exception):
    pass


def json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def redirect_response(handler: BaseHTTPRequestHandler, location: str, headers: dict[str, str] | None = None) -> None:
    handler.send_response(302)
    handler.send_header("Location", location)
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()


def parse_cookie(header: str | None) -> dict[str, str]:
    if not header:
        return {}
    cookie = http.cookies.SimpleCookie()
    cookie.load(header)
    return {key: morsel.value for key, morsel in cookie.items()}


def make_cookie(name: str, value: str, max_age: int) -> str:
    cookie = http.cookies.SimpleCookie()
    cookie[name] = value
    cookie[name]["path"] = "/"
    cookie[name]["max-age"] = str(max_age)
    cookie[name]["httponly"] = True
    cookie[name]["samesite"] = "Lax"
    return cookie.output(header="").strip()


def make_session_cookie(session_id: str, max_age: int) -> str:
    return make_cookie(SESSION_COOKIE, session_id, max_age)


def clear_session_cookie() -> str:
    return make_session_cookie("", 0)


def make_access_cookie(access_id: str, max_age: int) -> str:
    return make_cookie(ACCESS_COOKIE, access_id, max_age)


def clear_access_cookie() -> str:
    return make_access_cookie("", 0)


def has_app_access(handler: BaseHTTPRequestHandler) -> bool:
    if not APP_ACCESS_CODE:
        return True
    access_id = parse_cookie(handler.headers.get("Cookie")).get(ACCESS_COOKIE)
    if not access_id:
        return False
    expires_at = ACCESS_SESSIONS.get(access_id, 0)
    if expires_at <= time.time():
        ACCESS_SESSIONS.pop(access_id, None)
        return False
    return True


def require_app_access(handler: BaseHTTPRequestHandler) -> bool:
    if has_app_access(handler):
        return True
    json_response(handler, {"error": "Access code required.", "requiresAccessCode": True}, 401)
    return False


def login_app_access(handler: BaseHTTPRequestHandler) -> None:
    if not APP_ACCESS_CODE:
        json_response(handler, {"ok": True, "enabled": False})
        return

    length = int(handler.headers.get("Content-Length", "0"))
    body = handler.rfile.read(length).decode("utf-8")
    code = json.loads(body or "{}").get("code", "")
    if not secrets.compare_digest(str(code), APP_ACCESS_CODE):
        json_response(handler, {"error": "Invalid access code."}, 401)
        return

    access_id = secrets.token_urlsafe(32)
    ACCESS_SESSIONS[access_id] = time.time() + SESSION_MAX_AGE_SECONDS
    json_response(handler, {"ok": True, "enabled": True}, headers={"Set-Cookie": make_access_cookie(access_id, SESSION_MAX_AGE_SECONDS)})


def logout_app_access(handler: BaseHTTPRequestHandler) -> None:
    access_id = parse_cookie(handler.headers.get("Cookie")).get(ACCESS_COOKIE)
    if access_id:
        ACCESS_SESSIONS.pop(access_id, None)
    json_response(handler, {"ok": True}, headers={"Set-Cookie": clear_access_cookie()})


def session_from_request(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    session_id = parse_cookie(handler.headers.get("Cookie")).get(SESSION_COOKIE)
    if not session_id:
        return None
    session = SESSIONS.get(session_id)
    if not session:
        return None
    session_expires_at = session.get("sessionExpiresAt", 0)
    if session_expires_at and session_expires_at <= time.time():
        SESSIONS.pop(session_id, None)
        return None
    return session


def refresh_google_session(session: dict[str, Any]) -> bool:
    refresh_token = session.get("refreshToken", "")
    if not refresh_token or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return False

    payload = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            token_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return False

    access_token = token_data.get("access_token", "")
    if not access_token:
        return False

    expires_in = int(token_data.get("expires_in", 3600))
    session["accessToken"] = access_token
    session["accessExpiresAt"] = time.time() + max(60, expires_in - 60)
    return True


def access_token_from_request(handler: BaseHTTPRequestHandler) -> str:
    if GOOGLE_ACCESS_TOKEN:
        return GOOGLE_ACCESS_TOKEN
    session = session_from_request(handler)
    if not session:
        return ""
    access_expires_at = session.get("accessExpiresAt", 0)
    if access_expires_at and access_expires_at <= time.time():
        if not refresh_google_session(session):
            return ""
    return session.get("accessToken", "")


def drive_request(access_token: str, path: str, params: dict[str, str | None] | None = None) -> bytes:
    if not access_token:
        raise AppError("Missing Google Drive access. Set GOOGLE_ACCESS_TOKEN or sign in with Google.")

    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"https://www.googleapis.com/drive/v3/{path}"
    if query:
        url = f"{url}?{query}"

    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
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


def start_google_oauth(handler: BaseHTTPRequestHandler) -> None:
    if not require_app_access(handler):
        return
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        json_response(handler, {"error": "Missing GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET."}, 400)
        return

    state = secrets.token_urlsafe(24)
    OAUTH_STATES[state] = time.time() + 600
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    redirect_response(handler, f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}")


def complete_google_oauth(handler: BaseHTTPRequestHandler, query: dict[str, list[str]]) -> None:
    state = query.get("state", [""])[0]
    code = query.get("code", [""])[0]
    state_expiry = OAUTH_STATES.pop(state, 0)
    if not state or not code or state_expiry <= time.time():
        redirect_response(handler, "/?auth=failed")
        return

    payload = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": GOOGLE_REDIRECT_URI,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            token_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError:
        redirect_response(handler, "/?auth=failed")
        return

    access_token = token_data.get("access_token", "")
    if not access_token:
        redirect_response(handler, "/?auth=failed")
        return

    expires_in = int(token_data.get("expires_in", 3600))
    session_id = secrets.token_urlsafe(32)
    SESSIONS[session_id] = {
        "accessToken": access_token,
        "refreshToken": token_data.get("refresh_token", ""),
        "accessExpiresAt": time.time() + max(60, expires_in - 60),
        "sessionExpiresAt": time.time() + SESSION_MAX_AGE_SECONDS,
    }
    redirect_response(handler, "/?auth=ok", {"Set-Cookie": make_session_cookie(session_id, SESSION_MAX_AGE_SECONDS)})


def logout_google(handler: BaseHTTPRequestHandler) -> None:
    session_id = parse_cookie(handler.headers.get("Cookie")).get(SESSION_COOKIE)
    if session_id:
        SESSIONS.pop(session_id, None)
    json_response(handler, {"ok": True}, headers={"Set-Cookie": clear_session_cookie()})


def list_children(access_token: str, parent_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        payload = drive_request(
            access_token,
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


def list_drive_tree(access_token: str, parent_id: str, relative_path: str = "") -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for file in list_children(access_token, parent_id):
        file_path = f"{relative_path}/{file['name']}" if relative_path else file["name"]
        if file["mimeType"] == MIME_FOLDER:
            files.extend(list_drive_tree(access_token, file["id"], file_path))
        else:
            files.append({**file, "path": file_path})
        if len(files) >= MAX_FILES_PER_QUERY:
            break
    return files[:MAX_FILES_PER_QUERY]


def download_file(access_token: str, file: dict[str, Any]) -> bytes:
    return drive_request(access_token, f"files/{file['id']}", {"alt": "media", "supportsAllDrives": "true"})


def export_google_doc(access_token: str, file: dict[str, Any]) -> str:
    payload = drive_request(access_token, f"files/{file['id']}/export", {"mimeType": "text/plain"})
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
    utf16_runs = re.findall(r"[\w\s\u4e00-\u9fff,\.;:!\?\(\)\[\]\-]{8,}", utf16)
    latin = data.decode("latin-1", errors="ignore")
    latin_runs = re.findall(r"[A-Za-z0-9 ,.;:!?()'\-]{12,}", latin)
    text = "\n".join(run.strip() for run in [*utf16_runs, *latin_runs] if run.strip())
    if len(text) < 80:
        raise AppError("Legacy .doc text extraction failed. Convert to .docx or Google Docs for reliable parsing.")
    return text


def export_file_text(access_token: str, file: dict[str, Any]) -> str | None:
    name = file["name"].lower()
    mime = file["mimeType"]

    if mime == MIME_DOC:
        return export_google_doc(access_token, file)
    if mime in MIME_TEXT or name.endswith((".txt", ".md", ".markdown")):
        return download_file(access_token, file).decode("utf-8", errors="replace")
    if mime == MIME_DOCX or name.endswith(".docx"):
        return extract_docx(download_file(access_token, file))
    if mime == MIME_PDF or name.endswith(".pdf"):
        return extract_pdf(download_file(access_token, file))
    if mime == MIME_RTF or name.endswith(".rtf"):
        return extract_rtf(download_file(access_token, file))
    if mime == MIME_DOC_LEGACY or name.endswith(".doc"):
        return extract_legacy_doc(download_file(access_token, file))
    return None


def clean_excerpt(text: str, limit: int = 420) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}..."


def make_chunk(file: dict[str, Any], content: str, index: int) -> dict[str, Any]:
    return {
        "id": f"{file['id']}#{index}",
        "title": file["name"],
        "source": file["path"],
        "driveId": file["id"],
        "modifiedTime": file.get("modifiedTime"),
        "chunkIndex": index,
        "content": content,
        "excerpt": clean_excerpt(content),
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
            item["aiTopic"] = "AI topic judgment disabled"
        return {
            "topic": "AI topic judgment disabled",
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
        "You are a retrieval judge for a private knowledge base.",
        "Do not classify the user topic by hard-coded keywords. Judge semantically from the user intent and candidate snippets.",
        "Pick up to 8 snippets that can truly help answer the user question, and produce a natural-language topic label.",
        "If none of the candidates are relevant, return an empty selected_ids array.",
        "Return JSON only, no Markdown.",
        "Schema: {\"topic\":\"...\",\"confidence\":\"high|medium|low\",\"selected_ids\":[\"...\"],\"reason\":\"...\"}",
        "",
        f"User question: {query}",
        "",
        "Candidate snippets:",
        json.dumps(compact_candidates, ensure_ascii=False),
    ])
    try:
        judgment = parse_json_object(openai_request(prompt))
    except Exception as exc:
        fallback = candidates[:MAX_CHUNKS_FOR_MODEL]
        for item in fallback:
            item["aiTopic"] = "AI judgment failed"
        return {
            "topic": "AI judgment failed",
            "confidence": "low",
            "selected": fallback,
            "reason": str(exc),
        }

    selected_ids = set(judgment.get("selected_ids", []))
    selected = [item for item in candidates if item["id"] in selected_ids]
    if not selected and candidates:
        selected = candidates[: min(3, MAX_CHUNKS_FOR_MODEL)]
    topic = judgment.get("topic") or "Not judged"
    for item in selected:
        item["aiTopic"] = topic
    return {
        "topic": topic,
        "confidence": judgment.get("confidence", "low"),
        "selected": selected[:MAX_CHUNKS_FOR_MODEL],
        "reason": judgment.get("reason", ""),
    }


def public_sources(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for index, item in enumerate(matches):
        sources.append({
            "citation": index + 1,
            "title": item.get("title", "Untitled"),
            "source": item.get("source", "unknown"),
            "driveId": item.get("driveId", ""),
            "modifiedTime": item.get("modifiedTime", ""),
            "chunkIndex": item.get("chunkIndex", 0),
            "excerpt": item.get("excerpt", clean_excerpt(item.get("content", ""))),
            "aiTopic": item.get("aiTopic", ""),
            "roughScore": item.get("roughScore", 0),
        })
    return sources


def collect_drive_context(access_token: str, query: str) -> dict[str, Any]:
    files = list_drive_tree(access_token, DRIVE_FOLDER_ID)
    skipped: list[dict[str, str]] = []
    chunks: list[dict[str, Any]] = []

    for file in files:
        try:
            text = export_file_text(access_token, file)
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
        "sources": public_sources(ai_judgment["selected"]),
        "aiJudgment": {
            "topic": ai_judgment["topic"],
            "confidence": ai_judgment["confidence"],
            "reason": ai_judgment["reason"],
        },
    }


def fallback_answer(query: str, matches: list[dict[str, Any]], skipped: list[dict[str, str]], ai_judgment: dict[str, Any]) -> str:
    if not OPENAI_API_KEY:
        return "\n".join([
            "OPENAI_API_KEY is not configured, so AI topic judgment and final answer generation are disabled.",
            "The app only performed coarse candidate filtering and will not invent a topic label.",
            f"Candidate snippets: {len(matches)}; skipped files: {len(skipped)}.",
            "After configuring an OpenAI key, the app will ask AI to judge the topic, select citations, and generate the answer.",
        ])

    if not matches:
        lines = ["AI judged that no sufficiently relevant Drive snippets were found."]
        if skipped:
            lines.append(f"{len(skipped)} files were skipped because of unsupported formats or parsing errors.")
        return "\n".join(lines)

    return "\n".join([
        f"AI topic: {ai_judgment.get('topic', 'Not judged')} (confidence: {ai_judgment.get('confidence', 'low')})",
        "",
        "Relevant sources were selected, but final answer generation failed or returned no text. Please try again.",
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
        "You are a private AI mentor built from Brind course notes.",
        "Never claim to be Brind. Use the notes' ideas, rhythm, and reasoning style while staying honest about uncertainty.",
        "Answer in the same language as the user's question unless the user asks otherwise.",
        "Do not classify by keywords. Treat the earlier AI retrieval judgment as the topic signal.",
        "Rules:",
        "1. Start with the conclusion, then explain the reasoning.",
        "2. If the sources are insufficient, say so clearly and do not fabricate.",
        "3. For stock, finance, or medical questions, provide educational analysis only. Do not give deterministic buy/sell, diagnosis, or medication instructions.",
        "4. Cite source numbers in the answer, using forms like [1] or [2].",
        "",
        f"AI judged topic: {ai_judgment.get('topic', 'Not judged')}",
        f"User question: {query}",
        "",
        "Live Google Drive sources selected by AI:",
        context or "None",
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
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/access/status":
            json_response(self, {
                "enabled": bool(APP_ACCESS_CODE),
                "granted": has_app_access(self),
            })
            return
        if parsed.path == "/api/sources":
            has_session_token = bool(access_token_from_request(self))
            json_response(self, {
                "mode": "python-drive-live-ai-judged",
                "folderId": DRIVE_FOLDER_ID,
                "hasGoogleToken": has_session_token,
                "hasGoogleEnvToken": bool(GOOGLE_ACCESS_TOKEN),
                "hasGoogleOAuthConfig": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
                "hasOpenAIKey": bool(OPENAI_API_KEY),
                "storesLocalDocuments": False,
                "topicJudgment": "openai" if OPENAI_API_KEY else "disabled",
                "supports": ["Google Docs", "txt", "markdown", "pdf", "docx", "rtf", "legacy doc best-effort"],
            })
            return
        if parsed.path == "/api/auth/google/start":
            start_google_oauth(self)
            return
        if parsed.path == "/api/auth/google/callback":
            complete_google_oauth(self, urllib.parse.parse_qs(parsed.query))
            return
        serve_static(self, parsed.path)

    def do_POST(self) -> None:
        if self.path == "/api/access/login":
            login_app_access(self)
            return
        if self.path == "/api/access/logout":
            logout_app_access(self)
            return
        if self.path == "/api/auth/google/logout":
            logout_google(self)
            return
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            if not require_app_access(self):
                return
            access_token = access_token_from_request(self)
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            message = json.loads(body or "{}").get("message", "")
            context = collect_drive_context(access_token, message)
            answer = openai_answer(message, context["matches"], context["aiJudgment"]) or fallback_answer(
                message,
                context["matches"],
                context["skipped"],
                context["aiJudgment"],
            )
            json_response(self, {
                "answer": answer,
                "sources": context["sources"],
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
