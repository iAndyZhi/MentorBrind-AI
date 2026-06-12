# MentorBrind-AI

A private AI mentor prototype built around Brind's notes, quotes, and records. The app does not sync Google Drive documents to local storage and does not commit any private corpus to GitHub. On each chat request, the Python backend reads the configured Google Drive folder live, asks AI to judge the topic semantically, selects relevant snippets, and generates an answer from those snippets.

## Current Capabilities

- Reads a configured Google Drive folder at request time.
- Stores no Brind source documents, extracted text, local corpus, or knowledge-base snapshot.
- Supports Google Docs, TXT, Markdown, PDF, DOCX, RTF, and best-effort legacy `.doc` parsing.
- Uses rough text matching only to narrow candidates, then delegates topic judgment and final source selection to OpenAI.
- Supports either a manually provided Google access token or an in-app Google OAuth login.
- Supports an optional app access code for lightweight private sharing.
- Returns numbered citations and short snippet previews for the sources selected by AI.
- Generates mentor-style answers inspired by the notes' ideas, rhythm, and reasoning style, while staying honest that it is not Brind.
- Treats stock, finance, and medical questions as educational analysis only, not as deterministic trading, investment, diagnosis, or medication instructions.

## Privacy Design

This repository is designed to be safe for a private GitHub repo because it only contains application code:

- `.env` is not committed.
- Google Drive files are not committed.
- No `corpus.jsonl`, local vector database, raw-text cache, or sync report is generated or committed.
- Retrieved Drive text exists only in memory during the current request.
- OAuth access and refresh tokens are held in server memory only in the current prototype. They are not written to disk.
- `.gitignore` excludes virtual environments, caches, logs, `data/`, and other local artifacts.

## Requirements

- Python 3.11 or newer
- A Google OAuth access token with read-only Drive permission
- An OpenAI API key

Recommended Google Drive scope:

```text
https://www.googleapis.com/auth/drive.readonly
```

Default Google Drive folder ID:

```text
1qSD6wwFWTaJtZLVZ-pEHnLOjJXJbS8OC
```

## Preview Locally

The easiest preview path on Windows is:

```powershell
cd C:\Users\mrand\Documents\Codex\2026-06-11\ai-mentor-google-drive-brind-openai\outputs\MentorBrind-AI
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
powershell -ExecutionPolicy Bypass -File .\scripts\preview.ps1
```

Then open:

```text
http://localhost:4173
```

The preview script tries, in order:

- `PYTHON` environment variable, if set
- `.venv\Scripts\python.exe`
- `py`
- `python`
- Codex Desktop's bundled Python runtime

Edit `.env` with your local credentials when you want live chat. If you only want to preview the UI, credentials are not required. Chat requests require Google Drive access and, for AI answers, an OpenAI key.

The app automatically loads `.env` from the project root. Real environment variables still take priority over values in `.env`.

## Manual Run

```powershell
cd C:\Users\mrand\Documents\Codex\2026-06-11\ai-mentor-google-drive-brind-openai\outputs\MentorBrind-AI

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

$env:GOOGLE_DRIVE_FOLDER_ID="1qSD6wwFWTaJtZLVZ-pEHnLOjJXJbS8OC"
$env:GOOGLE_ACCESS_TOKEN="<google-oauth-access-token>"
$env:GOOGLE_CLIENT_ID="<google-oauth-client-id>"
$env:GOOGLE_CLIENT_SECRET="<google-oauth-client-secret>"
$env:GOOGLE_REDIRECT_URI="http://localhost:4173/api/auth/google/callback"
$env:SESSION_MAX_AGE_SECONDS="2592000"
$env:APP_ACCESS_CODE="<optional-app-access-code>"
$env:OPENAI_API_KEY="<openai-api-key>"
$env:OPENAI_MODEL="gpt-5.4-mini"
$env:MAX_CANDIDATES_FOR_AI="30"

.\.venv\Scripts\python.exe app.py
```

If your system does not have `python` or `py` on PATH, install Python 3.11+ or set `PYTHON` to a full `python.exe` path before using `scripts\preview.ps1`.

Open:

```text
http://localhost:4173
```

If the port is busy:

