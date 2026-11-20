"""CRUD for the six things a college enters: branches, rooms, sections, faculty,
courses and allocations. One small factory builds list/create/update/delete for each.

(No `from __future__ import annotations` here on purpose: FastAPI needs the real
schema class in each route's signature.)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..database import get_db
from ..deps import current_user
from ..schemas import AllocationIn, BranchIn, CourseIn, FacultyIn, RoomIn, SectionIn
from ..services.dataset import get_or_create_settings, section_label, touch

router = APIRouter()


def _plain(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name != "owner_id"}


def _all(db, model, owner_id):
    return db.scalars(select(model).where(model.owner_id == owner_id).order_by(model.id)).all()


def _owned(db, model, owner_id, obj_id, what):
    obj = db.get(model, obj_id)
    if obj is None or obj.owner_id != owner_id:
        raise HTTPException(422, f"The selected {what} does not exist.")
    return obj


class Resource:
    def __init__(self, path, model, schema, *, refs=(), guards=(), serialize=None, validate=None,
                 on_delete=None, tag=None):
        self.path, self.model, self.schema = path, model, schema
        self.refs, self.guards = refs, guards
        self.serialize = serialize or (lambda db, uid, rows: [_plain(r) for r in rows])
        self.validate, self.on_delete = validate, on_delete
        self.tag = tag or path

    # -- helpers ------------------------------------------------------------------
    def check(self, db, uid, data, existing_id=None):
        for field, ref_model, what in self.refs:
            if data.get(field) is not None:
                _owned(db, ref_model, uid, data[field], what)
        if self.validate:
            self.validate(db, uid, data, existing_id)

    @staticmethod
    def commit(db):
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "A record with the same name/code already exists.")

    def register(self):
        r, model, schema, me = router, self.model, self.schema, self

        @r.get(f"/{self.path}", tags=[self.tag], name=f"list_{self.path}")
        def list_all(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
            return me.serialize(db, user.id, _all(db, model, user.id))

        @r.post(f"/{self.path}", status_code=201, tags=[self.tag], name=f"create_{self.path}")
        def create(body: schema, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
            data = body.model_dump()
            me.check(db, user.id, data)
            obj = model(owner_id=user.id, **data)
            db.add(obj)
            me.commit(db)
            touch(db, user.id)
            return me.serialize(db, user.id, [obj])[0]

        @r.put(f"/{self.path}/{{item_id}}", tags=[self.tag], name=f"update_{self.path}")
        def update_one(item_id: int, body: schema, user: m.User = Depends(current_user),
                       db: DbSession = Depends(get_db)):
            obj = db.get(model, item_id)
            if obj is None or obj.owner_id != user.id:
                raise HTTPException(404, "Not found.")
            data = body.model_dump()
            me.check(db, user.id, data, item_id)
            for k, v in data.items():
                setattr(obj, k, v)
            me.commit(db)
            touch(db, user.id)
            return me.serialize(db, user.id, [obj])[0]

        @r.delete(f"/{self.path}/{{item_id}}", tags=[self.tag], name=f"delete_{self.path}")
        def delete_one(item_id: int, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
            obj = db.get(model, item_id)
            if obj is None or obj.owner_id != user.id:
                raise HTTPException(404, "Not found.")
            for child, fk, message in me.guards:
                if db.scalar(select(child.id).where(getattr(child, fk) == item_id).limit(1)):
                    raise HTTPException(409, message)
            if me.on_delete:
                me.on_delete(db, user.id, obj)
            db.delete(obj)
            me.commit(db)
            touch(db, user.id)
            return {"ok": True}


# ----------------------------------------------------------------------- serializers
def _branch_map(db, uid):
    return {b.id: b for b in _all(db, m.Branch, uid)}


def ser_sections(db, uid, rows):
    branches, rooms = _branch_map(db, uid), {r.id: r for r in _all(db, m.Room, uid)}
    out = []
    for s in rows:
        d = _plain(s)
        b = branches[s.branch_id]
        d.update(branch_code=b.code, label=section_label(b.code, s.year, s.name),
                 home_room=rooms[s.home_room_id].name if s.home_room_id in rooms else None)
        out.append(d)
    return out


def _with_branch(db, uid, rows):
    branches = _branch_map(db, uid)
    out = []
    for x in rows:
        d = _plain(x)
        d["branch_code"] = branches[x.branch_id].code if x.branch_id in branches else None
        out.append(d)
    return out


def ser_allocations(db, uid, rows):
    secs = {s["id"]: s for s in ser_sections(db, uid, _all(db, m.Section, uid))}
    courses = {c.id: c for c in _all(db, m.Course, uid)}
    faculty = {f.id: f for f in _all(db, m.Faculty, uid)}
    out = []
    for a in rows:
        d = _plain(a)
        c, f, s = courses[a.course_id], faculty[a.faculty_id], secs[a.section_id]
        d.update(section_label=s["label"], course_code=c.code, course_name=c.name, course_type=c.course_type,
                 faculty_name=f.name, faculty_code=f.code,
                 effective_hours=a.hours_per_week or c.hours_per_week)
        out.append(d)
    return out


# ----------------------------------------------------------------------- business rules
def val_section(db, uid, data, _id):
    if data["home_room_id"] is not None:
        room = _owned(db, m.Room, uid, data["home_room_id"], "room")
        if room.room_type != "CLASSROOM":
            raise HTTPException(422, "A home room must be a classroom, not a lab.")


def val_faculty(db, uid, data, _id):
    s = get_or_create_settings(db, uid)
    for day, period in data["unavailable"]:
        if not (0 <= day < s.days_per_week and 1 <= period <= s.periods_per_day):
            raise HTTPException(422, f"Unavailable slot (day {day + 1}, period {period}) is outside the "
                                     f"{s.days_per_week}-day, {s.periods_per_day}-period week.")
    data["unavailable"] = sorted({(d, p) for d, p in data["unavailable"]})
    data["unavailable"] = [list(x) for x in data["unavailable"]]


def val_course(db, uid, data, _id):
    if data["hours_per_week"] % data["session_length"]:
        raise HTTPException(422, f"Hours per week ({data['hours_per_week']}) must be a multiple of the "
                                 f"session length ({data['session_length']}).")


def val_allocation(db, uid, data, item_id):
    sec = _owned(db, m.Section, uid, data["section_id"], "section")
    course = db.get(m.Course, data["course_id"])
    if data["batch"] is not None and data["batch"] > sec.batches:
        raise HTTPException(422, f"Batch {data['batch']} does not exist: the section has {sec.batches} batch(es). "
                                 f"Raise 'Lab batches' on the section first.")
    hours = data["hours_per_week"] or course.hours_per_week
    if hours % course.session_length:
        raise HTTPException(422, f"{hours} hours/week is not a multiple of {course.code}'s "
                                 f"session length ({course.session_length}).")
    q = select(m.Allocation.id).where(
        m.Allocation.owner_id == uid, m.Allocation.section_id == data["section_id"],
        m.Allocation.course_id == data["course_id"],
        m.Allocation.batch.is_(None) if data["batch"] is None else m.Allocation.batch == data["batch"])
    if item_id is not None:
        q = q.where(m.Allocation.id != item_id)
    if db.scalar(q.limit(1)):
        raise HTTPException(409, "This course is already allocated to that section/batch.")


def cascade_allocation(db, uid, obj):
    db.execute(delete(m.Session).where(m.Session.allocation_id == obj.id))


def cascade_section(db, uid, obj):
    ids = db.scalars(select(m.Allocation.id).where(m.Allocation.section_id == obj.id)).all()
    if ids:
        db.execute(delete(m.Session).where(m.Session.allocation_id.in_(ids)))
        db.execute(delete(m.Allocation).where(m.Allocation.id.in_(ids)))


def release_room(db, uid, obj):
    db.execute(update(m.Section).where(m.Section.home_room_id == obj.id).values(home_room_id=None))
    db.execute(update(m.Session).where(m.Session.room_id == obj.id).values(room_id=None))


Resource("branches", m.Branch, BranchIn, guards=[
    (m.Section, "branch_id", "This branch still has sections. Delete or move them first."),
    (m.Course, "branch_id", "This branch still has courses. Delete or move them first."),
    (m.Faculty, "branch_id", "This branch still has faculty. Delete or move them first.")]).register()
Resource("rooms", m.Room, RoomIn, on_delete=release_room).register()
Resource("sections", m.Section, SectionIn, refs=[("branch_id", m.Branch, "branch")],
         serialize=ser_sections, validate=val_section, on_delete=cascade_section).register()
Resource("faculty", m.Faculty, FacultyIn, refs=[("branch_id", m.Branch, "branch")],
         serialize=_with_branch, validate=val_faculty,
         guards=[(m.Allocation, "faculty_id", "This faculty member still has allocations. Remove them first.")]).register()
Resource("courses", m.Course, CourseIn, refs=[("branch_id", m.Branch, "branch")],
         serialize=_with_branch, validate=val_course,
         guards=[(m.Allocation, "course_id", "This course is still allocated to sections. Remove those allocations first.")]).register()
Resource("allocations", m.Allocation, AllocationIn,
         refs=[("section_id", m.Section, "section"), ("course_id", m.Course, "course"),
               ("faculty_id", m.Faculty, "faculty member")],
         serialize=ser_allocations, validate=val_allocation, on_delete=cascade_allocation).register()
