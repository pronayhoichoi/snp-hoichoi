import logging

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db, SessionLocal
from app.models import models  # noqa: F401 — register models
from app.models.models import Script, Analysis
from app.routers import auth_routes, script_routes, admin_routes, ms_auth
from app.auth import current_user

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="SnP Checker")

Base.metadata.create_all(bind=engine)


@app.on_event("startup")
def cleanup_orphaned_analyses() -> None:
    """Any analysis still in extracting/running at startup was killed by a
    previous crash — mark it as error so the UI stops spinning forever."""
    db = SessionLocal()
    try:
        stuck = (
            db.query(Analysis)
            .filter(Analysis.status.in_(["extracting", "running"]))
            .all()
        )
        for a in stuck:
            a.status = "error"
            a.error = a.error or "Analysis was interrupted (container restart). Please try again."
        if stuck:
            db.commit()
            logging.info("startup: marked %d orphaned analyses as error", len(stuck))
    finally:
        db.close()

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(auth_routes.router)
app.include_router(ms_auth.router)
app.include_router(script_routes.router)
app.include_router(admin_routes.router)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, user=Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "dashboard.html", {"user": user})


@app.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    scripts = (
        db.query(Script)
        .filter(Script.user_id == user.id)
        .order_by(Script.created_at.desc())
        .all()
    )
    rows = []
    for s in scripts:
        latest = (
            db.query(Analysis)
            .filter(Analysis.script_id == s.id, Analysis.status == "done")
            .order_by(Analysis.created_at.desc())
            .first()
        )
        findings = (latest.findings_json or {}).get("findings", []) if latest else []
        rows.append({
            "script": s,
            "analysis": latest,
            "total": len(findings),
            "high": sum(1 for f in findings if f.get("severity") == "high"),
            "med": sum(1 for f in findings if f.get("severity") == "medium"),
            "low": sum(1 for f in findings if f.get("severity") == "low"),
        })
    return templates.TemplateResponse(
        request, "history.html", {"user": user, "rows": rows}
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}
