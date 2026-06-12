# MentorBrind-AI

A private AI mentor prototype built around Brind's notes, quotes, and records. The app does not sync Google Drive documents to local storage and does not commit any private corpus to GitHub. On each chat request, the Python backend reads the configured Google Drive folder live, asks AI to judge the topic semantically, selects relevant snippets, and generates an answer from those snippets.

## Current Capabilities

- Reads a configured Google Drive folder at request time.
- Stores no Brind source documents, extracted text, local corpus, or knowledge-base snapshot.
- Supports Google Docs, TXT, Markdown, PDF, DOCX, RTF, and best-effort legacy `.doc` parsing.
- Uses rough text matching only to narrow candidates, then delegates topic judgment and final source selection to OpenAI.
- Generates mentor-style answers inspired by the notes' ideas, rhythm, and reasoning style, while staying honest that it is not Brind.
- Treats stock, finance, and medical questions as educational analysis only, not as deterministic trading, investment, diagnosis, or medication instructions.

## Privacy Design

This repository is designed to be safe for a private GitHub repo because it only contains application code:

- `.env` is not committed.
- Google Drive files are not committed.
- No `corpus.jsonl`, local vector database, raw-text cache, or sync report is generated or committed.
- Retrieved Drive text exists only in memory during the current request.
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

## Run Locally

```powershell
cd C:\Users\mrand\Documents\Codex\2026-06-11\ai-mentor-google-drive-brind-openai\outputs\MentorBrind-AI

py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

$env:GOOGLE_DRIVE_FOLDER_ID="1qSD6wwFWTaJtZLVZ-pEHnLOjJXJbS8OC"
$env:GOOGLE_ACCESS_TOKEN="<google-oauth-access-token>"
$env:OPENAI_API_KEY="<openai-api-key>"
$env:OPENAI_MODEL="gpt-5.4-mini"
$env:MAX_CANDIDATES_FOR_AI="30"

.\.venv\Scripts\python app.py
```

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
| `GOOGLE_ACCESS_TOKEN` | Google OAuth access token with Drive read permission |
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
- `sources`: AI-selected source snippets
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

The current prototype uses a manually provided `GOOGLE_ACCESS_TOKEN`, which is fine for local development. For a hosted app, replace it with a proper Google OAuth flow:

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
