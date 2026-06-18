# MentorBrind-AI

A private AI mentor prototype built around Brind's notes, quotes, and records. The app does not sync Google Drive documents to local storage and does not commit any private corpus to GitHub. The Python backend reads the configured Google Drive folder into an in-memory index, asks AI to judge the topic semantically, selects relevant snippets, and generates an answer from those snippets.

## Current Capabilities

- Reads a configured Google Drive folder into a process-memory index with automatic expiry, manual refresh, and incremental reuse of unchanged files.
- Stores no Brind source documents, extracted text, local corpus, or knowledge-base snapshot.
- Supports Google Docs, TXT, Markdown, PDF, DOCX, RTF, and best-effort legacy `.doc` parsing.
- Uses rough text matching only to narrow candidates, then delegates topic judgment and final source selection to OpenAI.
- Supports either a manually provided Google access token or an in-app Google OAuth login.
- Supports an optional app access code for lightweight private sharing.
- Returns protected numbered citations with a controlled excerpt of up to 100 characters, while hiding Drive IDs, file paths, and full source text.
- Generates mentor-style answers inspired by the notes' ideas, rhythm, and reasoning style, while staying honest that it is not Brind.
- Treats stock, finance, and medical questions as educational analysis only, not as deterministic trading, investment, diagnosis, or medication instructions.

## Privacy Design

This repository is designed to be safe for a private GitHub repo because it only contains application code:

- `.env` is not committed.
- Google Drive files are not committed.
- No `corpus.jsonl`, local vector database, raw-text cache, or sync report is generated or committed.
- Retrieved Drive text exists only in server memory and expires with the process or cache TTL.
- OAuth access and refresh tokens are held in server memory only in the current prototype. They are not written to disk.
- User-facing chat responses do not expose retrieved chunks by default. The model sees sources server-side, but the API returns protected citation labels unless explicitly configured otherwise.
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
1RbZmNxR8Ga-rnDzckYhoEO8i7FiVigWj
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

The app automatically loads `.env` from the project root. For local preview, values in `.env` override same-named shell variables so stale terminal settings do not silently win.

## Manual Run

```powershell
cd C:\Users\mrand\Documents\Codex\2026-06-11\ai-mentor-google-drive-brind-openai\outputs\MentorBrind-AI

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

$env:GOOGLE_DRIVE_FOLDER_ID="1RbZmNxR8Ga-rnDzckYhoEO8i7FiVigWj"
$env:GOOGLE_ACCESS_TOKEN="<google-oauth-access-token>"
$env:GOOGLE_CLIENT_ID="<google-oauth-client-id>"
$env:GOOGLE_CLIENT_SECRET="<google-oauth-client-secret>"
$env:GOOGLE_REDIRECT_URI="http://localhost:4173/api/auth/google/callback"
$env:SESSION_MAX_AGE_SECONDS="2592000"
$env:APP_ACCESS_CODE="<optional-app-access-code>"
$env:OPENAI_API_KEY="<openai-api-key>"
$env:OPENAI_MODEL="gpt-5.4-mini"
$env:OPENAI_TIMEOUT_SECONDS="45"
$env:AI_RETRIEVAL_MODE="fast"
$env:MAX_CANDIDATES_FOR_AI="30"
$env:DRIVE_INDEX_TTL_SECONDS="900"

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
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Optional service account JSON credentials. Recommended for hosted use so users do not need Google OAuth. |
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` | Optional base64-encoded service account JSON. Use this if the host UI does not handle multiline JSON well. |
| `GOOGLE_ACCESS_TOKEN` | Optional manual Google OAuth access token with Drive read permission |
| `GOOGLE_CLIENT_ID` | Optional Google OAuth client ID for in-app sign-in |
| `GOOGLE_CLIENT_SECRET` | Optional Google OAuth client secret for in-app sign-in |
| `GOOGLE_REDIRECT_URI` | OAuth callback URL, default `http://localhost:4173/api/auth/google/callback` |
| `SESSION_MAX_AGE_SECONDS` | In-memory OAuth session lifetime, default 30 days |
| `APP_ACCESS_CODE` | Optional lightweight app passcode. If set, users must unlock the app before chat or Google OAuth. |
| `EXPOSE_SOURCE_METADATA` | Optional debug flag. Default `false`; when true, source titles/paths/modified times can be returned to the UI. |
| `EXPOSE_SOURCE_EXCERPTS` | Controls citation excerpt visibility. Default `true`; excerpts remain bounded by `SOURCE_EXCERPT_MAX_CHARS`. |
| `SOURCE_EXCERPT_MAX_CHARS` | Maximum visible citation excerpt length, including the ellipsis. Default `100`. |
| `OPENAI_API_KEY` | OpenAI API key for topic judgment and final answers |
| `OPENAI_MODEL` | Model used for judgment and answer generation |
| `OPENAI_TIMEOUT_SECONDS` | Maximum wait for each OpenAI request before falling back, default 45 seconds |
| `AI_RETRIEVAL_MODE` | `fast` skips the separate AI retrieval judge for lower latency; `ai` adds a semantic judge call before final answering |
| `COOKIE_SECURE` | Set `true` on HTTPS deployments so session cookies use the Secure flag. Keep `false` for local HTTP preview. |
| `HOST` | Bind host. Use `127.0.0.1` locally and `0.0.0.0` on hosted platforms. |
| `PORT` | Local server port, default `4173` |
| `MAX_FILES_PER_QUERY` | Maximum Drive files scanned while building the in-memory index |
| `MAX_CHUNKS_FOR_MODEL` | Maximum snippets passed into the final answer prompt |
| `MAX_CANDIDATES_FOR_AI` | Candidate snippets passed to AI for semantic judging |
| `DRIVE_INDEX_TTL_SECONDS` | In-memory Drive index lifetime before automatic rebuild, default 15 minutes |

