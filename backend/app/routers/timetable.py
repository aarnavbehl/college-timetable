"""Generate, view, check, edit and export the timetable."""
import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..database import get_db
from ..deps import current_user
from ..serialization import iso
from ..scheduler.check import check_sessions
from ..scheduler.problem import ProblemError, build_problem
from ..scheduler.soft import soft_cost
from ..scheduler.solver import solve, state_from_sessions
from ..schemas import GenerateIn, MoveIn
from ..services import history
from ..services.dataset import get_or_create_settings, load_dataset
from ..services.timetable import describe_sessions, period_times, to_csv
from .college import settings_json

router = APIRouter(prefix="/timetable", tags=["timetable"])

_ROW = ("id", "allocation_id", "task_key", "day", "start", "length", "room_id")


def _rows(db, uid) -> list[dict]:
    q = select(m.Session).where(m.Session.owner_id == uid).order_by(m.Session.id)
    return [{k: getattr(s, k) for k in _ROW} for s in db.scalars(q)]


def _problem(ds):
    try:
        return build_problem(ds)
    except ProblemError as e:
        raise HTTPException(422, {"message": "Fix these problems first.", "errors": e.errors})


@router.post("/generate")
def generate(body: GenerateIn, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    ds = load_dataset(db, user.id)
    problem = _problem(ds)
    sol = solve(problem, time_limit=body.time_limit, seed=body.seed)
    rows = sol.sessions()
    settings = get_or_create_settings(db, user.id)
    before_rows = history.snapshot(_rows(db, user.id))
    before_meta = history.meta_snapshot(db.get(m.TimetableMeta, user.id))

    db.execute(delete(m.Session).where(m.Session.owner_id == user.id))
    db.add_all(m.Session(owner_id=user.id, **r) for r in rows)
    meta = db.get(m.TimetableMeta, user.id) or m.TimetableMeta(owner_id=user.id)
    meta.generated_at = datetime.now(timezone.utc)
    meta.hard, meta.soft, meta.seconds = sol.hard, sol.soft, sol.seconds
    meta.generations, meta.data_version = sol.generations, settings.data_version
    db.add(meta)
    history.record(db, user, "generated",
                   f"Generated a timetable ({len(rows)} scheduled classes, {sol.hard} hard violation(s), soft score {sol.soft})",
                   {"classes": len(rows), "hard": sol.hard, "soft": sol.soft, "seconds": round(sol.seconds, 2),
                    "before_rows": before_rows, "before_meta": before_meta, "before_classes": len(before_rows),
                    "after_fingerprint": history.rows_fingerprint(rows)}, undoable=True)
    db.commit()
    problems = check_sessions(ds, rows) if sol.hard else []
    return {"ok": sol.hard == 0, "hard": sol.hard, "soft": sol.soft, "seconds": round(sol.seconds, 2),
            "generations": sol.generations, "sessions": len(rows), "classes": len(problem.tasks),
            "warnings": problem.warnings, "problems": problems}


@router.get("")
def get_timetable(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    ds = load_dataset(db, user.id)
    settings = get_or_create_settings(db, user.id)
    meta = db.get(m.TimetableMeta, user.id)
    rows = _rows(db, user.id)
    if meta is None or not rows:
        return {"meta": None, "settings": settings_json(settings), "sessions": []}
    try:
        sessions = describe_sessions(ds, rows)
    except KeyError:                       # allocations were deleted since: treat as no timetable
        return {"meta": None, "settings": settings_json(settings), "sessions": []}
    return {
        "meta": {"generated_at": iso(meta.generated_at), "hard": meta.hard, "soft": meta.soft,
                 "seconds": round(meta.seconds, 1), "generations": meta.generations,
                 "stale": meta.data_version != settings.data_version},
        "settings": settings_json(settings),
        "sessions": sessions,
    }


@router.delete("")
def clear(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    before_rows = history.snapshot(_rows(db, user.id))
    meta = db.get(m.TimetableMeta, user.id)
    if before_rows:
        history.record(db, user, "cleared", f"Deleted the timetable ({len(before_rows)} scheduled classes)",
                       {"before_rows": before_rows, "before_meta": history.meta_snapshot(meta), "before_classes": len(before_rows),
                        "after_fingerprint": history.rows_fingerprint([])}, undoable=True)
    db.execute(delete(m.Session).where(m.Session.owner_id == user.id))
    if meta:
        db.delete(meta)
    db.commit()
    return {"ok": True}


@router.get("/check")
def check(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    """Re-verify the saved timetable from scratch with an independent checker."""
    ds = load_dataset(db, user.id)
    rows = _rows(db, user.id)
    if not rows:
        raise HTTPException(404, "There is no timetable yet.")
    try:
        problems = check_sessions(ds, rows)
    except KeyError:
        raise HTTPException(409, "The saved timetable is out of date: regenerate it.")
    return {"ok": not problems, "problems": problems, "sessions": len(rows)}


@router.get("/export.csv", response_class=PlainTextResponse)
def export_csv(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    ds = load_dataset(db, user.id)
    s = get_or_create_settings(db, user.id)
    rows = _rows(db, user.id)
    if not rows:
        raise HTTPException(404, "There is no timetable yet.")
    times = period_times(s.start_time, s.period_minutes, s.periods_per_day, s.lunch_after, s.lunch_minutes)
    return PlainTextResponse(to_csv(describe_sessions(ds, rows), times), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="timetable.csv"'})


# ------------------------------------------------------------------ manual editing
def _edit_context(db, uid, session_id):
    ds = load_dataset(db, uid)
    problem = _problem(ds)
    rows = _rows(db, uid)
    target = next((r for r in rows if r["id"] == session_id), None)
    if target is None:
        raise HTTPException(404, "Session not found.")
    try:
        state = state_from_sessions(problem, rows)
    except (ValueError, KeyError) as e:
        raise HTTPException(409, str(e) if isinstance(e, ValueError) else "Regenerate the timetable.")
    task = problem.task_by_key().get(target["task_key"])
    if task is None:
        raise HTTPException(409, "The saved timetable is out of date: regenerate it.")
    return problem, state, task, rows, ds


@router.get("/sessions/{session_id}/options")
def move_options(session_id: int, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    """Every legal slot this class (and its simultaneous partners) could move to, and why not."""
    problem, state, task, _, _ds = _edit_context(db, user.id, session_id)
    current = state.slot[task.index]
    state.unplace(task.index)
    rng = random.Random(0)
    options = []
    for day, start in task.starts:
        cost, _rooms = state.eval_slot(task.index, day, start, rng)
        options.append({"day": day, "start": start, "ok": cost == 0, "current": (day, start) == current,
                        "reasons": state.reasons_at(task.index, day, start) if cost else []})
    return {"task_key": task.key, "length": task.length, "options": options}


@router.put("/sessions/{session_id}/move")
def move_session(session_id: int, body: MoveIn, user: m.User = Depends(current_user),
                 db: DbSession = Depends(get_db)):
    problem, state, task, rows, ds = _edit_context(db, user.id, session_id)
    if (body.day, body.start) not in task.starts:
        raise HTTPException(422, "That slot is outside the teaching week or would cross the lunch break.")
    state.unplace(task.index)
    cost, rooms = state.eval_slot(task.index, body.day, body.start, random.Random(0))
    if cost and not body.force:
        raise HTTPException(409, {"message": "That move would cause clashes.",
                                  "reasons": state.reasons_at(task.index, body.day, body.start)})
    state.place(task.index, body.day, body.start, rooms)
    room_by_alloc = {a: problem.room_ids[rooms[j]] for j, a in enumerate(task.alloc_ids)}
    meta = db.get(m.TimetableMeta, user.id)
    meta_before = {"hard": meta.hard, "soft": meta.soft} if meta else None
    allocs = {a.id: a for a in ds.allocations}
    labels = {s_.id: s_ for s_ in ds.sections}
    courses = {c.id: c for c in ds.courses}
    changes = []
    for s in db.scalars(select(m.Session).where(m.Session.owner_id == user.id, m.Session.task_key == task.key).order_by(m.Session.id)):
        a = allocs[s.allocation_id]
        before = {"day": s.day, "start": s.start, "room_id": s.room_id}
        s.day, s.start, s.room_id = body.day, body.start, room_by_alloc[s.allocation_id]
        changes.append({"session_id": s.id, "allocation_id": s.allocation_id, "length": s.length, "before": before,
                        "after": {"day": s.day, "start": s.start, "room_id": s.room_id},
                        "label": f"{courses[a.course_id].code} ({labels[a.section_id].label}{' b' + str(a.batch) if a.batch else ''})"})
    if meta:
        meta.hard, meta.soft = state.hard, soft_cost(problem, state.slot)
    first = changes[0]
    old_slot = history.slot_text(first["before"]["day"], first["before"]["start"], task.length)
    new_slot = history.slot_text(body.day, body.start, task.length)
    partners = f" (+{len(changes) - 1} class(es) running at the same time)" if len(changes) > 1 else ""
    history.record(db, user, "manual_edit", f"Moved {first['label']}{partners} from {old_slot} to {new_slot}"
                   + (" (forced, creates clashes)" if cost else ""),
                   {"changes": changes, "forced": bool(cost), "hard_after": state.hard, "meta_before": meta_before,
                    "meta_after": {"hard": meta.hard, "soft": meta.soft} if meta else None}, undoable=True)
    db.commit()
    return {"ok": True, "hard": state.hard}
