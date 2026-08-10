# SnP Checker — Product Requirements Document

**Product:** Standards & Practices (S&P) Script Review Tool
**Owner:** Content Team, Hoichoi
**Version:** 0.1 (MVP)
**Date:** 2026-07-30

---

## 1. Problem

Content team members manually review scripts against S&P guidelines before production. It's slow, inconsistent, and easy to miss issues. We want an LLM-assisted first pass that flags risky lines and returns an annotated document, so reviewers only spend time on the flagged parts.

## 2. Goals (MVP)

- One reviewer uploads a script (PDF / DOCX / TXT).
- The system checks the script against a stored S&P guideline document.
- Output: the same document returned with problem lines **highlighted** and a **comment** next to each explaining which guideline it violates and why.
- Simple auth so only team members can use it.

## 3. Non-Goals (MVP)

- No multi-user collaboration / real-time editing.
- No re-writing or auto-fixing the script.
- No versioning / project management.
- No mobile app.
- No integrations (Slack, Drive, etc.).

## 4. Users

- **Reviewer** — content team member uploads scripts, reads the annotated output.
- **Admin** — uploads / updates the S&P guideline document.

## 5. User Flow

1. User logs in (email + password).
2. Lands on dashboard → **Upload Script** button.
3. Selects file (PDF / DOCX / TXT), clicks **Analyze**.
4. Progress indicator while LLM runs.
5. Result screen shows:
   - The script text rendered inline.
   - Problem lines highlighted (color-coded by severity: red / amber).
   - Sidebar or inline comments explaining the violation and citing the guideline.
6. Download annotated DOCX (with tracked comments) or PDF.
7. History page lists past uploads for the logged-in user.

**Admin flow:** Admin page → upload / replace `snp-guidelines.pdf` (or paste text). This becomes the reference used in every future analysis.

## 6. Functional Requirements

### 6.1 Auth
- Email + password login.
- Session cookie.
- Admin role flag on user record.

### 6.2 Upload
- Accept `.pdf`, `.docx`, `.txt`.
- Max size: 20 MB.
- Store original in object storage; store text extraction in DB.

### 6.3 Text Extraction
- PDF → `pdf-parse` / `pdfplumber`.
- DOCX → `python-docx` / `mammoth`.
- TXT → read as-is.
- Preserve line numbers so we can map LLM output back to positions.

### 6.4 LLM Analysis
- Model: Claude (Sonnet 5 for MVP; configurable).
- Input: extracted script + S&P guidelines (system prompt / context).
- Output: structured JSON list of findings, each with:
  ```json
  {
    "line_start": 42,
    "line_end": 44,
    "excerpt": "...",
    "severity": "high" | "medium" | "low",
    "guideline_ref": "Section 3.2 — Violence",
    "reason": "Depicts graphic violence without narrative justification.",
    "suggestion": "Consider off-screen implication or tone-down."
  }
  ```
- Validate JSON schema before rendering.

### 6.5 Result Rendering
- Web viewer: script on left, findings list on right. Clicking a finding scrolls to and highlights the line(s).
- Downloadable annotated DOCX: uses Word's native comment feature so reviewers can accept / resolve.

### 6.6 History
- Table of past runs for the current user: filename, date, # findings, severity breakdown, links to view / download.

### 6.7 Admin
- Upload / replace guideline document.
- View list of users.

## 7. Non-Functional

- Runs on Railway (single service, Postgres addon).
- Analysis latency target: < 60s for a 30-page script.
- Uploaded files private to the uploader (except admin).
- Basic rate limit: 20 analyses / user / day.

## 8. Tech Stack (proposed)

- **Backend:** Python + FastAPI (good for LLM + file processing).
- **Frontend:** Next.js (React) — simple pages, fetches from FastAPI. Or bundled Jinja templates if we want to stay single-service.
- **DB:** Postgres (Railway addon).
- **File storage:** Railway volume for MVP; migrate to S3 later if needed.
- **LLM:** Anthropic Claude API (`claude-sonnet-5`).
- **Auth:** email/password with `passlib` bcrypt, `itsdangerous` signed session cookies.
- **Deploy:** Railway (Dockerfile or Nixpacks).

## 9. Data Model (rough)

```
users(id, email, password_hash, is_admin, created_at)
guidelines(id, version, content_text, uploaded_by, created_at)   -- only latest used
scripts(id, user_id, filename, mime, storage_path, text_extracted, created_at)
analyses(id, script_id, guideline_id, status, findings_json, created_at, completed_at)
```

## 10. Milestones

1. **M1 — Scaffold** (day 1): folder structure, FastAPI hello world, Postgres, auth stubs.
2. **M2 — Upload + Extract** (day 2): file upload, text extraction for all three formats.
3. **M3 — LLM pipeline** (day 3): guidelines storage, Claude call with structured output.
4. **M4 — Viewer** (day 4): web viewer with highlights + comments sidebar.
5. **M5 — DOCX annotated download** (day 5): generate `.docx` with Word comments.
6. **M6 — History + Admin** (day 6).
7. **M7 — Deploy to Railway** (day 7).

## 11. Open Questions

- Do we need multi-language support (Bengali scripts)? — assume **yes**, since Hoichoi is Bengali-first. Confirm.
- Should the guideline doc be one global doc, or per-genre? MVP = one global.
- Do reviewers need to add their own comments back into the doc, or just consume LLM output? MVP = consume only.
- Bengali PDF text extraction can be lossy — do we need OCR fallback? MVP = no, flag unsupported files.
