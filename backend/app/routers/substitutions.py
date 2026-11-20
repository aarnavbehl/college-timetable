"""Substitution plans for absent faculty."""
from datetime import date as _date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..database import get_db
from ..deps import current_user
from ..serialization import iso
from ..scheduler.problem import DAY_NAMES
from ..schemas import SubstituteIn, SubstitutionSaveIn
from ..services.dataset import get_or_create_settings, load_dataset
from ..services.substitutions import suggest_substitutes
from ..services.timetable import describe_sessions
from .timetable import _rows

router = APIRouter(prefix="/substitutions", tags=["substitutions"])


def _plan_json(p: m.SubstitutionPlan) -> dict:
    return {"id": p.id, "date": p.date, "absent_faculty_ids": p.absent_faculty_ids, "rows": p.rows,
            "created_at": iso(p.created_at)}


@router.post("/generate")
def generate(body: SubstituteIn, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    ds = load_dataset(db, user.id)
    settings = get_or_create_settings(db, user.id)
    known = {f.id for f in ds.faculty}
    if not set(body.absent_faculty_ids) <= known:
        raise HTTPException(422, "One of the selected faculty members does not exist.")
    rows = _rows(db, user.id)
    if not rows:
        raise HTTPException(409, "Generate a timetable first.")
    day = _date.fromisoformat(body.date).weekday()
    if day >= settings.days_per_week:
        return {"date": body.date, "day": day, "day_name": DAY_NAMES[day], "rows": [], "unfilled": 0,
                "message": f"{DAY_NAMES[day]} is not a teaching day."}
    try:
        described = describe_sessions(ds, rows)
    except KeyError:
        raise HTTPException(409, "The saved timetable is out of date: regenerate it.")
    result = suggest_substitutes(ds, described, day, set(body.absent_faculty_ids))
    return {"date": body.date, "day": day, "day_name": DAY_NAMES[day], "rows": result,
            "unfilled": sum(1 for r in result if r["substitute_id"] is None), "message": ""}


@router.post("", status_code=201)
def save(body: SubstitutionSaveIn, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    plan = db.scalar(select(m.SubstitutionPlan).where(m.SubstitutionPlan.owner_id == user.id,
                                                       m.SubstitutionPlan.date == body.date))
    if plan is None:
        plan = m.SubstitutionPlan(owner_id=user.id, date=body.date)
        db.add(plan)
    plan.absent_faculty_ids, plan.rows = body.absent_faculty_ids, body.rows
    db.commit()
    return _plan_json(plan)


@router.get("")
def list_plans(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    q = select(m.SubstitutionPlan).where(m.SubstitutionPlan.owner_id == user.id).order_by(
        m.SubstitutionPlan.date.desc())
    return [_plan_json(p) for p in db.scalars(q)]


@router.delete("/{plan_id}")
def delete_plan(plan_id: int, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    plan = db.get(m.SubstitutionPlan, plan_id)
    if plan is None or plan.owner_id != user.id:
        raise HTTPException(404, "Not found.")
    db.delete(plan)
    db.commit()
    return {"ok": True}
