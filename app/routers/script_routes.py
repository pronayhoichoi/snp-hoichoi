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

    data = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(400, f"File exceeds {settings.max_upload_mb} MB limit")

    upload_dir = Path(settings.storage_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = upload_dir / stored_name
    stored_path.write_bytes(data)

    try:
        text, ocr_used = extract(str(stored_path), file.content_type or "")
    except Exception as e:
        os.unlink(stored_path)
        raise HTTPException(400, f"Extraction failed: {e}") from e

    if not text.strip():
        os.unlink(stored_path)
        raise HTTPException(400, "No text could be extracted from this file")

    script = Script(
        user_id=user.id,
        filename=filename,
        mime=file.content_type or "",
        storage_path=str(stored_path),
        text_extracted=text,
        ocr_used=ocr_used,
    )
    db.add(script)
    db.commit()
    db.refresh(script)

    return RedirectResponse(f"/scripts/{script.id}/analyze", status_code=302)


def _run_analysis_bg(analysis_id: int, script_text: str, guidelines_text: str) -> None:
    db = SessionLocal()
    try:
        analysis = db.get(Analysis, analysis_id)
        if not analysis:
            log.error("bg: analysis %d missing", analysis_id)
            return
        try:
            log.info("bg: starting analysis %d", analysis_id)
            result = analyze(script_text, guidelines_text)
            analysis.findings_json = result
            analysis.status = "done"
            analysis.completed_at = datetime.utcnow()
            log.info("bg: analysis %d done, %d findings", analysis_id, len(result.get("findings", [])))
        except Exception as e:
            log.exception("bg: analysis %d failed", analysis_id)
            analysis.status = "error"
            analysis.error = str(e)[:2000]
        db.commit()
    finally:
        db.close()


@router.get("/{script_id}/analyze")
def start_analysis(
    script_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    script = db.get(Script, script_id)
    if not script or script.user_id != user.id:
        raise HTTPException(404)

    guideline = _active_guideline(db)
    if not guideline:
        raise HTTPException(400, "No S&P guidelines have been set. Ask an admin to add them.")

    done = (
        db.query(Analysis)
        .filter(Analysis.script_id == script.id, Analysis.status == "done")
        .order_by(Analysis.created_at.desc())
        .first()
    )
    if done:
        return RedirectResponse(f"/scripts/{script.id}/result/{done.id}", status_code=302)

    running = (
        db.query(Analysis)
        .filter(Analysis.script_id == script.id, Analysis.status == "running")
        .order_by(Analysis.created_at.desc())
        .first()
    )
    if running:
        return RedirectResponse(f"/scripts/{script.id}/status/{running.id}", status_code=302)

    analysis = Analysis(
        script_id=script.id,
        guideline_id=guideline.id,
        status="running",
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    threading.Thread(
        target=_run_analysis_bg,
        args=(analysis.id, script.text_extracted, guideline.content_text),
        daemon=True,
    ).start()

    return RedirectResponse(f"/scripts/{script.id}/status/{analysis.id}", status_code=302)


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
