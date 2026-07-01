from __future__ import annotations

import base64
import hashlib
import hmac
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
APP_SESSION_SECRET = os.getenv("APP_SESSION_SECRET", "").strip()
EXPOSE_SOURCE_METADATA = os.getenv("EXPOSE_SOURCE_METADATA", "").lower() in {"1", "true", "yes"}
CITATION_CONTEXT_CHARS = int(os.getenv("CITATION_CONTEXT_CHARS", "300"))
SOURCE_EXCERPT_CHARS = int(os.getenv("SOURCE_EXCERPT_CHARS", "100"))
MAX_FILES_PER_QUERY = int(os.getenv("MAX_FILES_PER_QUERY", "160"))
MAX_CHUNKS_FOR_MODEL = int(os.getenv("MAX_CHUNKS_FOR_MODEL", "8"))
MAX_CANDIDATES_FOR_AI = int(os.getenv("MAX_CANDIDATES_FOR_AI", "30"))
MAX_CHAT_CANDIDATES_FOR_AI = max(0, int(os.getenv("MAX_CHAT_CANDIDATES_FOR_AI", "10")))
MAX_CHAT_SOURCES_FOR_MODEL = max(0, int(os.getenv("MAX_CHAT_SOURCES_FOR_MODEL", "3")))
DRIVE_INDEX_TTL_SECONDS = int(os.getenv("DRIVE_INDEX_TTL_SECONDS", "900"))
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "45"))
AI_RETRIEVAL_MODE = os.getenv("AI_RETRIEVAL_MODE", "ai").strip().lower()
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
MENTOR_CHAT_ALIASES = tuple(
    alias.strip()
    for alias in os.getenv("MENTOR_CHAT_ALIASES", "Brind,张成熙,Brind张成熙").split(",")
    if alias.strip()
)
MENTOR_CHAT_ALIAS_KEYS = frozenset(
    re.sub(r"[^\w\u4e00-\u9fff]+", "", alias, flags=re.UNICODE).casefold()
    for alias in MENTOR_CHAT_ALIASES
)
CHAT_CONTEXT_MESSAGES = max(0, int(os.getenv("CHAT_CONTEXT_MESSAGES", "2")))
CHAT_MENTOR_CHUNK_CHARS = max(400, int(os.getenv("CHAT_MENTOR_CHUNK_CHARS", "1600")))
CHAT_MIN_MENTOR_CHUNK_CHARS = max(1, int(os.getenv("CHAT_MIN_MENTOR_CHUNK_CHARS", "12")))


def load_coded_term_groups() -> dict[str, tuple[str, ...]]:
    configured: dict[str, Any] = {}
    raw = os.getenv("CODED_TERM_GROUPS_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                configured = parsed
        except json.JSONDecodeError:
            configured = {}

    merged: dict[str, tuple[str, ...]] = {}
    for canonical, aliases in configured.items():
        values = aliases if isinstance(aliases, list) else [aliases]
        normalized = tuple(dict.fromkeys(
            str(value).strip()
            for value in [canonical, *values]
            if str(value).strip()
        ))
        if normalized:
            merged[str(canonical).strip()] = normalized
    return merged


CODED_TERM_GROUPS = load_coded_term_groups()

MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_DOC = "application/vnd.google-apps.document"
MIME_TEXT = {"text/plain", "text/markdown"}
MIME_PDF = "application/pdf"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_DOC_LEGACY = "application/msword"
MIME_RTF = "application/rtf"
CHAT_MESSAGE_HEADER_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+'([^'\r\n]+)'\s*$",
    flags=re.MULTILINE,
)
SESSION_COOKIE = "mentorbrind_session"
ACCESS_COOKIE = "mentorbrind_access"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", str(30 * 24 * 60 * 60)))
ACCESS_MAX_AGE_SECONDS = int(os.getenv("ACCESS_MAX_AGE_SECONDS", str(180 * 24 * 60 * 60)))

SESSIONS: dict[str, dict[str, Any]] = {}
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


def access_signing_key() -> bytes:
    return (APP_SESSION_SECRET or APP_ACCESS_CODE).encode("utf-8")