## Retrieval Flow

The backend keeps a Drive-derived index in process memory:

1. The first chat request or manual refresh lists files in the configured folder.
2. Supported files are exported or parsed in memory.
3. The in-memory index is reused until `DRIVE_INDEX_TTL_SECONDS` expires.
4. On refresh or TTL expiry, Drive is listed again, but unchanged files reuse their existing in-memory chunks.
5. Rough text matching reduces the candidate snippet set.
6. In `fast` mode, the top candidates go directly into the final answer prompt. In `ai` mode, OpenAI first semantically judges and narrows candidates.
7. The answer is generated from the selected snippets.

Use the sidebar **Refresh** button or `POST /api/index/refresh` to pick up Google Drive changes immediately. Refresh runs in a background thread so hosted platforms do not time out while PDFs and DOCX files are parsed. The sidebar shows **Building** during indexing and reports how many files were read, reused, changed, removed, or skipped. Otherwise, updates are picked up automatically after the TTL.

The rough match is only a latency and token-cost reducer. It is not treated as a topic classifier. In `fast` mode the app avoids a separate topic label and lets the final model reason over selected context. In `ai` mode, topic judgment is semantic and no longer depends on hard-coded keyword rules.

Every answer uses the notes as its primary reasoning frame before extending outward. When direct note overlap is low, the backend prepends a disclosure and requires the model to distinguish note-supported claims from reasoned extensions.

If `OPENAI_API_KEY` is not configured, the app clearly reports that AI topic judgment is disabled instead of inventing a topic label.

## Google Drive Access

For hosted use, prefer a Google service account:

1. Create a Google Cloud service account.
2. Create a JSON key for that service account.
3. Share the target Google Drive folder with the service account `client_email` as a viewer.
4. Put the full JSON key into `GOOGLE_SERVICE_ACCOUNT_JSON`, or a base64-encoded copy into `GOOGLE_SERVICE_ACCOUNT_JSON_B64`.

When service account credentials are configured, the backend reads Drive automatically after users unlock the app. Users do not need to click **Connect Google Drive**.

For Render, `GOOGLE_SERVICE_ACCOUNT_JSON_B64` is usually easier because it avoids multiline secret formatting issues. On Windows PowerShell:

```powershell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-Content .\service-account.json -Raw)))
```

## Google OAuth Login

For local development or fallback use, you can either paste a short-lived `GOOGLE_ACCESS_TOKEN` into the environment or configure Google OAuth and use the in-app **Connect Google Drive** button.

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
- The index is process-memory only. Restarting the server clears it.
- Google Drive updates are visible after manual refresh or after `DRIVE_INDEX_TTL_SECONDS` expires.

