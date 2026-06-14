from __future__ import annotations

import json
import os
import secrets
import time
import urllib.parse

import streamlit as st

import app as mentor


def apply_streamlit_secrets() -> None:
    for key, value in st.secrets.items():
        if isinstance(value, (str, int, float, bool)):
            os.environ[str(key)] = str(value)

    mentor.HOST = os.getenv("HOST", mentor.HOST)
    mentor.PORT = int(os.getenv("PORT", str(mentor.PORT)))
    mentor.APP_BASE_URL = os.getenv("APP_BASE_URL", mentor.APP_BASE_URL).rstrip("/")
    mentor.DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", mentor.DRIVE_FOLDER_ID)
    mentor.GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    mentor.GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    mentor.GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    mentor.GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", mentor.APP_BASE_URL)
    mentor.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    mentor.OPENAI_MODEL = os.getenv("OPENAI_MODEL", mentor.OPENAI_MODEL)
    mentor.APP_ACCESS_CODE = os.getenv("APP_ACCESS_CODE", mentor.APP_ACCESS_CODE).strip()
    mentor.OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", str(mentor.OPENAI_TIMEOUT_SECONDS)))
    mentor.AI_RETRIEVAL_MODE = os.getenv("AI_RETRIEVAL_MODE", mentor.AI_RETRIEVAL_MODE).strip().lower()
    mentor.DRIVE_INDEX_TTL_SECONDS = int(os.getenv("DRIVE_INDEX_TTL_SECONDS", str(mentor.DRIVE_INDEX_TTL_SECONDS)))


