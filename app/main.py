from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db
from app.models import models  # noqa: F401 — register models
from app.models.models import Script, Analysis
from app.routers import auth_routes, script_routes, admin_routes
from app.auth import current_user

app = FastAPI(title="SnP Checker")

Base.metadata.create_all(bind=engine)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(auth_routes.router)
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
