"""Plain-data description of a college timetabling problem.

`Dataset` is what the database layer produces. `build_problem()` validates it and
turns it into a `Problem`: a list of *tasks* (things to place in the week) with
all ids replaced by small integers so the solver's inner loops are fast.

Vocabulary
----------
period    1-based slot within a day; `day` is 0-based (0 = Monday).
atom      (section, batch). A section with 2 lab batches has two atoms. A class for the
          whole section occupies every atom; a batch lab occupies just one. Two classes
          clash when they share an atom in the same period.
member    one allocation inside a task ("Dr X teaches DSA to CSE-2A batch 1").
task      one session to place: usually a single member, but allocations sharing a
          `sync_group` label (lab rotations, electives) form one task whose members run
          at the same time with different faculty and rooms.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .weights import DEFAULT_WEIGHTS, SoftWeights

THEORY = "THEORY"
LAB = "LAB"
CLASSROOM = "CLASSROOM"
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class ProblemError(Exception):
    """The data cannot be turned into a solvable problem; `.errors` says why."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# --------------------------------------------------------------------------- records
@dataclass
class Config:
    days: int
    periods: int
    lunch_after: int = 0      # break between this period and the next; 0 = none

    def block_ok(self, start: int, length: int) -> bool:
        """Can a session of `length` periods start at period `start`?"""
        end = start + length - 1
        if start < 1 or end > self.periods:
            return False
        return not (self.lunch_after and start <= self.lunch_after < end)   # never span lunch


@dataclass
class RoomRec:
    id: int
    name: str
    type: str                 # CLASSROOM | LAB
    capacity: int


@dataclass
class SectionRec:
    id: int
    label: str                # e.g. "CSE Y2-A"
    branch_id: int
    year: int
    strength: int
    batches: int = 1
    home_room_id: int | None = None
    branch_code: str = ""


@dataclass
class CourseRec:
    id: int
    code: str
    name: str
    type: str                 # THEORY | LAB
    hours: int
    length: int = 1
    branch_id: int | None = None


@dataclass
class FacultyRec:
    id: int
    name: str
    code: str
    max_day: int = 5
    max_week: int = 20
    branch_id: int | None = None
    unavailable: frozenset = frozenset()       # {(day, period), ...}


@dataclass
class AllocRec:
    id: int
    section_id: int
    course_id: int
    faculty_id: int
    hours: int | None = None
    batch: int | None = None
    sync_group: str | None = None
    students: int | None = None


@dataclass
class Dataset:
    config: Config
    rooms: list[RoomRec]
    sections: list[SectionRec]
    courses: list[CourseRec]
    faculty: list[FacultyRec]
    allocations: list[AllocRec]


# --------------------------------------------------------------------------- problem
@dataclass
class Task:
    index: int
    key: str                                   # stable id, stored with sessions
    length: int
    alloc_ids: tuple[int, ...]                 # one per member
    fac: tuple[int, ...]                       # dense faculty index per member
    room_opts: tuple[tuple[int, ...], ...]     # compatible dense room indexes per member
    home: tuple[int, ...]                      # preferred room per member (-1 = none)
    atoms: tuple[int, ...]                     # dense atom indexes occupied
    sections: tuple[int, ...]                  # dense section indexes involved
    starts: list[tuple[int, int]]              # every legal (day, start)
    theory: bool                               # single-period class (spread-out preference)


@dataclass
class Problem:
    config: Config
    tasks: list[Task]
    S: int                                     # slots per day in the flat arrays (periods + 1)
    T: int                                     # days * S
    n_atoms: int
    n_fac: int
    n_rooms: int
    n_sections: int
    fac_ids: list[int]
    room_ids: list[int]
    section_ids: list[int]
    fac_max_day: list[int]
    unav: list[list[int]]                      # unav[f][day*S+period] == 1 if unavailable
    section_total: list[int]                   # periods per week each section is in class
    warnings: list[str] = field(default_factory=list)
    weights: SoftWeights = DEFAULT_WEIGHTS          # soft-preference weights (what-if scenarios may change them)

    @property
    def days(self) -> int:
        return self.config.days

    @property
    def periods(self) -> int:
        return self.config.periods

    def task_by_key(self) -> dict[str, Task]:
        return {t.key: t for t in self.tasks}


