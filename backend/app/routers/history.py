"""Change history of the saved timetable, and undo."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..database import get_db
from ..deps import current_user
from ..serialization import iso
from ..schemas import ApplyIn
from ..services import history
from ..services.dataset import load_session_rows

router = APIRouter(prefix="/history", tags=["history"])

KIND_LABELS = {"generated": "Generated", "manual_edit": "Manual edit", "simulation": "Simulation applied",
               "cleared": "Timetable deleted", "undo": "Undo"}
ACTOR_NOTE = "This application has one login per college, so every change is attributed to that college account."


def _view(e: m.ChangeLog, room_names: dict[int, str]) -> dict:
    d = e.details or {}
    if e.kind == "manual_edit":
        room = lambda rid: room_names.get(rid, "no room")   # noqa: E731
        return {"forced": d.get("forced", False), "hard_after": d.get("hard_after"), "changes": [
            {"class": c["label"], "from": f"{history.slot_text(c['before']['day'], c['before']['start'], c.get('length', 1))}, {room(c['before']['room_id'])}",
             "to": f"{history.slot_text(c['after']['day'], c['after']['start'], c.get('length', 1))}, {room(c['after']['room_id'])}"}
            for c in d.get("changes", [])]}
    hidden = {"before_rows", "before_meta", "after_fingerprint", "changes"}
    out = {k: v for k, v in d.items() if k not in hidden}
    if e.kind == "simulation":
        out["changes"] = d.get("changes", [])
    return out


def _json(e: m.ChangeLog, user: m.User, rows: list[dict], db: DbSession, room_names: dict[int, str]) -> dict:
    ok, reason = history.undo_status(db, e, rows)
    return {"id": e.id, "created_at": iso(e.created_at), "kind": e.kind, "kind_label": KIND_LABELS.get(e.kind, e.kind),
            "summary": e.summary, "actor": {"id": user.id, "email": user.email, "college_name": user.college_name},
            "undone": e.undone_at is not None, "undone_at": iso(e.undone_at),
            "can_undo": ok, "undo_reason": None if ok else reason, "details": _view(e, room_names)}


@router.get("")
def list_history(limit: int = Query(50, ge=1, le=200), user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    rows = load_session_rows(db, user.id)
    rooms = {r.id: r.name for r in db.scalars(select(m.Room).where(m.Room.owner_id == user.id))}
    q = select(m.ChangeLog).where(m.ChangeLog.owner_id == user.id).order_by(m.ChangeLog.id.desc()).limit(limit)
    return {"actor_note": ACTOR_NOTE, "entries": [_json(e, user, rows, db, rooms) for e in db.scalars(q)]}


@router.post("/{entry_id}/undo")
def undo_entry(entry_id: int, body: ApplyIn, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    if not body.confirm:
        raise HTTPException(422, "Undoing changes your saved timetable. Send confirm=true to proceed.")
    try:
        return history.undo(db, user, entry_id)
    except history.UndoError as e:
        raise HTTPException(e.status, e.detail)