## API

### `GET /api/health`

Returns non-secret runtime diagnostics, including missing configuration, recommended setup actions, uptime, and whether the app is reading from `.env`.

### `GET /api/sources`

Returns service status, including whether Google and OpenAI credentials are configured, whether local document storage is used, and which file types are supported.

### `GET /api/index/status`

Returns non-secret in-memory index status, including whether the index is cached, file/chunk counts, TTL, last refresh age, and incremental refresh stats.

### `POST /api/index/refresh`

Starts a background Google Drive listing and incrementally rebuilds the process-memory index. Unchanged files reuse existing in-memory chunks. Poll `GET /api/index/status` until `refreshRunning` is `false` and `cached` is `true`.

### `POST /api/chat`

Request:

```json
{
  "message": "Huachen Equipment stock logic",
  "mode": "mentor"
}
```

Supported answer modes:

- `mentor`: conclusion first, then Brind-style mechanism analysis.
- `strict citation`: every key judgment needs a citation; unsupported claims must say `Not found in the notes`.
- `beginner explanation`: fewer technical terms, layered explanation, and a simple analogy when useful.
- `challenge assumptions`: hidden assumptions first, then counterexamples, risk boundaries, and final judgment.

Response includes:

- `answer`: mentor-style answer
- `answerMode`: normalized answer mode used by the backend
- `sources`: protected AI-selected citations with excerpts capped at 100 characters by default; file metadata remains hidden.
- `aiJudgment`: AI-generated topic, confidence, reason, and note coverage level
- `stats`: scanned file count, text chunk count, skipped file count, and index cache status
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

The app can be deployed as a single Python web service. It uses the platform-provided `PORT` and should bind to `HOST=0.0.0.0` in production.

### Render Quick Start

1. Push this repository to GitHub.
2. In Render, create a new **Web Service** from the GitHub repo, or use the included `render.yaml` as a blueprint.
3. Use:

```text
Build Command: pip install -r requirements.txt
Start Command: python app.py
Health Check Path: /api/health
Instance Type: Free
```

4. Set these environment variables in Render:

```text
HOST=0.0.0.0
APP_BASE_URL=https://your-render-service.onrender.com
GOOGLE_DRIVE_FOLDER_ID=1RbZmNxR8Ga-rnDzckYhoEO8i7FiVigWj
GOOGLE_SERVICE_ACCOUNT_JSON_B64=<base64-service-account-json>
APP_ACCESS_CODE=<private-app-passcode>
COOKIE_SECURE=true
OPENAI_API_KEY=<openai-api-key>
OPENAI_MODEL=gpt-5.4-mini
OPENAI_TIMEOUT_SECONDS=45
AI_RETRIEVAL_MODE=fast
DRIVE_INDEX_TTL_SECONDS=900
```

If you use Google OAuth fallback instead of service account credentials, also set:

```text
GOOGLE_CLIENT_ID=<google-oauth-client-id>
GOOGLE_CLIENT_SECRET=<google-oauth-client-secret>
GOOGLE_REDIRECT_URI=https://your-render-service.onrender.com/api/auth/google/callback
```

5. If using Google OAuth fallback, add this authorized redirect URI in Google Cloud Console:

```text
https://your-render-service.onrender.com/api/auth/google/callback
```

6. If the OAuth app is still in Testing mode, add every Google account that needs access as a test user.
7. Deploy, open the Render URL, enter the app access code, then click **Connect Google Drive**.

### Production Gaps

The current hosted prototype still keeps OAuth sessions and the Drive index in process memory. That is acceptable for a private test deployment, but a production version should move token storage to a secure secret store or encrypted database:

1. The user signs in with Google.
2. The server stores the refresh token securely.
3. The server requests short-lived access tokens as needed.
4. The server maintains either an in-memory or secure remote index.
5. Private notes are never written to GitHub or public logs.

## Roadmap

- Add OCR support for scanned PDFs and images.
- Add an optional secure remote index for better latency on large Drive folders.
- Add user access controls so friends can use the app without direct access to raw Drive files.
- Improve citation display with clearer file and snippet references.
