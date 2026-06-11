# Brind Mentor Drive Live

Private AI mentor prototype that reads the provided Google Drive folder at request time.

This version is designed to be GitHub-safe:

- No Brind documents are stored in the repo.
- No local `corpus.jsonl`, raw text dump, extracted notes, or sync report is created.
- The server reads Google Drive live using `GOOGLE_ACCESS_TOKEN`.
- Retrieved text only lives in memory for the current request.
- `.env`, `data/`, logs, and `node_modules/` are ignored by `.gitignore`.

## Run Locally

```powershell
cd C:\Users\mrand\Documents\Codex\2026-06-11\ai-mentor-google-drive-brind-openai\outputs\brind-mentor-mvp

$env:GOOGLE_DRIVE_FOLDER_ID="1qSD6wwFWTaJtZLVZ-pEHnLOjJXJbS8OC"
$env:GOOGLE_ACCESS_TOKEN="<google-oauth-access-token>"
$env:OPENAI_API_KEY="<openai-api-key>"
$env:OPENAI_MODEL="gpt-5.4-mini"

node server.mjs
```

Open:

```text
http://localhost:4173
```

If the port is busy:

```powershell
node server.mjs --port 4175
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

Currently supported without local storage:

- Native Google Docs, exported as plain text through Drive API
- Plain text / Markdown files

Skipped at runtime:

- PDF
- DOCX / DOC
- RTF
- Images / scanned files

Those skipped files are listed in the chat response. To support them without local storage, the next step is to add one of:

- Google Drive conversion to native Docs before reading
- in-memory DOCX/PDF parsers as server dependencies
- OCR for images/scanned PDFs
- OpenAI vector stores or another managed vector DB, if remote storage is acceptable

## GitHub Upload Checklist

Before pushing:

```powershell
git init
git add .gitignore .env.example package.json README.md server.mjs public
git status
```

Confirm these are **not** staged:

- `.env`
- `data/`
- any downloaded Drive folder
- any `corpus.jsonl`
- any extracted notes
- logs or screenshots containing private content

Then commit:

```powershell
git commit -m "Add stateless Drive-live Brind mentor MVP"
```

## Deployment Notes

For a hosted app, do not use a manually pasted short-lived access token. Use proper Google OAuth:

1. User signs in with Google.
2. Server stores refresh token securely in the hosting provider secret store or database.
3. Server requests short-lived access tokens as needed.
4. Drive text is fetched at request time.
5. No private notes are committed to GitHub.
