import bcrypt
from fastapi import Request, HTTPException, Depends
from itsdangerous import URLSafeSerializer, BadSignature
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.models import User

serializer = URLSafeSerializer(settings.session_secret, salt="snp-session")

SESSION_COOKIE = "snp_session"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), pw_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def make_session(user_id: int) -> str:
    return serializer.dumps({"uid": user_id})


def read_session(token: str) -> int | None:
    try:
        data = serializer.loads(token)
        return int(data.get("uid"))
    except (BadSignature, ValueError, TypeError):
        return None


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    uid = read_session(tok)
    if not uid:
        return None
    return db.get(User, uid)


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user
