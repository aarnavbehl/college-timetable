"""Read-only insight endpoints: quality analytics and 'Why this slot?' explanations.

Nothing here writes to the database."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..database import get_db
from ..deps import current_user
from ..serialization import iso
from ..services.analytics import analyze
from ..services.dataset import get_or_create_settings, load_dataset, load_session_rows
from ..services.explain import ExplainError, explain

router = APIRouter(tags=["insights"])


@router.get("/analytics")
def analytics(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    """Metrics from the saved timetable. Hard-constraint numbers come from the independent verifier."""
    settings = get_or_create_settings(db, user.id)
    rows = load_session_rows(db, user.id)
    if not rows:
        return {"available": False, "reason": "No timetable has been generated yet. Generate one to see analytics."}
    out = analyze(load_dataset(db, user.id), rows, settings.period_minutes)
    meta = db.get(m.TimetableMeta, user.id)
    if meta is not None:
        out["timetable"] = {"generated_at": iso(meta.generated_at), "stale": meta.data_version != settings.data_version,
                            "solver_reported_hard": meta.hard}
    return out


@router.get("/timetable/sessions/{session_id}/explain")
def explain_session(session_id: int, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    """Why is this class where it is? (read-only; see services/explain.py for what is and is not claimed)"""
    rows = load_session_rows(db, user.id)
    if not any(r["id"] == session_id for r in rows):
        raise HTTPException(404, "Session not found.")
    settings = get_or_create_settings(db, user.id)
    meta = db.get(m.TimetableMeta, user.id)
    try:
        return explain(load_dataset(db, user.id), rows, session_id,
                       stale=meta is not None and meta.data_version != settings.data_version)
    except ExplainError as e:
        raise HTTPException(409, str(e))
