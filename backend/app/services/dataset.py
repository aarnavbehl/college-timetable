"""Load a college's rows from the database as a plain `Dataset` for the scheduler."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..scheduler.problem import (AllocRec, Config, CourseRec, Dataset, FacultyRec, RoomRec,
                                 SectionRec)


def get_or_create_settings(db: DbSession, owner_id: int) -> m.CollegeSettings:
    row = db.get(m.CollegeSettings, owner_id)
    if row is None:
        row = m.CollegeSettings(owner_id=owner_id)
        db.add(row)
        db.commit()
    return row


def touch(db: DbSession, owner_id: int) -> None:
    """Record that the data changed (so a saved timetable can be flagged out of date)."""
    row = get_or_create_settings(db, owner_id)
    row.data_version += 1
    db.commit()


def section_label(branch_code: str, year: int, name: str) -> str:
    return f"{branch_code} Y{year}-{name}"


def load_dataset(db: DbSession, owner_id: int) -> Dataset:
    s = get_or_create_settings(db, owner_id)
    rows = lambda model: db.scalars(select(model).where(model.owner_id == owner_id).order_by(model.id)).all()
    branches = {b.id: b for b in rows(m.Branch)}
    return Dataset(
        config=Config(days=s.days_per_week, periods=s.periods_per_day, lunch_after=s.lunch_after),
        rooms=[RoomRec(r.id, r.name, r.room_type, r.capacity) for r in rows(m.Room) if r.is_active],   # closed rooms are not scheduled
        sections=[SectionRec(x.id, section_label(branches[x.branch_id].code, x.year, x.name),
                             x.branch_id, x.year, x.strength, x.batches, x.home_room_id,
                             branches[x.branch_id].code)
                  for x in rows(m.Section)],
        courses=[CourseRec(c.id, c.code, c.name, c.course_type, c.hours_per_week, c.session_length,
                           c.branch_id) for c in rows(m.Course)],
        faculty=[FacultyRec(f.id, f.name, f.code, f.max_per_day, f.max_per_week, f.branch_id,
                            frozenset((int(d), int(p)) for d, p in (f.unavailable or [])))
                 for f in rows(m.Faculty)],
        allocations=[AllocRec(a.id, a.section_id, a.course_id, a.faculty_id, a.hours_per_week,
                              a.batch, a.sync_group, a.students) for a in rows(m.Allocation)],
    )


SESSION_FIELDS = ("id", "allocation_id", "task_key", "day", "start", "length", "room_id")


def load_session_rows(db: DbSession, owner_id: int) -> list[dict]:
    """The saved timetable as plain dicts (one per scheduled class)."""
    q = select(m.Session).where(m.Session.owner_id == owner_id).order_by(m.Session.id)
    return [{k: getattr(s, k) for k in SESSION_FIELDS} for s in db.scalars(q)]
