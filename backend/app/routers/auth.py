from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..database import get_db
from ..deps import current_user
from ..schemas import ChangePasswordIn, LoginIn, RegisterIn
from ..security import create_token, hash_password, verify_password
from ..services.dataset import get_or_create_settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_json(u: m.User) -> dict:
    return {"id": u.id, "college_name": u.college_name, "email": u.email}


@router.post("/register", status_code=201)
def register(body: RegisterIn, db: DbSession = Depends(get_db)):
    if db.scalar(select(m.User).where(m.User.email == body.email)):
        raise HTTPException(409, "An account with this email already exists. Please sign in.")
    user = m.User(college_name=body.college_name, email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    get_or_create_settings(db, user.id)
    return {"token": create_token(user.id), "user": _user_json(user)}


@router.post("/login")
def login(body: LoginIn, db: DbSession = Depends(get_db)):
    user = db.scalar(select(m.User).where(m.User.email == body.email))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password.")
    return {"token": create_token(user.id), "user": _user_json(user)}


@router.get("/me")
def me(user: m.User = Depends(current_user)):
    return _user_json(user)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user: m.User = Depends(current_user),
                    db: DbSession = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(422, "Your current password is incorrect.")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
