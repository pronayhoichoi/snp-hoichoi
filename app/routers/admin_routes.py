from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models.models import Guideline, User

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


def _active_guideline(db: Session) -> Guideline | None:
    return (
        db.query(Guideline)
        .filter(Guideline.is_active.is_(True))
        .order_by(Guideline.version.desc())
        .first()
    )


@router.get("/guidelines", response_class=HTMLResponse)
def guidelines_page(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    current = _active_guideline(db)
    return templates.TemplateResponse(
        request,
        "admin_guidelines.html",
        {"user": user, "current": current},
    )


@router.post("/guidelines")
def guidelines_save(
    request: Request,
    content_text: str = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    prev = _active_guideline(db)
    if prev:
        prev.is_active = False
    next_version = (prev.version + 1) if prev else 1
    g = Guideline(
        version=next_version,
        content_text=content_text.strip(),
        uploaded_by=user.id,
        is_active=True,
        created_at=datetime.utcnow(),
    )
    db.add(g)
    db.commit()
    return RedirectResponse("/admin/guidelines", status_code=302)
