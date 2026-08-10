from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.models import User
from app.auth import verify_password, make_session, hash_password, SESSION_COOKIE, current_user
from app.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None, "ms_enabled": settings.ms_enabled})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid credentials", "ms_enabled": settings.ms_enabled}, status_code=401
        )
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, make_session(user.id), httponly=True, samesite="lax")
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse(request, "signup.html", {"error": None, "ms_enabled": settings.ms_enabled})


@router.post("/signup")
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse(
            request, "signup.html", {"error": "Email already registered", "ms_enabled": settings.ms_enabled}, status_code=400
        )
    is_first = db.query(User).count() == 0
    user = User(email=email, password_hash=hash_password(password), is_admin=is_first)
    db.add(user)
    db.commit()
    db.refresh(user)
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, make_session(user.id), httponly=True, samesite="lax")
    return resp
