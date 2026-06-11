# Brind Mentor Drive Live

Private AI mentor prototype that reads the provided Google Drive folder at request time.

This version is designed to be GitHub-safe:

- No Brind documents are stored in the repo.
- No local `corpus.jsonl`, raw text dump, extracted notes, or sync report is created.
- The Python backend reads Google Drive live using `GOOGLE_ACCESS_TOKEN`.
- Retrieved text only lives in memory for the current request.
- `.env`, `data/`, logs, virtualenvs, and caches are ignored by `.gitignore`.

## Run Locally

```powershell
cd C:\Users\mrand\Documents\Codex\2026-06-11\ai-mentor-google-drive-brind-openai\outputs\MentorBrind-AI

py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

$env:GOOGLE_DRIVE_FOLDER_ID="1qSD6wwFWTaJtZLVZ-pEHnLOjJXJbS8OC"
$env:GOOGLE_ACCESS_TOKEN="<google-oauth-access-token>"
$env:OPENAI_API_KEY="<openai-api-key>"
$env:OPENAI_MODEL="gpt-5.4-mini"

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

## Google Drive Scope

Use a Google OAuth access token with read-only Drive permission, ideally:

```text
https://www.googleapis.com/auth/drive.readonly
```

The default folder is:

```text
1qSD6wwFWTaJtZLVZ-pEHnLOjJXJbS8OC
```

## What It Can Read Live

Currently supported without local document storage:

- Native Google Docs, exported as plain text through Drive API
- Plain text / Markdown files
- PDF files, parsed in memory with `pypdf`
- DOCX files, parsed in memory with Python zip/xml
- RTF files, parsed in memory with a lightweight cleaner
- Legacy `.doc`, best-effort text recovery

Still limited:

- Images and scanned PDFs need OCR.
- Legacy `.doc` is unreliable; convert to `.docx` or Google Docs when possible.

Skipped files are listed in the chat response with reasons. The app does not write extracted text to disk.

## GitHub Upload Checklist

Before pushing:

```powershell
git status
```

Confirm these are **not** staged:

- `.env`
- `.venv/`
- `data/`
- any downloaded Drive folder
- any `corpus.jsonl`
- any extracted notes
- logs or screenshots containing private content

## Deployment Notes

For a hosted app, do not use a manually pasted short-lived access token. Use proper Google OAuth:

1. User signs in with Google.
2. Server stores refresh token securely in the hosting provider secret store or database.
3. Server requests short-lived access tokens as needed.
4. Drive text is fetched at request time.
5. No private notes are committed to GitHub.
