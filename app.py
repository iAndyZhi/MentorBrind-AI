from __future__ import annotations

import base64
import http.cookies
import io
import json
import os
import re
import secrets
import socket
import threading
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


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ[key] = value


load_dotenv(ROOT / ".env")

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "4173"))
APP_BASE_URL = os.getenv("APP_BASE_URL", f"http://localhost:{PORT}")
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "1RbZmNxR8Ga-rnDzckYhoEO8i7FiVigWj")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SERVICE_ACCOUNT_JSON_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", f"{APP_BASE_URL}/api/auth/google/callback")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
APP_ACCESS_CODE = os.getenv("APP_ACCESS_CODE", "").strip()
EXPOSE_SOURCE_METADATA = os.getenv("EXPOSE_SOURCE_METADATA", "").lower() in {"1", "true", "yes"}
EXPOSE_SOURCE_EXCERPTS = os.getenv("EXPOSE_SOURCE_EXCERPTS", "").lower() in {"1", "true", "yes"}
MAX_FILES_PER_QUERY = int(os.getenv("MAX_FILES_PER_QUERY", "160"))
MAX_CHUNKS_FOR_MODEL = int(os.getenv("MAX_CHUNKS_FOR_MODEL", "8"))
MAX_CANDIDATES_FOR_AI = int(os.getenv("MAX_CANDIDATES_FOR_AI", "30"))
DRIVE_INDEX_TTL_SECONDS = int(os.getenv("DRIVE_INDEX_TTL_SECONDS", "900"))
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "45"))
AI_RETRIEVAL_MODE = os.getenv("AI_RETRIEVAL_MODE", "fast").strip().lower()
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}

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
SERVICE_ACCOUNT_TOKEN: dict[str, Any] = {"accessToken": "", "expiresAt": 0.0, "clientEmail": ""}
STARTED_AT = time.time()
DRIVE_INDEX_LOCK = threading.Lock()
DRIVE_INDEX_CACHE: dict[str, Any] = {
    "folderId": "",
    "builtAt": 0.0,
    "expiresAt": 0.0,
    "files": [],
    "chunks": [],
    "skipped": [],
    "records": {},
    "refreshStats": {},
    "lastError": "",
}
ANSWER_MODE_PROMPTS = {
    "mentor": [
        "Mode: mentor.",
        "Start with a direct conclusion, then explain the mechanism behind it.",
        "Use Brind-like reasoning: mechanism first, incentives and constraints next, then risks and boundary conditions.",
        "Keep the tone calm, pointed, and mentor-like rather than encyclopedic.",
    ],
    "strict citation": [
        "Mode: strict citation.",
        "Every important factual claim, causal claim, or judgment must include a citation like [1] or [2].",
        "If a claim is not supported by the selected notes, say: Not found in the notes.",
        "Do not rely on outside knowledge except for clearly labeled general background, and keep that background minimal.",
    ],
    "beginner explanation": [
        "Mode: beginner explanation.",
        "Use fewer technical terms and explain layered ideas step by step.",
        "Define necessary jargon briefly before using it.",
        "Use one simple analogy when it helps, but do not let the analogy replace the evidence.",
    ],
    "challenge assumptions": [
        "Mode: challenge assumptions.",
        "First list the hidden assumptions in the user's question.",
        "Then list counterexamples or scenarios where those assumptions fail.",
        "Then define the risk boundaries and what evidence would change the judgment.",
        "Only after that, give the final judgment.",
    ],
}
INDEX_JOB: dict[str, Any] = {
    "running": False,
    "startedAt": 0.0,
    "finishedAt": 0.0,
    "progress": {},
    "error": "",
}


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
    if COOKIE_SECURE:
        cookie[name]["secure"] = True
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
    code = str(json.loads(body or "{}").get("code", "")).strip()
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


