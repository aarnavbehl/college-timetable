"""Change history for the saved timetable (recording side; undo lives in `undo` below).

Every entry stores the real server time and the college account that made the change. This app has one
login per college, so "who" is that account: no other identity is invented."""
from __future__ import annotations

import hashlib

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from .. import models as m

MAX_ENTRIES = 200
ROW_KEYS = ("allocation_id", "task_key", "day", "start", "length", "room_id")


def rows_fingerprint(rows: list[dict]) -> str:
    """Order-independent hash of a timetable's placements (detects any change to it)."""
    parts = sorted(f"{r['allocation_id']}|{r['task_key']}|{r['day']}|{r['start']}|{r['length']}|{r['room_id']}" for r in rows)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def snapshot(rows: list[dict]) -> list[dict]:
    return [{k: r[k] for k in ROW_KEYS} for r in rows]


def meta_snapshot(meta: m.TimetableMeta | None) -> dict | None:
    if meta is None:
        return None
    return {"hard": meta.hard, "soft": meta.soft, "seconds": meta.seconds, "generations": meta.generations,
            "data_version": meta.data_version, "generated_at": meta.generated_at.isoformat()}


def record(db: DbSession, user: m.User, kind: str, summary: str, details: dict, *, undoable: bool = False,
           undo_note: str | None = None) -> m.ChangeLog:
    """Add an entry to the caller's transaction (the caller commits). Keeps the newest MAX_ENTRIES."""
    entry = m.ChangeLog(owner_id=user.id, user_id=user.id, kind=kind, summary=summary[:300], details=details,
                        undoable=undoable, undo_note=undo_note)
    db.add(entry)
    db.flush()
    total = db.scalar(select(func.count()).select_from(m.ChangeLog).where(m.ChangeLog.owner_id == user.id)) or 0
    if total > MAX_ENTRIES:
        oldest = db.scalars(select(m.ChangeLog.id).where(m.ChangeLog.owner_id == user.id)
                            .order_by(m.ChangeLog.id).limit(total - MAX_ENTRIES)).all()
        db.execute(delete(m.ChangeLog).where(m.ChangeLog.id.in_(oldest)))
    return entry


# --------------------------------------------------------------------------- undo
from datetime import datetime, timezone  # noqa: E402

from sqlalchemy.exc import IntegrityError  # noqa: E402

from ..scheduler.check import check_violations  # noqa: E402
from ..scheduler.problem import DAY_NAMES, ProblemError, build_problem  # noqa: E402
from ..scheduler.soft import soft_cost  # noqa: E402
from ..scheduler.solver import state_from_sessions  # noqa: E402
from .dataset import get_or_create_settings, load_dataset, load_session_rows  # noqa: E402


class UndoError(Exception):
    def __init__(self, message: str, status: int = 409, errors: list[str] | None = None):
        super().__init__(message)
        self.message, self.status, self.errors = message, status, errors or []

    @property
    def detail(self):
        return {"message": self.message, "errors": self.errors}


def slot_text(day: int, start: int, length: int = 1) -> str:
    return f"{DAY_NAMES[day][:3]} P{start}" + (f"-{start + length - 1}" if length > 1 else "")


def undo_status(db: DbSession, entry: m.ChangeLog, rows: list[dict]) -> tuple[bool, str | None]:
    """Can this entry be undone right now? (Cheap eligibility only; the undo itself re-validates.)"""
    if entry.undone_at is not None:
        return False, "Already undone."
    if entry.kind == "undo":
        return False, "An undo cannot be undone. Make the change again instead."
    if not entry.undoable:
        return False, entry.undo_note or "This change cannot be undone automatically."
    d = entry.details or {}
    if entry.kind == "manual_edit":
        current = {r["id"]: r for r in rows}
        for ch in d.get("changes", []):
            r = current.get(ch["session_id"])
            if r is None:
                return False, "These classes no longer exist in the saved timetable (it was regenerated or replaced)."
            if (r["day"], r["start"], r["room_id"]) != (ch["after"]["day"], ch["after"]["start"], ch["after"]["room_id"]):
                return False, "These classes were changed again after this edit. Undo the later change first."
        return True, None
    newer = db.scalar(select(m.ChangeLog.id).where(m.ChangeLog.owner_id == entry.owner_id, m.ChangeLog.id > entry.id,
                                                    m.ChangeLog.undone_at.is_(None), m.ChangeLog.kind != "undo").limit(1))
    if newer is not None:
        return False, "Newer changes exist. Undo them first (newest first)."
    if rows_fingerprint(rows) != d.get("after_fingerprint"):
        return False, ("The timetable was changed outside the history (for example by an import or data change), "
                       "so this state can no longer be restored safely.")
    return True, None