def make_access_token(max_age: int) -> str:
    expires_at = int(time.time()) + max_age
    nonce = secrets.token_urlsafe(12)
    payload = f"{expires_at}.{nonce}"
    signature = hmac.new(access_signing_key(), payload.encode("utf-8"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload}.{encoded_signature}"


def valid_access_token(token: str) -> bool:
    try:
        expires_text, nonce, provided_signature = token.split(".", 2)
        expires_at = int(expires_text)
    except (TypeError, ValueError):
        return False
    if expires_at <= int(time.time()) or not nonce:
        return False
    payload = f"{expires_at}.{nonce}"
    expected = hmac.new(access_signing_key(), payload.encode("utf-8"), hashlib.sha256).digest()
    expected_signature = base64.urlsafe_b64encode(expected).decode("ascii").rstrip("=")
    return secrets.compare_digest(provided_signature, expected_signature)


def make_access_cookie(access_token: str, max_age: int) -> str:
    return make_cookie(ACCESS_COOKIE, access_token, max_age)


def clear_access_cookie() -> str:
    return make_access_cookie("", 0)


def has_app_access(handler: BaseHTTPRequestHandler) -> bool:
    if not APP_ACCESS_CODE:
        return True
    token = parse_cookie(handler.headers.get("Cookie")).get(ACCESS_COOKIE, "")
    return valid_access_token(token) if token else False


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

    access_token = make_access_token(ACCESS_MAX_AGE_SECONDS)
    json_response(handler, {"ok": True, "enabled": True}, headers={"Set-Cookie": make_access_cookie(access_token, ACCESS_MAX_AGE_SECONDS)})


def logout_app_access(handler: BaseHTTPRequestHandler) -> None:
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
            "accessSessionStateless": True,
            "accessMaxAgeSeconds": ACCESS_MAX_AGE_SECONDS,
            "hasAppSessionSecret": bool(APP_SESSION_SECRET),
            "exposesSourceMetadata": EXPOSE_SOURCE_METADATA,
            "citationContextChars": CITATION_CONTEXT_CHARS,
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
        encoded = "".join(GOOGLE_SERVICE_ACCOUNT_JSON_B64.split())
        try:
            raw = base64.b64decode(encoded, validate=True).decode("utf-8")
        except Exception as exc:
            raise AppError("GOOGLE_SERVICE_ACCOUNT_JSON_B64 is not valid base64-encoded UTF-8 JSON. Clear it and use GOOGLE_SERVICE_ACCOUNT_JSON, or paste a base64 string generated from the service account JSON file.") from exc
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        try:
            raw = raw.replace("\\n", "\n")
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AppError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the entire service account JSON file content, or use GOOGLE_SERVICE_ACCOUNT_JSON_B64.") from exc
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
    if limit <= 3:
        return compact[:limit]
    return f"{compact[:limit - 3].rstrip()}..."


def relevant_excerpt(text: str, query: str, limit: int = 100) -> str:
    """Return a bounded window around the sentence with the best query overlap."""
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    if limit <= 6:
        return compact[:limit]

    query_text = re.sub(r"\s+", " ", query).strip().lower()
    terms = sorted(tokenize(query), key=len, reverse=True)
    sentences = list(re.finditer(r"[^。！？!?；;]+(?:[。！？!?；;]+|$)", compact))

    def sentence_score(match: re.Match[str]) -> int:
        sentence = match.group(0).lower()
        exact_score = 1000 if query_text and query_text in sentence else 0
        overlap_score = sum((len(term) ** 2) * sentence.count(term) for term in terms if term in sentence)
        return exact_score + overlap_score

    best = max(sentences, key=sentence_score) if sentences else None
    if not best or sentence_score(best) <= 0:
        return clean_excerpt(compact, limit)

    best_text = best.group(0).lower()
    matching_positions = [
        (best_text.find(term), len(term))
        for term in terms
        if term and best_text.find(term) >= 0
    ]
    if matching_positions:
        position, term_length = min(matching_positions, key=lambda item: item[0])
        anchor = best.start() + position + term_length // 2
    else:
        anchor = (best.start() + best.end()) // 2

    prefix = suffix = True
    start = end = 0
    for _ in range(3):
        budget = max(1, limit - (3 if prefix else 0) - (3 if suffix else 0))
        if best.end() - best.start() <= budget:
            surrounding = budget - (best.end() - best.start())
            desired_start = best.start() - surrounding // 4
        else:
            desired_start = anchor - budget // 3
        start = max(0, min(len(compact) - budget, desired_start))
        end = min(len(compact), start + budget)
        prefix = start > 0
        suffix = end < len(compact)

    excerpt = compact[start:end].strip()
    return f"{'...' if prefix else ''}{excerpt}{'...' if suffix else ''}"


def normalize_source_evidence(value: Any) -> str:
    return clean_excerpt(str(value or ""), SOURCE_EXCERPT_CHARS)


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


def normalize_speaker_name(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value, flags=re.UNICODE).casefold()


def is_mentor_speaker(value: str) -> bool:
    normalized = normalize_speaker_name(value)
    return bool(normalized) and normalized in MENTOR_CHAT_ALIAS_KEYS


def parse_chat_messages(content: str) -> list[dict[str, str]]:
    matches = list(CHAT_MESSAGE_HEADER_RE.finditer(content))
    messages: list[dict[str, str]] = []
    for position, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[position + 1].start() if position + 1 < len(matches) else len(content)
        body = content[body_start:body_end].strip()
        if body:
            messages.append({
                "timestamp": match.group(1),
                "speaker": match.group(2).strip(),
                "text": body,
            })
    return messages


def is_chat_history_file(file: dict[str, Any], content: str) -> bool:
    path = f"{file.get('path', '')}/{file.get('name', '')}".replace("\\", "/").casefold()
    if "chat history" in path or "chat_history" in path or "聊天记录" in path:
        return True
    if not str(file.get("name", "")).casefold().endswith(".txt"):
        return False
    return len(CHAT_MESSAGE_HEADER_RE.findall(content[:100_000])) >= 3


def useful_chat_text(value: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", value.replace("\r\n", "\n")).strip()
    placeholders = re.sub(
        r"\[(?:图片|视频|动画表情|表情|语音|文件|链接|image|video|sticker|audio|file|link)\]",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text if placeholders.strip() else ""


def clean_mentor_chat_text(value: str) -> str:
    text = useful_chat_text(value)
    if not text:
        return ""
    # Exported replies often append another member's quoted message. It is context, not mentor evidence.
    return re.sub(r"\s*\[引用[^\n]*$", "", text).strip()


def mentor_messages_are_substantive(messages: list[dict[str, str]]) -> bool:
    combined = " ".join(message["text"] for message in messages)
    meaningful_chars = re.findall(r"[\w\u4e00-\u9fff]", combined, flags=re.UNICODE)
    if len(meaningful_chars) < CHAT_MIN_MENTOR_CHUNK_CHARS:
        return False
    casual = re.sub(r"[^\w\u4e00-\u9fff]+", "", combined, flags=re.UNICODE).casefold()
    return casual not in {"哈哈", "哈哈哈", "好的", "是的", "对", "嗯", "可以", "收到", "笑死", "牛"}


def make_mentor_chat_chunk(
    file: dict[str, Any],
    mentor_messages: list[dict[str, str]],
    context_messages: list[dict[str, str]],
    index: int,
) -> dict[str, Any]:
    mentor_content = "\n\n".join(
        f"{message['speaker']}（导师，{message['timestamp']}）：{message['text']}"
        for message in mentor_messages
    )
    context_content = "\n".join(
        f"{message['speaker']}（群聊上下文）：{clean_excerpt(message['text'], 240)}"
        for message in context_messages
    )
    chunk = make_chunk(file, mentor_content, index)
    chunk.update({
        "sourceType": "mentor_chat",
        "mentorSpeaker": mentor_messages[0]["speaker"],
        "chatContext": context_content,
    })
    return chunk


def split_mentor_chat_into_chunks(file: dict[str, Any], content: str) -> list[dict[str, Any]]:
    messages = parse_chat_messages(content)
    chunks: list[dict[str, Any]] = []
    recent_context: list[dict[str, str]] = []
    pending_context: list[dict[str, str]] = []
    mentor_buffer: list[dict[str, str]] = []

    def flush() -> None:
        nonlocal mentor_buffer, pending_context
        if mentor_buffer and mentor_messages_are_substantive(mentor_buffer):
            chunks.append(make_mentor_chat_chunk(file, mentor_buffer, pending_context, len(chunks)))
        mentor_buffer = []
        pending_context = []

    for message in messages:
        mentor_message = is_mentor_speaker(message["speaker"])
        text = clean_mentor_chat_text(message["text"]) if mentor_message else useful_chat_text(message["text"])
        if not text:
            continue
        cleaned = {**message, "text": text}
        if not mentor_message:
            flush()
            if CHAT_CONTEXT_MESSAGES:
                recent_context = [*recent_context, cleaned][-CHAT_CONTEXT_MESSAGES:]
            continue

        formatted_length = len(cleaned["speaker"]) + len(cleaned["timestamp"]) + len(text) + 8
        buffered_length = sum(
            len(item["speaker"]) + len(item["timestamp"]) + len(item["text"]) + 8
            for item in mentor_buffer
        )
        if mentor_buffer and buffered_length + formatted_length > CHAT_MENTOR_CHUNK_CHARS:
            flush()
        if not mentor_buffer:
            pending_context = list(recent_context)
        mentor_buffer.append(cleaned)

    flush()
    return chunks


def split_into_chunks(file: dict[str, Any], content: str) -> list[dict[str, Any]]:
    normalized = re.sub(r"\n{3,}", "\n\n", content.replace("\r\n", "\n")).strip()
    if not normalized:
        return []
    if is_chat_history_file(file, normalized):
        return split_mentor_chat_into_chunks(file, normalized)

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


def term_appears(text: str, term: str) -> bool:
    if not term:
        return False
    if term.isascii() and re.fullmatch(r"[A-Za-z0-9_\-]+", term):
        return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.IGNORECASE))
    return term.casefold() in text.casefold()


def matched_coded_term_groups(text: str) -> list[tuple[str, tuple[str, ...]]]:
    return [
        (canonical, terms)
        for canonical, terms in CODED_TERM_GROUPS.items()
        if any(term_appears(text, term) for term in terms)
    ]


def expanded_retrieval_terms(query: str) -> list[str]:
    terms = list(tokenize(query))
    for _, equivalents in matched_coded_term_groups(query):
        for equivalent in equivalents:
            terms.extend(tokenize(equivalent))
    return list(dict.fromkeys(terms))


def coded_term_guidance(query: str) -> str:
    groups = matched_coded_term_groups(query)
    if not groups:
        return "None"
    return "; ".join(
        f"{canonical} = {' / '.join(term for term in equivalents if term != canonical)}"
        for canonical, equivalents in groups
    )


def prioritize_ranked_candidates(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    documents = [item for item in items if item.get("sourceType") != "mentor_chat"]
    chats = [item for item in items if item.get("sourceType") == "mentor_chat"]
    chat_quota = min(MAX_CHAT_CANDIDATES_FOR_AI, limit)
    document_quota = max(0, limit - chat_quota)
    selected = [*documents[:document_quota], *chats[:chat_quota]]
    selected_ids = {item["id"] for item in selected}
    if len(selected) < limit:
        leftovers = [item for item in items if item["id"] not in selected_ids]
        selected.extend(leftovers[:limit - len(selected)])
    return selected


def prioritize_selected_sources(items: list[dict[str, Any]], limit: int = MAX_CHUNKS_FOR_MODEL) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    documents = [item for item in items if item.get("sourceType") != "mentor_chat"]
    chats = [item for item in items if item.get("sourceType") == "mentor_chat"]
    if not documents:
        return chats[:limit]
    selected_documents = documents[:limit]
    remaining = max(0, limit - len(selected_documents))
    selected_chats = chats[:min(MAX_CHAT_SOURCES_FOR_MODEL, remaining)]
    return [*selected_documents, *selected_chats]


def rough_rank_chunks(query: str, chunks: list[dict[str, Any]], limit: int = MAX_CANDIDATES_FOR_AI) -> list[dict[str, Any]]:
    """Only a coarse candidate reducer. It does not judge topic."""
    terms = expanded_retrieval_terms(query)
    raw_query = query.lower()
    scored = []
    for item in chunks:
        haystack = f"{item.get('title', '')} {item.get('content', '')}".lower()
        context_haystack = str(item.get("chatContext", "")).lower()
        term_score = sum(2 for term in terms if term in haystack)
        context_score = sum(1 for term in terms if term in context_haystack)
        phrase_score = 8 if raw_query and raw_query in haystack else 0
        context_phrase_score = 2 if raw_query and raw_query in context_haystack else 0
        score = term_score + context_score + phrase_score + context_phrase_score
        if score > 0:
            scored.append({**item, "roughScore": score})
    if not scored:
        scored = [{**item, "roughScore": 0} for item in chunks[:limit]]
    ranked = sorted(scored, key=lambda item: item["roughScore"], reverse=True)
    return prioritize_ranked_candidates(ranked, limit)


def note_coverage(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [int(item.get("roughScore", 0) or 0) for item in candidates]
    top_score = max(scores, default=0)
    matched_candidates = sum(1 for score in scores if score > 0)
    if top_score <= 0:
        level = "low"
    elif top_score >= 8 or (top_score >= 6 and matched_candidates >= 3):
        level = "high"
    elif top_score >= 4 or matched_candidates >= 2:
        level = "medium"
    else:
        level = "low"
    return {
        "level": level,
        "topRoughScore": top_score,
        "matchedCandidates": matched_candidates,
    }


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
        fallback = prioritize_selected_sources(candidates)
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
            "source_type": item.get("sourceType", "document"),
            "chat_context_only": str(item.get("chatContext", ""))[:500],
        }
        for item in candidates
    ]
    prompt = "\n".join([
        "You are a retrieval judge for a private knowledge base.",
        "Do not classify the user topic by hard-coded keywords. Judge semantically from the user intent and candidate snippets.",
        "Pick up to 8 snippets that can truly help answer the user question, and produce a natural-language topic label.",
        "Curated document snippets are the primary knowledge source. mentor_chat snippets are supplementary and are usually less complete.",
        f"When relevant document snippets exist, select them first and select at most {MAX_CHAT_SOURCES_FOR_MODEL} mentor_chat snippets.",
        "For mentor_chat snippets, excerpt contains Brind's statements. chat_context_only contains other members' nearby messages and may help identify the question, but it is not evidence of Brind's view.",
        "If none of the candidates are relevant, return an empty selected_ids array.",
        "Return JSON only, no Markdown.",
        "Schema: {\"topic\":\"...\",\"confidence\":\"high|medium|low\",\"selected_ids\":[\"...\"],\"reason\":\"...\"}",
        "",
        f"User question: {query}",
        f"Knowledge-base coded-term equivalences: {coded_term_guidance(query)}",
        "",
        "Candidate snippets:",
        json.dumps(compact_candidates, ensure_ascii=False),
    ])
    try:
        judgment = parse_json_object(openai_request(prompt))
    except Exception as exc:
        fallback = prioritize_selected_sources(candidates)
        for item in fallback:
            item["aiTopic"] = "AI judgment failed"
        return {
            "topic": "AI judgment failed",
            "confidence": "low",
            "selected": fallback,
            "reason": str(exc),
        }

    selected_ids = set(judgment.get("selected_ids", []))
    selected = prioritize_selected_sources([item for item in candidates if item["id"] in selected_ids])
    if not selected and candidates:
        selected = prioritize_selected_sources(candidates, min(3, MAX_CHUNKS_FOR_MODEL))
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
    selected = prioritize_selected_sources(candidates)
    for item in selected:
        item["aiTopic"] = "Fast retrieval mode"
    return {
        "topic": "Fast retrieval mode",
        "confidence": "medium" if selected else "low",
        "selected": selected,
        "reason": "Skipped the separate AI retrieval judge to reduce latency. The final model still evaluates the selected context before answering.",
    }


def citation_context(item: dict[str, Any], chunk_lookup: dict[tuple[str, int], str]) -> str:
    drive_id = str(item.get("driveId", ""))
    chunk_index = int(item.get("chunkIndex", 0) or 0)
    current = str(item.get("content", ""))
    before = chunk_lookup.get((drive_id, chunk_index - 1), "")
    after = chunk_lookup.get((drive_id, chunk_index + 1), "")
    sections: list[str] = []
    if before and CITATION_CONTEXT_CHARS > 0:
        sections.append(f"...{before[-CITATION_CONTEXT_CHARS:]}")
    sections.append(current)
    if after and CITATION_CONTEXT_CHARS > 0:
        sections.append(f"{after[:CITATION_CONTEXT_CHARS]}...")
    return "\n\n".join(section for section in sections if section)


def public_sources(matches: list[dict[str, Any]], all_chunks: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    chunk_lookup = {
        (str(chunk.get("driveId", "")), int(chunk.get("chunkIndex", 0) or 0)): str(chunk.get("content", ""))
        for chunk in all_chunks
    }
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
        source["excerpt"] = relevant_excerpt(citation_context(item, chunk_lookup), query, SOURCE_EXCERPT_CHARS)
        sources.append(source)
    return sources


def apply_source_evidence(sources: list[dict[str, Any]], evidence_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence_by_citation: dict[int, str] = {}
    for item in evidence_items:
        try:
            citation = int(item.get("citation", 0) or 0)
        except (TypeError, ValueError):
            continue
        evidence = normalize_source_evidence(item.get("evidence") or item.get("supports") or item.get("summary"))
        if citation > 0 and evidence:
            evidence_by_citation[citation] = evidence

    if not evidence_by_citation:
        return sources

    updated: list[dict[str, Any]] = []
    for source in sources:
        clone = dict(source)
        try:
            citation = int(clone.get("citation", 0) or 0)
        except (TypeError, ValueError):
            citation = 0
        if citation in evidence_by_citation:
            clone["excerpt"] = evidence_by_citation[citation]
            clone["evidenceBound"] = True
        updated.append(clone)
    return updated


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
    coverage = note_coverage(candidates)
    timings["rankMs"] = int((time.perf_counter() - rank_started_at) * 1000)

    judge_started_at = time.perf_counter()
    if AI_RETRIEVAL_MODE == "ai":
        ai_judgment = ai_select_context(query, candidates)
        semantic_confidence = str(ai_judgment.get("confidence", "low")).lower()
        if ai_judgment.get("selected") and semantic_confidence in {"high", "medium"}:
            coverage["level"] = semantic_confidence
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
        "sources": public_sources(ai_judgment["selected"], chunks, query),
        "aiJudgment": {
            "topic": ai_judgment["topic"],
            "confidence": ai_judgment["confidence"],
            "reason": ai_judgment["reason"],
            "noteCoverage": coverage,
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


def add_low_coverage_notice(answer: str, query: str, ai_judgment: dict[str, Any]) -> str:
    coverage = ai_judgment.get("noteCoverage") or {}
    if coverage.get("level") != "low":
        return answer
    if re.search(r"[\u4e00-\u9fff]", query):
        notice = "\u8bf4\u660e\uff1a\u73b0\u6709\u7b14\u8bb0\u4e0e\u8fd9\u4e2a\u95ee\u9898\u7684\u76f4\u63a5\u91cd\u5408\u5ea6\u8f83\u4f4e\uff0c\u4ee5\u4e0b\u56de\u7b54\u5c06\u4ee5\u7b14\u8bb0\u4e2d\u6700\u63a5\u8fd1\u7684\u539f\u5219\u4e3a\u8d77\u70b9\u8fdb\u884c\u63a8\u6f14\u3002"
    else:
        notice = "Note: The available notes have low direct overlap with this question. The answer below extrapolates from the closest principles in the notes."
    return f"{notice}\n\n{answer}"


def build_answer_prompt(query: str, matches: list[dict[str, Any]], ai_judgment: dict[str, Any], answer_mode: str = "mentor") -> str:
    mode = normalize_answer_mode(answer_mode)
    context = "\n\n---\n\n".join(
        "\n".join([
            f"[{index + 1}] {item.get('title', 'Untitled')}",
            f"source: {item.get('source', 'unknown')}",
            f"modified: {item.get('modifiedTime', 'unknown')}",
            f"source type: {item.get('sourceType', 'document')}",
            *(
                [f"other members' chat context (context only, not Brind evidence):\n{item.get('chatContext', '')}"]
                if item.get("chatContext") else []
            ),
            "Brind/mentor evidence:" if item.get("sourceType") == "mentor_chat" else "Document evidence:",
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
        "2. Use the Brind notes as the primary evidence when they directly address the question. Do not force unrelated note fragments into the answer.",
        "3. For stock, finance, or medical questions, provide educational analysis only. Do not give deterministic buy/sell, diagnosis, or medication instructions.",
        "4. Cite source numbers in the answer, using forms like [1] or [2].",
        "5. Do not reveal raw source passages or long quotes. Synthesize the answer from the sources instead.",
        "6. If the user asks for raw notes, full documents, exact transcripts, hidden file names, Drive paths, or bulk extraction, refuse that part and offer a concise synthesized summary instead.",
        "7. If the notes are incomplete, say so briefly, then give a cautious reasoned extension labeled as analysis rather than as a direct statement from Brind.",
        "8. Return JSON only. The answer field must contain the user-facing answer. The source_evidence field must explain what each cited source actually supports.",
        "9. For every source number cited in answer, add one source_evidence item with citation and evidence. Evidence must be a concise paraphrase, not a long quote, and must match the cited claim.",
        "10. Do not cite a source unless its source_evidence can clearly support the cited claim.",
        "11. In mentor_chat sources, only the Brind/mentor evidence is authoritative. Other members' chat context may clarify the question but must never be presented or cited as Brind's opinion.",
        "12. Curated document notes outrank mentor_chat sources. Use chat only as supplementary evidence; if they conflict, follow the curated notes and briefly acknowledge uncertainty.",
        "JSON schema: {\"answer\":\"...\",\"source_evidence\":[{\"citation\":1,\"evidence\":\"what this source supports, <=100 Chinese characters or <=55 English words\"}]}",
        "",
        "Answer mode rules:",
        *ANSWER_MODE_PROMPTS[mode],
        "",
        f"Retrieval mode/topic signal: {ai_judgment.get('topic', 'Not judged')}",
        f"Retrieval note: {ai_judgment.get('reason', '')}",
        f"User question: {query}",
        f"Knowledge-base coded-term equivalences: {coded_term_guidance(query)}",
        "",
        "Live Google Drive sources selected by AI:",
        context or "None",
    ])


def openai_answer(query: str, matches: list[dict[str, Any]], ai_judgment: dict[str, Any], answer_mode: str = "mentor") -> dict[str, Any] | None:
    if not OPENAI_API_KEY:
        return None
    raw = openai_request(build_answer_prompt(query, matches, ai_judgment, answer_mode))
    try:
        parsed = parse_json_object(raw)
    except Exception:
        return {"answer": raw, "source_evidence": []}

    answer = str(parsed.get("answer") or "").strip()
    evidence = parsed.get("source_evidence") or parsed.get("sources") or []
    if not isinstance(evidence, list):
        evidence = []
    return {
        "answer": answer or raw,
        "source_evidence": evidence,
    }


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
            source_evidence: list[dict[str, Any]] = []
            answer_started_at = time.perf_counter()
            try:
                answer_result = openai_answer(message, context["matches"], context["aiJudgment"], answer_mode)
                answer = answer_result.get("answer") if answer_result else None
                source_evidence = answer_result.get("source_evidence", []) if answer_result else []
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
            answer = add_low_coverage_notice(answer, message, context["aiJudgment"])
            sources = apply_source_evidence(context["sources"], source_evidence)
            timings = {
                **context["timings"],
                "answerMs": answer_ms,
                "totalMs": int((time.perf_counter() - request_started_at) * 1000),
            }
            json_response(self, {
                "answer": answer,
                "sources": sources,
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
