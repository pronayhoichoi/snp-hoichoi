# SnP Checker

Standards & Practices script review tool for OTT content teams. Upload a script (PDF / DOCX / TXT), Claude checks it against your S&P guidelines, and you get back an annotated `.docx` with native Word comments on every flagged line.

## Features

- Email + password auth (first signup = admin)
- Upload PDF / DOCX / TXT (max 20 MB)
- OCR fallback for scanned PDFs (Bengali + English via Tesseract)
- LLM analysis with structured findings (line ranges, severity, guideline reference, reason, suggestion)
- In-browser viewer: script left, findings sidebar right, click-to-scroll highlights
- Download `.docx` with real Word comments — reviewers can accept/resolve inline
- Admin page to edit/version S&P guidelines
- History page per user

## Stack

FastAPI · Jinja2 · HTMX · SQLAlchemy · Postgres (SQLite in dev) · Anthropic Claude · Tesseract OCR · python-docx

## Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit ANTHROPIC_API_KEY
# macOS system deps for OCR:
brew install tesseract tesseract-lang poppler
uvicorn app.main:app --reload
```

Open http://localhost:8000 · sign up (first account becomes admin) · go to **Guidelines**, paste your S&P doc, save · upload a script.

## Deploy to Railway

1. Push this folder to a Git repo.
2. In Railway: **New Project → Deploy from GitHub** → pick the repo.
3. Add a **Postgres** service; Railway sets `DATABASE_URL` for you.
4. Add env vars on the app service:
   - `ANTHROPIC_API_KEY` — your key
   - `SESSION_SECRET` — long random string
   - `ANTHROPIC_MODEL` — e.g. `claude-sonnet-5` (default)
5. Railway detects `railway.json` + `Dockerfile` and builds. Health check hits `/healthz`.
6. Add a volume mounted at `/app/storage` if you want uploads to survive deploys.

## Environment variables

| var | default | notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./snp.db` | Postgres URL in prod |
| `SESSION_SECRET` | `dev-secret-change-me` | change in prod |
| `ANTHROPIC_API_KEY` | — | required |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | |
| `STORAGE_DIR` | `./storage` | uploads + outputs |
| `MAX_UPLOAD_MB` | `20` | |

## Structure

```
app/
├── main.py          # FastAPI app, / and /history routes
├── config.py        # env config
├── db.py            # SQLAlchemy engine
├── auth.py          # bcrypt + signed cookie sessions
├── models/          # User, Guideline, Script, Analysis
├── routers/
│   ├── auth_routes.py    # /login /signup /logout
│   ├── script_routes.py  # /scripts/upload, /analyze, /result, /download
│   └── admin_routes.py   # /admin/guidelines
├── services/
│   ├── extract.py        # PDF/DOCX/TXT + OCR
│   ├── analyzer.py       # Claude call, structured JSON out
│   └── docx_writer.py    # annotated .docx with native comments
├── templates/       # Jinja2
└── static/          # CSS
```