def undo(db: DbSession, user: m.User, entry_id: int) -> dict:
    """Restore the state before `entry`. Re-verifies the restored timetable and refuses if it would be worse."""
    uid = user.id
    entry = db.get(m.ChangeLog, entry_id)
    if entry is None or entry.owner_id != uid:
        raise UndoError("History entry not found.", 404)
    rows = load_session_rows(db, uid)
    ok, reason = undo_status(db, entry, rows)
    if not ok:
        raise UndoError(reason or "This change cannot be undone.")
    d = entry.details or {}
    ds = load_dataset(db, uid)
    if entry.kind == "manual_edit":
        before = {c["session_id"]: c["before"] for c in d["changes"]}
        restored = [{**r, **before[r["id"]]} if r["id"] in before else dict(r) for r in rows]
    else:
        restored = [{**r, "id": i} for i, r in enumerate(d["before_rows"], 1)]
    now_bad, restored_bad = check_violations(ds, rows), check_violations(ds, restored)
    if len(restored_bad) > len(now_bad):
        raise UndoError(f"Undoing this would introduce {len(restored_bad) - len(now_bad)} new hard-constraint violation(s) "
                        "(your data probably changed since). Nothing was changed.", 409, [v.message for v in restored_bad[:8]])
    try:
        meta = db.get(m.TimetableMeta, uid)
        if entry.kind == "manual_edit":
            for sess in db.scalars(select(m.Session).where(m.Session.owner_id == uid, m.Session.id.in_(list(before)))):
                b = before[sess.id]
                sess.day, sess.start, sess.room_id = b["day"], b["start"], b["room_id"]
            if meta is not None:
                # Recompute from the restored timetable: a cached score from when the edit was made is wrong as soon as
                # any other edit has happened in between (edits to different classes can be undone in any order).
                try:
                    problem = build_problem(ds)
                    state = state_from_sessions(problem, restored)
                    meta.hard, meta.soft = state.hard, soft_cost(problem, state.slot)
                except (ProblemError, ValueError):
                    meta.hard = len(restored_bad)
        else:
            db.execute(delete(m.Session).where(m.Session.owner_id == uid))
            db.add_all(m.Session(owner_id=uid, **r) for r in d["before_rows"])
            mb = d.get("before_meta")
            if mb is None:
                if meta is not None:
                    db.delete(meta)
            else:
                meta = meta or m.TimetableMeta(owner_id=uid)
                meta.hard, meta.soft, meta.seconds, meta.generations = mb["hard"], mb["soft"], mb["seconds"], mb["generations"]
                meta.data_version, meta.generated_at = mb["data_version"], datetime.fromisoformat(mb["generated_at"])
                db.add(meta)
        db.flush()
        if entry.kind != "manual_edit":
            get_or_create_settings(db, uid)
        log = record(db, user, "undo", f"Undid: {entry.summary}", {"undid": entry.id, "undid_kind": entry.kind,
                                                                      "violations_after": len(restored_bad)})
        entry.undone_at, entry.undone_by = datetime.now(timezone.utc), log.id
        db.commit()
    except IntegrityError:
        db.rollback()
        raise UndoError("The previous state refers to records that were deleted since. Nothing was changed.")
    except Exception:
        db.rollback()
        raise
    return {"undone": entry.id, "violations_after": len(restored_bad), "classes": len(restored)}