def health_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    google_access_error = ""
    try:
        has_google_access = bool(access_token_from_request(handler))
    except Exception as exc:
        has_google_access = False
        google_access_error = str(exc)
    has_oauth_config = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
    missing: list[str] = []
    actions: list[str] = []

    if not has_google_access:
        missing.append("google_drive_access")
        if google_access_error:
            actions.append(google_access_error)
        elif has_oauth_config:
            actions.append("Click Connect Google Drive in the sidebar.")
        else:
            actions.append("Set GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_ACCESS_TOKEN, or configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")

    if not OPENAI_API_KEY:
        missing.append("openai_api_key")
        actions.append("Set OPENAI_API_KEY to enable AI topic judgment and final answers.")

    if APP_ACCESS_CODE and not has_app_access(handler):
        missing.append("app_access")
        actions.append("Enter the app access code in the sidebar.")

    return {
        "ok": not missing,
        "uptimeSeconds": int(time.time() - STARTED_AT),
        "mode": "python-drive-live-ai-judged",
        "missing": missing,
        "actions": actions,
        "config": {
            "host": HOST,
            "port": PORT,
            "folderId": DRIVE_FOLDER_ID,
            "hasGoogleAccess": has_google_access,
            "googleAccessError": google_access_error,
            "hasGoogleServiceAccount": has_service_account_config(),
            "googleServiceAccountEmail": SERVICE_ACCOUNT_TOKEN.get("clientEmail", ""),
            "hasGoogleEnvToken": bool(GOOGLE_ACCESS_TOKEN),
            "hasGoogleOAuthConfig": has_oauth_config,
            "hasOpenAIKey": bool(OPENAI_API_KEY),
            "openaiModel": OPENAI_MODEL,
            "openaiTimeoutSeconds": OPENAI_TIMEOUT_SECONDS,
            "aiRetrievalMode": AI_RETRIEVAL_MODE,
            "cookieSecure": COOKIE_SECURE,
            "appAccessCodeEnabled": bool(APP_ACCESS_CODE),
            "accessCodeLength": len(APP_ACCESS_CODE),
            "exposesSourceMetadata": EXPOSE_SOURCE_METADATA,
            "exposesSourceExcerpts": EXPOSE_SOURCE_EXCERPTS,
            "storesLocalDocuments": False,
            "loadsDotEnv": (ROOT / ".env").exists(),
            "driveIndex": index_status_payload(),
        },
    }


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


def service_account_info() -> dict[str, Any] | None:
    raw = GOOGLE_SERVICE_ACCOUNT_JSON.strip()
    if not raw and GOOGLE_SERVICE_ACCOUNT_JSON_B64.strip():
        raw = base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON_B64.strip()).decode("utf-8")
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        raw = raw.replace("\\n", "\n")
        info = json.loads(raw)
    if info.get("private_key"):
        info["private_key"] = str(info["private_key"]).replace("\\n", "\n")
    return info


def service_account_access_token() -> str:
    info = service_account_info()
    if not info:
        return ""
    now = time.time()
    if SERVICE_ACCOUNT_TOKEN.get("accessToken") and float(SERVICE_ACCOUNT_TOKEN.get("expiresAt") or 0) > now + 60:
        return str(SERVICE_ACCOUNT_TOKEN["accessToken"])
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except Exception as exc:
        raise AppError("Service account auth requires `pip install -r requirements.txt`.") from exc

    credentials = service_account.Credentials.from_service_account_info(info, scopes=[DRIVE_SCOPE])
    credentials.refresh(Request())
    expiry = credentials.expiry.timestamp() if credentials.expiry else now + 3600
    SERVICE_ACCOUNT_TOKEN.update({
        "accessToken": credentials.token,
        "expiresAt": expiry,
        "clientEmail": info.get("client_email", ""),
    })
    return str(credentials.token or "")


def has_service_account_config() -> bool:
    return bool(GOOGLE_SERVICE_ACCOUNT_JSON.strip() or GOOGLE_SERVICE_ACCOUNT_JSON_B64.strip())


