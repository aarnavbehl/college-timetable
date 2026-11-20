"""College-wide endpoints: settings, readiness checks, dashboard stats, demo data."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..database import get_db
from ..deps import current_user
from ..serialization import iso
from ..scheduler.problem import ProblemError, build_problem
from ..schemas import DemoIn, SettingsIn
from ..services import demo
from ..services.dataset import get_or_create_settings, load_dataset, touch
from ..services.timetable import period_times

router = APIRouter(tags=["college"])


def settings_json(s: m.CollegeSettings) -> dict:
    return {
        "days_per_week": s.days_per_week, "periods_per_day": s.periods_per_day, "start_time": s.start_time,
        "period_minutes": s.period_minutes, "lunch_after": s.lunch_after, "lunch_minutes": s.lunch_minutes,
        "data_version": s.data_version,
        "period_times": period_times(s.start_time, s.period_minutes, s.periods_per_day, s.lunch_after,
                                     s.lunch_minutes),
    }


@router.get("/settings")
def get_settings_(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    return settings_json(get_or_create_settings(db, user.id))


@router.put("/settings")
def put_settings(body: SettingsIn, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    s = get_or_create_settings(db, user.id)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    db.commit()
    touch(db, user.id)
    return settings_json(s)


@router.get("/validate")
def validate(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    """Everything that would stop a timetable from being generated (or that looks odd)."""
    try:
        problem = build_problem(load_dataset(db, user.id))
    except ProblemError as e:
        return {"ok": False, "errors": e.errors, "warnings": []}
    return {"ok": True, "errors": [], "warnings": problem.warnings, "tasks": len(problem.tasks)}


@router.get("/stats")
def stats(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    def count(model):
        return db.scalar(select(func.count()).select_from(model).where(model.owner_id == user.id)) or 0

    counts = {"branches": count(m.Branch), "rooms": count(m.Room), "sections": count(m.Section),
              "faculty": count(m.Faculty), "courses": count(m.Course), "allocations": count(m.Allocation)}
    meta = db.get(m.TimetableMeta, user.id)
    settings = get_or_create_settings(db, user.id)
    steps = [("branches", "Add branches (departments)"), ("rooms", "Add classrooms and labs"),
             ("sections", "Add sections (branch + year + section)"), ("faculty", "Add faculty"),
             ("courses", "Add courses"), ("allocations", "Allocate courses to sections and faculty")]
    return {
        "counts": counts,
        "checklist": [{"key": k, "label": label, "done": counts[k] > 0} for k, label in steps],
        "timetable": None if meta is None else {
            "generated_at": iso(meta.generated_at), "hard": meta.hard, "soft": meta.soft,
            "seconds": round(meta.seconds, 1), "stale": meta.data_version != settings.data_version,
            "sessions": count(m.Session),
        },
    }


@router.post("/demo")
def load_demo_data(body: DemoIn, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    if not demo.is_empty(db, user.id):
        if not body.reset:
            raise HTTPException(409, "Your college already has data. Loading the demo would replace it.")
        demo.wipe(db, user.id)
    return demo.load_demo(db, user.id)
