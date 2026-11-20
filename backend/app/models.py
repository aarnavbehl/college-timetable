"""Database tables. Every row belongs to one college account (`owner_id`)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, true
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    college_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CollegeSettings(Base):
    """Shape of the teaching week. One row per college."""
    __tablename__ = "college_settings"
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    days_per_week: Mapped[int] = mapped_column(Integer, default=5)
    periods_per_day: Mapped[int] = mapped_column(Integer, default=7)
    start_time: Mapped[str] = mapped_column(String(5), default="09:00")
    period_minutes: Mapped[int] = mapped_column(Integer, default=50)
    lunch_after: Mapped[int] = mapped_column(Integer, default=4)      # 0 = no lunch break
    lunch_minutes: Mapped[int] = mapped_column(Integer, default=45)
    data_version: Mapped[int] = mapped_column(Integer, default=0)     # bumped on every data change


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("owner_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(120))


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    room_type: Mapped[str] = mapped_column(String(10), default="CLASSROOM")   # CLASSROOM | LAB
    capacity: Mapped[int] = mapped_column(Integer, default=60)
    # Closed rooms are ignored by the scheduler (set by a what-if scenario or on the Rooms page).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())


class Section(Base):
    """A group of students: branch + year + section letter (e.g. CSE, year 2, A)."""
    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("owner_id", "branch_id", "year", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"))
    year: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(10))
    strength: Mapped[int] = mapped_column(Integer, default=60)
    batches: Mapped[int] = mapped_column(Integer, default=1)          # lab batches the section splits into
    home_room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)


class Faculty(Base):
    __tablename__ = "faculty"
    __table_args__ = (UniqueConstraint("owner_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(12))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    max_per_day: Mapped[int] = mapped_column(Integer, default=5)
    max_per_week: Mapped[int] = mapped_column(Integer, default=20)
    unavailable: Mapped[list] = mapped_column(JSON, default=list)     # [[day, period], ...]


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("owner_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    course_type: Mapped[str] = mapped_column(String(10), default="THEORY")    # THEORY | LAB
    hours_per_week: Mapped[int] = mapped_column(Integer, default=3)
    session_length: Mapped[int] = mapped_column(Integer, default=1)           # consecutive periods per session


class Allocation(Base):
    """'Faculty F teaches course C to section S (optionally one lab batch)'."""
    __tablename__ = "allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculty.id"))
    hours_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)   # None -> course default
    batch: Mapped[int | None] = mapped_column(Integer, nullable=True)            # None -> whole section
    sync_group: Mapped[str | None] = mapped_column(String(40), nullable=True)    # same label = same time slot
    students: Mapped[int | None] = mapped_column(Integer, nullable=True)        # enrolment (electives)


class Session(Base):
    """One scheduled class: an allocation placed at day/start (length periods) in a room."""
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    allocation_id: Mapped[int] = mapped_column(ForeignKey("allocations.id"))
    task_key: Mapped[str] = mapped_column(String(80))
    day: Mapped[int] = mapped_column(Integer)          # 0 = Monday
    start: Mapped[int] = mapped_column(Integer)        # first period, 1-based
    length: Mapped[int] = mapped_column(Integer, default=1)
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)


class TimetableMeta(Base):
    __tablename__ = "timetable_meta"
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    hard: Mapped[int] = mapped_column(Integer, default=0)
    soft: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(default=0.0)
    generations: Mapped[int] = mapped_column(Integer, default=0)
    data_version: Mapped[int] = mapped_column(Integer, default=0)


class SubstitutionPlan(Base):
    __tablename__ = "substitution_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[str] = mapped_column(String(10))
    absent_faculty_ids: Mapped[list] = mapped_column(JSON, default=list)
    rows: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Scenario(Base):
    """A what-if experiment: a list of temporary changes plus the candidate timetable they produced.
    Nothing here touches the real data or the saved timetable until the user applies it."""
    __tablename__ = "scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    changes: Mapped[list] = mapped_column(JSON, default=list)
    strategy: Mapped[str] = mapped_column(String(10), default="adjust")       # adjust | rebuild
    time_limit: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[str] = mapped_column(String(12), default="draft")          # draft | ready | invalid | infeasible | applied
    base_data_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # {"rows": [...]}
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChangeLog(Base):
    """Audit trail of timetable changes (who: the college account; when: server time; before/after values)."""
    __tablename__ = "change_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    kind: Mapped[str] = mapped_column(String(20))        # generated | manual_edit | simulation | cleared | undo
    summary: Mapped[str] = mapped_column(String(300))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    undoable: Mapped[bool] = mapped_column(Boolean, default=False)
    undo_note: Mapped[str | None] = mapped_column(String(200), nullable=True)   # why it cannot be undone
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undone_by: Mapped[int | None] = mapped_column(Integer, nullable=True)       # id of the 'undo' entry