def query_value(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        return value[0] if value else ""
    return str(value or "")


def google_access_token() -> str:
    if mentor.GOOGLE_ACCESS_TOKEN:
        return mentor.GOOGLE_ACCESS_TOKEN
    token = st.session_state.get("google_access_token", "")
    expires_at = st.session_state.get("google_access_expires_at", 0)
    if token and expires_at > time.time():
        return token
    return ""


def google_auth_url() -> str:
    state = secrets.token_urlsafe(24)
    st.session_state.google_oauth_state = state
    params = {
        "client_id": mentor.GOOGLE_CLIENT_ID,
        "redirect_uri": mentor.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": mentor.DRIVE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


def complete_google_oauth() -> None:
    code = query_value("code")
    state = query_value("state")
    if not code:
        return
    if not state or state != st.session_state.get("google_oauth_state"):
        st.error("Google OAuth state did not match. Start Google login again.")
        return
    payload = urllib.parse.urlencode({
        "client_id": mentor.GOOGLE_CLIENT_ID,
        "client_secret": mentor.GOOGLE_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": mentor.GOOGLE_REDIRECT_URI,
    }).encode("utf-8")
    request = mentor.urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with mentor.urllib.request.urlopen(request, timeout=60) as response:
            token_data = json.loads(response.read().decode("utf-8"))
    except mentor.urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        st.error(f"Google OAuth failed: {detail}")
        return

    access_token = token_data.get("access_token", "")
    if not access_token:
        st.error("Google OAuth did not return an access token.")
        return
    expires_in = int(token_data.get("expires_in", 3600))
    st.session_state.google_access_token = access_token
    st.session_state.google_access_expires_at = time.time() + max(60, expires_in - 60)
    st.query_params.clear()
    st.rerun()


def require_app_access() -> bool:
    if not mentor.APP_ACCESS_CODE:
        return True
    if st.session_state.get("app_unlocked"):
        return True
    with st.sidebar:
        st.subheader("Access")
        code = st.text_input("Access code", type="password")
        if st.button("Unlock", type="primary"):
            if secrets.compare_digest(code.strip(), mentor.APP_ACCESS_CODE):
                st.session_state.app_unlocked = True
                st.rerun()
            st.error("Invalid access code.")
    return False


def render_sidebar() -> None:
    token = google_access_token()
    index = mentor.index_status_payload()
    with st.sidebar:
        st.title("Brind Mentor")
        st.caption("Google Drive index stays in server memory. Source text is not shown to users.")
        st.metric("Google Drive", "Connected" if token else "Not connected")
        st.metric("OpenAI", "Configured" if mentor.OPENAI_API_KEY else "Missing")
        st.metric("Index", "Cached" if index["cached"] else "Empty")
        st.metric("Chunks", index["textChunks"])
        stats = index.get("refreshStats") or {}
        if stats:
            st.caption(
                f"read {stats.get('readFiles', 0)} / reused {stats.get('reusedFiles', 0)} / "
                f"changed {stats.get('changedFiles', 0)} / skipped {stats.get('unsupportedOrFailedFiles', 0)}"
            )

        if mentor.GOOGLE_CLIENT_ID and mentor.GOOGLE_CLIENT_SECRET and not token:
            st.link_button("Connect Google Drive", google_auth_url())
        if token and st.button("Refresh Drive index"):
            with st.spinner("Refreshing Google Drive index..."):
                mentor.get_drive_index(token, force_refresh=True)
            st.rerun()
        if token and st.button("Disconnect Google Drive"):
            st.session_state.google_access_token = ""
            st.session_state.google_access_expires_at = 0
            st.rerun()


def answer_question(message: str) -> dict[str, object]:
    token = google_access_token()
    if not token:
        raise mentor.AppError("Google Drive is not connected.")
    started_at = time.perf_counter()
    context = mentor.collect_drive_context(token, message)
    answer_started_at = time.perf_counter()
    answer_error = ""
    try:
        answer = mentor.openai_answer(message, context["matches"], context["aiJudgment"])
    except Exception as exc:
        answer_error = str(exc)
        answer = None
    answer_ms = int((time.perf_counter() - answer_started_at) * 1000)
    if not answer:
        answer = mentor.fallback_answer(message, context["matches"], context["skipped"], context["aiJudgment"])
        if answer_error:
            answer = f"{answer}\n\nAnswer generation error: {answer_error}"
    timings = {
        **context["timings"],
        "answerMs": answer_ms,
        "totalMs": int((time.perf_counter() - started_at) * 1000),
    }
    return {
        "answer": answer,
        "sources": context["sources"],
        "skipped": context["skipped"][:20],
        "aiJudgment": context["aiJudgment"],
        "answerError": answer_error,
        "stats": {
            "scannedFiles": context["scannedFiles"],
            "textChunks": context["textChunks"],
            "skippedFiles": len(context["skipped"]),
            "cache": context["cache"],
            "timings": timings,
        },
        "model": mentor.OPENAI_MODEL if mentor.OPENAI_API_KEY else "python-drive-live-demo",
    }


def format_timing(timings: dict[str, int]) -> str:
    def fmt(ms: int) -> str:
        return f"{ms}ms" if ms < 1000 else f"{ms / 1000:.1f}s"

    return " / ".join([
        f"total {fmt(timings.get('totalMs', 0))}",
        f"index {fmt(timings.get('indexMs', 0))}",
        f"rank {fmt(timings.get('rankMs', 0))}",
        f"judge {fmt(timings.get('judgeMs', 0))}",
        f"answer {fmt(timings.get('answerMs', 0))}",
    ])


def main() -> None:
    st.set_page_config(page_title="Brind Mentor", layout="wide")
    apply_streamlit_secrets()
    complete_google_oauth()

    if not require_app_access():
        st.stop()

    render_sidebar()
    st.title("Brind Mentor")

    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "Ask about stocks, medicine, psychology, AI, or Brind-style course ideas."}
        ]

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Ask a question")
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Checking the Drive index and asking the model..."):
            try:
                result = answer_question(prompt)
                content = f"{result['answer']}\n\nModel: {result['model']}\n\nTiming: {format_timing(result['stats']['timings'])}"
            except Exception as exc:
                content = f"Error: {exc}"
            st.markdown(content)
    st.session_state.messages.append({"role": "assistant", "content": content})


if __name__ == "__main__":
    main()