@dataclass
class _Alloc:
    rec: AllocRec
    section: SectionRec
    course: CourseRec
    length: int
    sessions: int
    atoms: tuple
    students: int
    room_type: str
    rooms: list
    label: str


def students_for(section: SectionRec, rec: AllocRec) -> int:
    if rec.students:
        return rec.students
    if rec.batch:
        return math.ceil(section.strength / max(section.batches, 1))
    return section.strength


def build_problem(ds: Dataset) -> Problem:
    """Validate `ds` and build a Problem. Raises ProblemError listing everything wrong."""
    errors: list[str] = []
    warnings: list[str] = []
    cfg = ds.config
    sections = {s.id: s for s in ds.sections}
    courses = {c.id: c for c in ds.courses}
    faculty = {f.id: f for f in ds.faculty}
    day_slots = cfg.days * cfg.periods

    if not ds.allocations:
        raise ProblemError(["There are no course allocations yet. Add at least one on the Allocations page."])

    allocs: list[_Alloc] = []
    for rec in ds.allocations:
        sec, course = sections.get(rec.section_id), courses.get(rec.course_id)
        if sec is None or course is None or rec.faculty_id not in faculty:
            errors.append(f"Allocation #{rec.id} refers to a section, course or faculty that no longer exists.")
            continue
        label = f"{course.code} for {sec.label}" + (f" batch {rec.batch}" if rec.batch else "")
        hours = rec.hours or course.hours
        length = course.length
        if hours % length:
            errors.append(f"{label}: {hours} hours/week is not a multiple of the session length ({length}).")
            continue
        if rec.batch is not None and not 1 <= rec.batch <= max(sec.batches, 1):
            errors.append(f"{label}: batch {rec.batch} does not exist (the section has {sec.batches}).")
            continue
        batches = [rec.batch] if rec.batch else list(range(1, max(sec.batches, 1) + 1))
        students = students_for(sec, rec)
        room_type = LAB if course.type == LAB else CLASSROOM
        rooms = [r for r in ds.rooms if r.type == room_type and r.capacity >= students]
        if not rooms:
            kind = "lab" if room_type == LAB else "classroom"
            errors.append(f"{label}: no {kind} with capacity for {students} students.")
        allocs.append(_Alloc(rec, sec, course, length, hours // length,
                             tuple((sec.id, b) for b in batches), students, room_type, rooms, label))

    # ---- group allocations into tasks (sync groups run at the same time)
    singles = [a for a in allocs if not a.rec.sync_group]
    groups: dict[str, list[_Alloc]] = {}
    for a in allocs:
        if a.rec.sync_group:
            groups.setdefault(a.rec.sync_group, []).append(a)

    specs: list[tuple[str, int, list[_Alloc]]] = []
    for a in singles:
        for k in range(a.sessions):
            specs.append((f"A{a.rec.id}#{k}", a.length, [a]))
    for name, members in groups.items():
        if len({m.sessions for m in members}) > 1 or len({m.length for m in members}) > 1:
            errors.append(f"Sync group '{name}': every member must have the same sessions per week "
                          f"and session length ({', '.join(m.label for m in members)}).")
            continue
        facs = [m.rec.faculty_id for m in members]
        if len(set(facs)) != len(facs):
            errors.append(f"Sync group '{name}': the same faculty member is used twice at the same time.")
            continue
        for k in range(members[0].sessions):
            specs.append((f"G{name}#{k}", members[0].length, members))

    # ---- dense indexes
    fac_ids = [f.id for f in ds.faculty]
    fac_index = {fid: i for i, fid in enumerate(fac_ids)}
    room_ids = [r.id for r in ds.rooms]
    room_index = {rid: i for i, rid in enumerate(room_ids)}
    section_ids = [s.id for s in ds.sections]
    section_index = {sid: i for i, sid in enumerate(section_ids)}
    atom_index: dict[tuple, int] = {}

    S = cfg.periods + 1
    tasks: list[Task] = []
    atom_load: dict[tuple, int] = {}
    fac_load: dict[int, int] = {}
    room_load: dict[str, int] = {}
    section_total = [0] * len(section_ids)

    for key, length, members in specs:
        starts = [(d, s) for d in range(cfg.days) for s in range(1, cfg.periods + 1)
                  if cfg.block_ok(s, length)]
        if not starts:
            errors.append(f"{members[0].label}: a {length}-period session does not fit in a day "
                          f"({cfg.periods} periods, break after period {cfg.lunch_after}).")
            continue
        atoms = sorted({a for m in members for a in m.atoms})
        for a in atoms:
            atom_index.setdefault(a, len(atom_index))
            atom_load[a] = atom_load.get(a, 0) + length
        secs = sorted({m.section.id for m in members})
        for sid in secs:
            section_total[section_index[sid]] += length
        for m in members:
            fac_load[m.rec.faculty_id] = fac_load.get(m.rec.faculty_id, 0) + length
            room_load[m.room_type] = room_load.get(m.room_type, 0) + length
        home = []
        for m in members:
            hr = m.section.home_room_id
            ok = (m.room_type == CLASSROOM and not m.rec.batch and hr is not None
                  and any(r.id == hr for r in m.rooms))
            home.append(room_index[hr] if ok else -1)
        tasks.append(Task(
            index=len(tasks), key=key, length=length,
            alloc_ids=tuple(m.rec.id for m in members),
            fac=tuple(fac_index[m.rec.faculty_id] for m in members),
            room_opts=tuple(tuple(room_index[r.id] for r in m.rooms) for m in members),
            home=tuple(home),
            atoms=tuple(atom_index[a] for a in atoms),
            sections=tuple(section_index[s] for s in secs),
            starts=starts,
            theory=length == 1 and members[0].course.type != LAB,
        ))

    # ---- capacity checks that make a timetable impossible before we even start
    for (sid, b), load in sorted(atom_load.items()):
        if load > day_slots:
            sec = sections[sid]
            who = sec.label + (f" batch {b}" if sec.batches > 1 else "")
            errors.append(f"{who} needs {load} periods a week but the week only has {day_slots}.")
    for f in ds.faculty:
        load = fac_load.get(f.id, 0)
        if not load:
            continue
        avail = day_slots - len({(d, p) for d, p in f.unavailable if d < cfg.days and 1 <= p <= cfg.periods})
        if load > f.max_week:
            errors.append(f"{f.name} is allocated {load} periods a week but the maximum is {f.max_week}.")
        if load > cfg.days * f.max_day:
            errors.append(f"{f.name} is allocated {load} periods but {f.max_day}/day over {cfg.days} days "
                          f"allows only {cfg.days * f.max_day}.")
        if load > avail:
            errors.append(f"{f.name} is allocated {load} periods but is available for only {avail}.")
    for rtype, load in room_load.items():
        count = sum(1 for r in ds.rooms if r.type == rtype)
        if load > count * day_slots:
            errors.append(f"{'Labs' if rtype == LAB else 'Classrooms'}: {load} room-periods needed "
                          f"but {count} room(s) give only {count * day_slots}.")

    used = {a.section.id for a in allocs}
    for s in ds.sections:
        if s.id not in used:
            warnings.append(f"{s.label} has no course allocations, so it will have no timetable.")

    if errors:
        raise ProblemError(errors)

    unav = [[0] * (cfg.days * S) for _ in fac_ids]
    for f in ds.faculty:
        for d, p in f.unavailable:
            if 0 <= d < cfg.days and 1 <= p <= cfg.periods:
                unav[fac_index[f.id]][d * S + p] = 1
    return Problem(
        config=cfg, tasks=tasks, S=S, T=cfg.days * S, n_atoms=len(atom_index), n_fac=len(fac_ids),
        n_rooms=len(room_ids), n_sections=len(section_ids), fac_ids=fac_ids, room_ids=room_ids,
        section_ids=section_ids, fac_max_day=[f.max_day for f in ds.faculty], unav=unav,
        section_total=section_total, warnings=warnings,
    )