```powershell
$env:PORT="4175"
.\.venv\Scripts\python app.py
```

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `GOOGLE_DRIVE_FOLDER_ID` | Google Drive folder ID to read from |
| `GOOGLE_ACCESS_TOKEN` | Optional manual Google OAuth access token with Drive read permission |
| `GOOGLE_CLIENT_ID` | Optional Google OAuth client ID for in-app sign-in |
| `GOOGLE_CLIENT_SECRET` | Optional Google OAuth client secret for in-app sign-in |
| `GOOGLE_REDIRECT_URI` | OAuth callback URL, default `http://localhost:4173/api/auth/google/callback` |
| `SESSION_MAX_AGE_SECONDS` | In-memory OAuth session lifetime, default 30 days |
| `APP_ACCESS_CODE` | Optional lightweight app passcode. If set, users must unlock the app before chat or Google OAuth. |
| `OPENAI_API_KEY` | OpenAI API key for topic judgment and final answers |
| `OPENAI_MODEL` | Model used for judgment and answer generation |
| `PORT` | Local server port, default `4173` |
| `MAX_FILES_PER_QUERY` | Maximum Drive files scanned per request |
| `MAX_CHUNKS_FOR_MODEL` | Maximum snippets passed into the final answer prompt |
| `MAX_CANDIDATES_FOR_AI` | Candidate snippets passed to AI for semantic judging |

## Retrieval Flow

Every chat request reads Drive live:

1. The backend lists files in the configured folder.
2. Supported files are exported or parsed in memory.
3. Rough text matching reduces the candidate snippet set.
4. OpenAI semantically judges the user's real topic and chooses relevant snippets.
5. The answer is generated from the AI-selected snippets.

The rough match is only a token-cost reducer. It is not treated as a topic classifier. Topic judgment no longer depends on hard-coded keyword rules, which avoids errors such as classifying a stock question as a medical or psychology topic.

If `OPENAI_API_KEY` is not configured, the app clearly reports that AI topic judgment is disabled instead of inventing a topic label.

## Google OAuth Login

For local development, you can either paste a short-lived `GOOGLE_ACCESS_TOKEN` into the environment or configure Google OAuth and use the in-app **Connect Google Drive** button.

To use OAuth:

1. Create an OAuth client in Google Cloud.
2. Add this authorized redirect URI:

```text
http://localhost:4173/api/auth/google/callback
```

3. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
4. Start the app and click **Connect Google Drive** in the sidebar.

In this prototype, OAuth tokens are kept in process memory and associated with an HttpOnly session cookie. The app refreshes expired Google access tokens in memory when a refresh token is available. Restarting the server clears all sessions and requires signing in again.

## Optional App Access Code

Set `APP_ACCESS_CODE` to add a simple passcode gate for early private sharing. When it is set, users must unlock the app before they can start Google OAuth or send chat requests.

This is intentionally lightweight and stored in process memory. It is useful for a private prototype, but it is not a replacement for production user accounts, audit logs, rate limits, or a proper authorization system.

## Supported File Types

Currently supported:

- Google Docs, exported as plain text
- `.txt`
- `.md` / `.markdown`
- `.pdf`, parsed in memory with `pypdf`
- `.docx`, parsed in memory through zip/xml
- `.rtf`, parsed with a lightweight text cleaner
- `.doc`, best-effort text recovery

Limitations:

- Images, handwriting, and scanned PDFs need OCR, which is not implemented yet.
- Legacy `.doc` parsing is unreliable; convert to `.docx` or Google Docs when possible.
- There is no persistent index yet, so each request scans Drive again. This means Drive updates are naturally reflected, but large folders may be slower.

## API

### `GET /api/sources`

Returns service status, including whether Google and OpenAI credentials are configured, whether local document storage is used, and which file types are supported.

### `POST /api/chat`

Request:

```json
{
  "message": "Huachen Equipment stock logic"
}
```

Response includes:

- `answer`: mentor-style answer
- `sources`: AI-selected source snippets with citation number, file path, modified time, and short excerpt
- `aiJudgment`: AI-generated topic, confidence, and reason
- `stats`: scanned file count, text chunk count, skipped file count
- `skipped`: files that could not be read and the reason

## GitHub Safety Checklist

Before committing:

```powershell
git status
```

Do not commit:

- `.env`
- `.venv/`
- `data/`
- any downloaded Drive folder
- any `corpus.jsonl`
- any extracted Brind source text
- logs or screenshots containing private content

## Deployment Notes

The current prototype includes a lightweight in-memory OAuth flow, which is fine for local development. For a hosted app, move token storage to a secure secret store or encrypted database:

1. The user signs in with Google.
2. The server stores the refresh token securely.
3. The server requests short-lived access tokens as needed.
4. Each chat request reads Drive live.
5. Private notes are never written to GitHub or public logs.

## Roadmap

- Add full Google OAuth login instead of manual access tokens.
- Add OCR support for scanned PDFs and images.
- Add an optional secure remote index for better latency on large Drive folders.
- Add user access controls so friends can use the app without direct access to raw Drive files.
- Improve citation display with clearer file and snippet references.
