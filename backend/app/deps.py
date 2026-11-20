"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as DbSession

from . import models as m
from .database import get_db
from .security import read_token

_bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                 db: DbSession = Depends(get_db)) -> m.User:
    user_id = read_token(creds.credentials) if creds else None
    user = db.get(m.User, user_id) if user_id else None
    if user is None:
        raise HTTPException(401, "Please sign in again.", headers={"WWW-Authenticate": "Bearer"})
    return user
