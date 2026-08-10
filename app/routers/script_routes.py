import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_user
from app.config import settings
from app.db import get_db, SessionLocal
from app.models.models import Script, User, Guideline, Analysis
from app.services.extract import extract
from app.services.analyzer import analyze
from app.services.docx_writer import build_annotated_docx

log = logging.getLogger(__name__)

router = APIRouter(prefix="/scripts", tags=["scripts"])
templates = Jinja2Templates(directory="app/templates")

ALLOWED_EXT = {".pdf", ".docx", ".txt"}


def _active_guideline(db: Session) -> Guideline | None:
    return (
        db.query(Guideline)
        .filter(Guideline.is_active.is_(True))
        .order_by(Guideline.version.desc())
        .first()
    )


@router.post("/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    filename = file.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    guideline = _active_guideline(db)
    if not guideline:
        raise HTTPException(400, "No S&P guidelines have been set. Ask an admin to add them.")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    upload_dir = Path(settings.storage_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = upload_dir / stored_name

    total = 0
    with open(stored_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                f.close()
                os.unlink(stored_path)
                raise HTTPException(400, f"File exceeds {settings.max_upload_mb} MB limit")
            f.write(chunk)

    script = Script(
        user_id=user.id,
        filename=filename,
        mime=file.content_type or "",
        storage_path=str(stored_path),
        text_extracted="",
        ocr_used=False,
    )
    db.add(script)
    db.commit()
    db.refresh(script)

    analysis = Analysis(
        script_id=script.id,
        guideline_id=guideline.id,
        status="extracting",
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    threading.Thread(
        target=_process_bg,
        args=(script.id, analysis.id, guideline.content_text, file.content_type or ""),
        daemon=True,
    ).start()

    return RedirectResponse(f"/scripts/{script.id}/status/{analysis.id}", status_code=302)


def _process_bg(script_id: int, analysis_id: int, guidelines_text: str, mime: str) -> None:
    """Do extract() and analyze() off the request thread so the HTTP response
    returns immediately regardless of file size."""
    db = SessionLocal()
    try:
        script = db.get(Script, script_id)
        analysis = db.get(Analysis, analysis_id)
        if not script or not analysis:
            log.error("bg: script %s / analysis %s missing", script_id, analysis_id)
            return

        try:
            log.info("bg: extracting script %d (%s)", script.id, script.filename)
            text, ocr_used = extract(script.storage_path, mime)
            if not text.strip():
                raise ValueError("No text could be extracted from this file")
            script.text_extracted = text
            script.ocr_used = ocr_used
            analysis.status = "running"
            db.commit()
            log.info("bg: extracted %d chars from script %d, starting analysis", len(text), script.id)

            result = analyze(text, guidelines_text)
            analysis.findings_json = result
            analysis.status = "done"
            analysis.completed_at = datetime.utcnow()
            log.info("bg: analysis %d done, %d findings", analysis.id, len(result.get("findings", [])))
        except Exception as e:
            log.exception("bg: script %d / analysis %d failed", script_id, analysis_id)
            analysis.status = "error"
            analysis.error = str(e)[:2000]
        db.commit()
    finally:
        db.close()


@router.get("/{script_id}/analyze")
def analyze_alias(
    script_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Legacy redirect: uploads now go straight to /status; find latest for this script."""
    script = db.get(Script, script_id)
    if not script or script.user_id != user.id:
        raise HTTPException(404)
    latest = (
        db.query(Analysis)
        .filter(Analysis.script_id == script.id)
        .order_by(Analysis.created_at.desc())
        .first()
    )
    if not latest:
        raise HTTPException(404, "No analysis for this script")
    if latest.status == "done":
        return RedirectResponse(f"/scripts/{script.id}/result/{latest.id}", status_code=302)
    return RedirectResponse(f"/scripts/{script.id}/status/{latest.id}", status_code=302)


@router.get("/{script_id}/status/{analysis_id}", response_class=HTMLResponse)
def status_page(
    script_id: int,
    analysis_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    script = db.get(Script, script_id)
    analysis = db.get(Analysis, analysis_id)
    if not script or script.user_id != user.id or not analysis or analysis.script_id != script.id:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "analyze_pending.html",
        {"user": user, "script": script, "analysis": analysis},
    )


@router.get("/{script_id}/status/{analysis_id}/json")
def status_json(
    script_id: int,
    analysis_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    script = db.get(Script, script_id)
    analysis = db.get(Analysis, analysis_id)
    if not script or script.user_id != user.id or not analysis or analysis.script_id != script.id:
        raise HTTPException(404)
    return JSONResponse({
        "status": analysis.status,
        "error": analysis.error or None,
        "result_url": f"/scripts/{script.id}/result/{analysis.id}" if analysis.status == "done" else None,
    })


@router.get("/{script_id}/result/{analysis_id}", response_class=HTMLResponse)
def view_result(
    script_id: int,
    analysis_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    script = db.get(Script, script_id)
    analysis = db.get(Analysis, analysis_id)
    if not script or script.user_id != user.id or not analysis or analysis.script_id != script.id:
        raise HTTPException(404)
    if analysis.status != "done":
        return RedirectResponse(f"/scripts/{script.id}/status/{analysis.id}", status_code=302)

    lines = script.text_extracted.splitlines()
    findings = (analysis.findings_json or {}).get("findings", [])

    flags_by_line: dict[int, list[dict]] = {}
    for i, f in enumerate(findings):
        for ln in range(f["line_start"], f["line_end"] + 1):
            flags_by_line.setdefault(ln, []).append({**f, "idx": i})

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "user": user,
            "script": script,
            "analysis": analysis,
            "lines": lines,
            "findings": findings,
            "flags_by_line": flags_by_line,
        },
    )


@router.get("/{script_id}/download/{analysis_id}")
def download_annotated(
    script_id: int,
    analysis_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    script = db.get(Script, script_id)
    analysis = db.get(Analysis, analysis_id)
    if not script or script.user_id != user.id or not analysis or analysis.script_id != script.id:
        raise HTTPException(404)

    findings = (analysis.findings_json or {}).get("findings", [])

    out_dir = Path(settings.storage_dir) / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"analysis-{analysis.id}.docx"

    if not out_path.exists() or not analysis.output_docx_path:
        build_annotated_docx(
            script.text_extracted,
            findings,
            str(out_path),
            source_filename=script.filename,
        )
        analysis.output_docx_path = str(out_path)
        db.commit()

    download_name = f"{Path(script.filename).stem}-SnP-review.docx"
    return FileResponse(
        str(out_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=download_name,
    )