def access_token_from_request(handler: BaseHTTPRequestHandler) -> str:
    token = service_account_access_token()
    if token:
        return token
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
        raise AppError("Missing Google Drive access. Set GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_ACCESS_TOKEN, or sign in with Google.")

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
        with urllib.request.urlopen(request, timeout=OPENAI_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AppError(f"OpenAI API {exc.code}: {detail}") from exc
    except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
        raise AppError(f"OpenAI API timed out after {OPENAI_TIMEOUT_SECONDS} seconds.") from exc

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


def normalize_answer_mode(value: str) -> str:
    mode = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return mode if mode in ANSWER_MODE_PROMPTS else "mentor"


def parse_message_and_mode(payload: dict[str, Any]) -> tuple[str, str]:
    message = str(payload.get("message", "")).strip()
    mode = normalize_answer_mode(str(payload.get("mode", "")))
    match = re.match(r"^\[([^\]]+)\]\s*(.*)$", message)
    if match:
        mode = normalize_answer_mode(match.group(1))
        message = match.group(2).strip()
    return message, mode


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


def fast_select_context(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    selected = candidates[:MAX_CHUNKS_FOR_MODEL]
    for item in selected:
        item["aiTopic"] = "Fast retrieval mode"
    return {
        "topic": "Fast retrieval mode",
        "confidence": "medium" if selected else "low",
        "selected": selected,
        "reason": "Skipped the separate AI retrieval judge to reduce latency. The final model still evaluates the selected context before answering.",
    }


def public_sources(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for index, item in enumerate(matches):
        source = {
            "citation": index + 1,
            "label": f"Source {index + 1}",
        }
        if EXPOSE_SOURCE_METADATA:
            source.update({
                "title": item.get("title", "Untitled"),
                "source": item.get("source", "unknown"),
                "modifiedTime": item.get("modifiedTime", ""),
                "chunkIndex": item.get("chunkIndex", 0),
                "aiTopic": item.get("aiTopic", ""),
            })
        if EXPOSE_SOURCE_EXCERPTS:
            source["excerpt"] = item.get("excerpt", clean_excerpt(item.get("content", "")))
        sources.append(source)
    return sources


def index_status_payload() -> dict[str, Any]:
    now = time.time()
    built_at = float(DRIVE_INDEX_CACHE.get("builtAt") or 0)
    expires_at = float(DRIVE_INDEX_CACHE.get("expiresAt") or 0)
    job_started_at = float(INDEX_JOB.get("startedAt") or 0)
    job_finished_at = float(INDEX_JOB.get("finishedAt") or 0)
    return {
        "cached": bool(DRIVE_INDEX_CACHE.get("chunks")),
        "refreshRunning": bool(INDEX_JOB.get("running")),
        "refreshStartedAt": int(job_started_at) if job_started_at else None,
        "refreshFinishedAt": int(job_finished_at) if job_finished_at else None,
        "refreshAgeSeconds": int(now - job_started_at) if job_started_at and INDEX_JOB.get("running") else None,
        "refreshProgress": INDEX_JOB.get("progress") or {},
        "refreshError": INDEX_JOB.get("error", ""),
        "folderId": DRIVE_INDEX_CACHE.get("folderId") or DRIVE_FOLDER_ID,
        "builtAt": int(built_at) if built_at else None,
        "ageSeconds": int(now - built_at) if built_at else None,
        "expiresInSeconds": max(0, int(expires_at - now)) if expires_at else 0,
        "ttlSeconds": DRIVE_INDEX_TTL_SECONDS,
        "scannedFiles": len(DRIVE_INDEX_CACHE.get("files") or []),
        "textChunks": len(DRIVE_INDEX_CACHE.get("chunks") or []),
        "skippedFiles": len(DRIVE_INDEX_CACHE.get("skipped") or []),
        "refreshStats": DRIVE_INDEX_CACHE.get("refreshStats") or {},
        "lastError": DRIVE_INDEX_CACHE.get("lastError", ""),
    }


def snapshot_drive_index() -> dict[str, Any] | None:
    with DRIVE_INDEX_LOCK:
        if DRIVE_INDEX_CACHE.get("folderId") != DRIVE_FOLDER_ID:
            return None
        return dict(DRIVE_INDEX_CACHE)


def file_signature(file: dict[str, Any]) -> str:
    return "|".join([
        file.get("id", ""),
        file.get("name", ""),
        file.get("mimeType", ""),
        file.get("modifiedTime", ""),
        str(file.get("size", "")),
    ])


def build_file_record(access_token: str, file: dict[str, Any]) -> dict[str, Any]:
    record = {
        "signature": file_signature(file),
        "file": file,
        "chunks": [],
        "skipped": [],
    }
    try:
        text = export_file_text(access_token, file)
        if not text:
            record["skipped"] = [{"name": file["name"], "path": file["path"], "mimeType": file["mimeType"], "reason": "Unsupported live export type"}]
            return record
        record["chunks"] = split_into_chunks(file, text)
    except Exception as exc:
        record["skipped"] = [{"name": file["name"], "path": file["path"], "mimeType": file["mimeType"], "reason": str(exc)}]
    return record


def build_drive_index(access_token: str, previous_index: dict[str, Any] | None = None) -> dict[str, Any]:
    files = list_drive_tree(access_token, DRIVE_FOLDER_ID)
    skipped: list[dict[str, str]] = []
    chunks: list[dict[str, Any]] = []
    records: dict[str, dict[str, Any]] = {}
    previous_records = (previous_index or {}).get("records") or {}
    stats = {
        "listedFiles": len(files),
        "reusedFiles": 0,
        "readFiles": 0,
        "changedFiles": 0,
        "unsupportedOrFailedFiles": 0,
        "removedFiles": 0,
    }
    if INDEX_JOB.get("running"):
        INDEX_JOB["progress"] = dict(stats)

    for file in files:
        signature = file_signature(file)
        previous_record = previous_records.get(file["id"])
        if previous_record and previous_record.get("signature") == signature:
            record = previous_record
            stats["reusedFiles"] += 1
        else:
            record = build_file_record(access_token, file)
            stats["readFiles"] += 1
            if previous_record:
                stats["changedFiles"] += 1
        if record.get("skipped"):
            stats["unsupportedOrFailedFiles"] += 1
        records[file["id"]] = record
        chunks.extend(record.get("chunks") or [])
        skipped.extend(record.get("skipped") or [])
        if INDEX_JOB.get("running"):
            INDEX_JOB["progress"] = dict(stats)

    previous_ids = set(previous_records.keys())
    current_ids = {file["id"] for file in files}
    stats["removedFiles"] = len(previous_ids - current_ids)

    now = time.time()
    return {
        "folderId": DRIVE_FOLDER_ID,
        "builtAt": now,
        "expiresAt": now + DRIVE_INDEX_TTL_SECONDS,
        "files": files,
        "chunks": chunks,
        "skipped": skipped,
        "records": records,
        "refreshStats": stats,
        "lastError": "",
    }


def get_drive_index(access_token: str, force_refresh: bool = False) -> dict[str, Any]:
    if not access_token:
        raise AppError("Missing Google Drive access. Set GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_ACCESS_TOKEN, or sign in with Google.")

    now = time.time()
    with DRIVE_INDEX_LOCK:
        is_current_folder = DRIVE_INDEX_CACHE.get("folderId") == DRIVE_FOLDER_ID
        has_cache = bool(DRIVE_INDEX_CACHE.get("builtAt"))
        is_fresh = float(DRIVE_INDEX_CACHE.get("expiresAt") or 0) > now
        if has_cache and is_current_folder and is_fresh and not force_refresh:
            return DRIVE_INDEX_CACHE
        previous_index = dict(DRIVE_INDEX_CACHE) if is_current_folder else None

    try:
        index = build_drive_index(access_token, previous_index)
    except Exception as exc:
        with DRIVE_INDEX_LOCK:
            DRIVE_INDEX_CACHE["lastError"] = str(exc)
        raise

    with DRIVE_INDEX_LOCK:
        DRIVE_INDEX_CACHE.clear()
        DRIVE_INDEX_CACHE.update(index)
        return DRIVE_INDEX_CACHE


def run_index_refresh_job(access_token: str) -> None:
    try:
        previous_index = snapshot_drive_index()
        index = build_drive_index(access_token, previous_index)
        with DRIVE_INDEX_LOCK:
            DRIVE_INDEX_CACHE.clear()
            DRIVE_INDEX_CACHE.update(index)
            INDEX_JOB.update({
                "running": False,
                "finishedAt": time.time(),
                "progress": index.get("refreshStats", {}),
                "error": "",
            })
    except Exception as exc:
        with DRIVE_INDEX_LOCK:
            DRIVE_INDEX_CACHE["lastError"] = str(exc)
            INDEX_JOB.update({
                "running": False,
                "finishedAt": time.time(),
                "progress": INDEX_JOB.get("progress") or {},
                "error": str(exc),
            })


def start_index_refresh(access_token: str) -> dict[str, Any]:
    if not access_token:
        raise AppError("Missing Google Drive access. Set GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_ACCESS_TOKEN, or sign in with Google.")
    with DRIVE_INDEX_LOCK:
        if INDEX_JOB.get("running"):
            return {"started": False, "cache": index_status_payload()}
        INDEX_JOB.update({
            "running": True,
            "startedAt": time.time(),
            "finishedAt": 0.0,
            "progress": {},
            "error": "",
        })
    thread = threading.Thread(target=run_index_refresh_job, args=(access_token,), daemon=True)
    thread.start()
    return {"started": True, "cache": index_status_payload()}


def drive_index_for_chat(access_token: str) -> dict[str, Any]:
    if not access_token:
        raise AppError("Missing Google Drive access. Set GOOGLE_ACCESS_TOKEN or sign in with Google.")
    now = time.time()
    start_refresh = False
    with DRIVE_INDEX_LOCK:
        has_chunks = bool(DRIVE_INDEX_CACHE.get("chunks"))
        is_current_folder = DRIVE_INDEX_CACHE.get("folderId") == DRIVE_FOLDER_ID
        is_expired = float(DRIVE_INDEX_CACHE.get("expiresAt") or 0) <= now
        is_running = bool(INDEX_JOB.get("running"))
        if has_chunks and is_current_folder:
            index = dict(DRIVE_INDEX_CACHE)
            start_refresh = is_expired and not is_running
        elif is_running:
            raise AppError("Knowledge index is still building. Wait for the sidebar status to become Cached, then send again.")
        else:
            raise AppError("Knowledge index is empty. Click Refresh in the sidebar first, then wait until it becomes Cached.")
    if start_refresh:
        start_index_refresh(access_token)
    return index


def collect_drive_context(access_token: str, query: str) -> dict[str, Any]:
    timings: dict[str, int] = {}
    started_at = time.perf_counter()
    index = drive_index_for_chat(access_token)
    timings["indexMs"] = int((time.perf_counter() - started_at) * 1000)

    rank_started_at = time.perf_counter()
    chunks = list(index.get("chunks") or [])
    skipped = list(index.get("skipped") or [])
    candidates = rough_rank_chunks(query, chunks)
    timings["rankMs"] = int((time.perf_counter() - rank_started_at) * 1000)

    judge_started_at = time.perf_counter()
    if AI_RETRIEVAL_MODE == "ai":
        ai_judgment = ai_select_context(query, candidates)
    else:
        ai_judgment = fast_select_context(query, candidates)
    timings["judgeMs"] = int((time.perf_counter() - judge_started_at) * 1000)
    return {
        "cache": index_status_payload(),
        "timings": timings,
        "scannedFiles": len(index.get("files") or []),
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


def build_answer_prompt(query: str, matches: list[dict[str, Any]], ai_judgment: dict[str, Any], answer_mode: str = "mentor") -> str:
    mode = normalize_answer_mode(answer_mode)
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
        "5. Do not reveal raw source passages or long quotes. Synthesize the answer from the sources instead.",
        "6. If the user asks for raw notes, full documents, exact transcripts, hidden file names, Drive paths, or bulk extraction, refuse that part and offer a concise synthesized summary instead.",
        "",
        "Answer mode rules:",
        *ANSWER_MODE_PROMPTS[mode],
        "",
        f"Retrieval mode/topic signal: {ai_judgment.get('topic', 'Not judged')}",
        f"Retrieval note: {ai_judgment.get('reason', '')}",
        f"User question: {query}",
        "",
        "Live Google Drive sources selected by AI:",
        context or "None",
    ])


def openai_answer(query: str, matches: list[dict[str, Any]], ai_judgment: dict[str, Any], answer_mode: str = "mentor") -> str | None:
    if not OPENAI_API_KEY:
        return None
    return openai_request(build_answer_prompt(query, matches, ai_judgment, answer_mode))


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
        if parsed.path == "/api/health":
            json_response(self, health_payload(self))
            return
        if parsed.path == "/api/access/status":
            json_response(self, {
                "enabled": bool(APP_ACCESS_CODE),
                "granted": has_app_access(self),
            })
            return
        if parsed.path == "/api/sources":
            google_access_error = ""
            try:
                has_session_token = bool(access_token_from_request(self))
            except Exception as exc:
                has_session_token = False
                google_access_error = str(exc)
            json_response(self, {
                "mode": "python-drive-live-ai-judged",
                "folderId": DRIVE_FOLDER_ID,
                "hasGoogleToken": has_session_token,
                "googleAccessError": google_access_error,
                "hasGoogleServiceAccount": has_service_account_config(),
                "googleServiceAccountEmail": SERVICE_ACCOUNT_TOKEN.get("clientEmail", ""),
                "hasGoogleEnvToken": bool(GOOGLE_ACCESS_TOKEN),
                "hasGoogleOAuthConfig": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
                "hasOpenAIKey": bool(OPENAI_API_KEY),
                "storesLocalDocuments": False,
                "topicJudgment": AI_RETRIEVAL_MODE if OPENAI_API_KEY else "disabled",
                "supports": ["Google Docs", "txt", "markdown", "pdf", "docx", "rtf", "legacy doc best-effort"],
                "driveIndex": index_status_payload(),
            })
            return
        if parsed.path == "/api/index/status":
            if not require_app_access(self):
                return
            json_response(self, index_status_payload())
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
        if self.path == "/api/index/refresh":
            try:
                if not require_app_access(self):
                    return
                result = start_index_refresh(access_token_from_request(self))
                json_response(self, {
                    "ok": True,
                    "started": result["started"],
                    "cache": result["cache"],
                }, 202)
            except Exception as exc:
                json_response(self, {"error": str(exc), "cache": index_status_payload()}, 500)
            return
        if self.path != "/api/chat":
            self.send_error(404)
            return
        request_started_at = time.perf_counter()
        try:
            if not require_app_access(self):
                return
            access_token = access_token_from_request(self)
            if not access_token:
                json_response(self, {"error": "Google Drive is not connected in this browser session. Click Connect Google Drive again."}, 401)
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            message, answer_mode = parse_message_and_mode(json.loads(body or "{}"))
            if not message:
                json_response(self, {"error": "Message is required."}, 400)
                return
            context = collect_drive_context(access_token, message)
            answer_error = ""
            answer_started_at = time.perf_counter()
            try:
                answer = openai_answer(message, context["matches"], context["aiJudgment"], answer_mode)
            except Exception as exc:
                answer_error = str(exc)
                answer = None
            answer_ms = int((time.perf_counter() - answer_started_at) * 1000)
            if not answer:
                answer = fallback_answer(
                    message,
                    context["matches"],
                    context["skipped"],
                    context["aiJudgment"],
                )
                if answer_error:
                    answer = f"{answer}\n\nAnswer generation error: {answer_error}"
            timings = {
                **context["timings"],
                "answerMs": answer_ms,
                "totalMs": int((time.perf_counter() - request_started_at) * 1000),
            }
            json_response(self, {
                "answer": answer,
                "sources": context["sources"],
                "skipped": context["skipped"][:20],
                "aiJudgment": context["aiJudgment"],
                "answerMode": answer_mode,
                "answerError": answer_error,
                "stats": {
                    "scannedFiles": context["scannedFiles"],
                    "textChunks": context["textChunks"],
                    "skippedFiles": len(context["skipped"]),
                    "cache": context["cache"],
                    "timings": timings,
                },
                "model": OPENAI_MODEL if OPENAI_API_KEY else "python-drive-live-demo",
            })
        except AppError as exc:
            json_response(self, {"error": str(exc), "cache": index_status_payload()}, 409)
        except Exception as exc:
            json_response(self, {"error": str(exc), "cache": index_status_payload()}, 500)

    def log_message(self, format: str, *args: Any) -> None:
        return


if __name__ == "__main__":
    print(f"Brind Mentor Python backend running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
