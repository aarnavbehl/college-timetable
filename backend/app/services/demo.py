"""A ready-made demo college: 3 departments, 5 sections, 14 faculty, 9 rooms.

It exercises every feature: lab batches that rotate (sync groups), an elective
slot where two courses run at once, 3-period lab blocks, home rooms, and a
visiting faculty member who is unavailable on Friday afternoons.
"""
from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..scheduler.problem import (AllocRec, Config, CourseRec, Dataset, FacultyRec, RoomRec,
                                 SectionRec)
from .dataset import get_or_create_settings, section_label, touch

SETTINGS = dict(days_per_week=5, periods_per_day=7, start_time="09:00", period_minutes=50,
                lunch_after=4, lunch_minutes=45)

BRANCHES = [("CSE", "Computer Science & Engineering"),
            ("ECE", "Electronics & Communication Engineering"),
            ("BS", "Basic Sciences & Mathematics")]

ROOMS = [("C101", "CLASSROOM", 70), ("C102", "CLASSROOM", 70), ("C103", "CLASSROOM", 70),
         ("C104", "CLASSROOM", 70), ("C201", "CLASSROOM", 80), ("C202", "CLASSROOM", 60),
         ("Lab-1 Programming", "LAB", 30), ("Lab-2 Systems", "LAB", 30),
         ("Lab-3 Electronics", "LAB", 36), ("Lab-4 Networks", "LAB", 30)]

# (branch, year, section, strength, lab batches, home room)
SECTIONS = [("CSE", 2, "A", 60, 2, "C101"), ("CSE", 2, "B", 60, 2, "C102"),
            ("CSE", 3, "A", 60, 2, "C103"), ("ECE", 2, "A", 60, 2, "C104"),
            ("ECE", 3, "A", 60, 2, "C201")]

# (code, name, branch, max/day, max/week, unavailable [(day, period)])
FRIDAY_PM = [(4, 5), (4, 6), (4, 7)]
FACULTY = [
    ("AS", "Dr. Anita Sharma", "CSE", 6, 20, []), ("RV", "Prof. Rahul Verma", "CSE", 6, 20, []),
    ("MK", "Dr. Meera Krishnan", "CSE", 6, 20, []), ("SP", "Prof. Suresh Patel", "CSE", 6, 20, []),
    ("NB", "Dr. Neha Bansal", "CSE", 6, 20, []), ("VR", "Prof. Vikram Rao", "CSE", 6, 20, []),
    ("DJ", "Dr. Deepak Joshi (visiting)", "CSE", 6, 20, FRIDAY_PM),
    ("PN", "Dr. Priya Nair", "ECE", 6, 20, []), ("AK", "Prof. Arjun Kumar", "ECE", 6, 20, []),
    ("SG", "Dr. Sunita Gupta", "ECE", 6, 20, []), ("RM", "Prof. Rohan Mehta", "ECE", 6, 20, []),
    ("KI", "Dr. Kavita Iyer", "ECE", 6, 20, [(0, 1), (0, 2)]),
    ("MT", "Dr. Manoj Tiwari", "BS", 6, 20, []), ("LS", "Prof. Lakshmi Singh", "BS", 6, 20, []),
]

# (code, name, branch, type, hours/week, session length)
COURSES = [
    ("CS201", "Data Structures", "CSE", "THEORY", 4, 1), ("CS202", "Object Oriented Programming", "CSE", "THEORY", 4, 1),
    ("CS203", "Database Management Systems", "CSE", "THEORY", 3, 1), ("CS204", "Computer Organization", "CSE", "THEORY", 3, 1),
    ("CS211", "Data Structures Lab", "CSE", "LAB", 3, 3), ("CS213", "DBMS Lab", "CSE", "LAB", 3, 3),
    ("MA201", "Discrete Mathematics", "BS", "THEORY", 4, 1), ("MA202", "Engineering Mathematics III", "BS", "THEORY", 4, 1),
    ("CS301", "Compiler Design", "CSE", "THEORY", 4, 1), ("CS302", "Operating Systems", "CSE", "THEORY", 4, 1),
    ("CS303", "Computer Networks", "CSE", "THEORY", 4, 1), ("CS304", "Software Engineering", "CSE", "THEORY", 3, 1),
    ("CS3E1", "Machine Learning (Elective)", "CSE", "THEORY", 3, 1), ("CS3E2", "Cloud Computing (Elective)", "CSE", "THEORY", 3, 1),
    ("CS311", "Operating Systems Lab", "CSE", "LAB", 3, 3), ("CS312", "Networks Lab", "CSE", "LAB", 3, 3),
    ("EC201", "Signals and Systems", "ECE", "THEORY", 4, 1), ("EC202", "Analog Circuits", "ECE", "THEORY", 4, 1),
    ("EC203", "Digital Electronics", "ECE", "THEORY", 4, 1), ("EC204", "Electromagnetic Theory", "ECE", "THEORY", 3, 1),
    ("EC211", "Analog Circuits Lab", "ECE", "LAB", 3, 3), ("EC212", "Digital Electronics Lab", "ECE", "LAB", 3, 3),
    ("EC301", "Communication Systems", "ECE", "THEORY", 4, 1), ("EC302", "VLSI Design", "ECE", "THEORY", 4, 1),
    ("EC303", "Microprocessors", "ECE", "THEORY", 4, 1), ("EC304", "Control Systems", "ECE", "THEORY", 3, 1),
    ("EC305", "Antennas and Propagation", "ECE", "THEORY", 3, 1),
    ("EC311", "Communication Lab", "ECE", "LAB", 3, 3), ("EC312", "VLSI Lab", "ECE", "LAB", 3, 3),
]


def _rotation(tag, lab_a, fac_a, lab_b, fac_b):
    """Two lab batches swap between two labs over two sessions: B1=A,B2=B then B1=B,B2=A."""
    return [(lab_a, fac_a, 1, f"{tag}-L1", None), (lab_b, fac_b, 2, f"{tag}-L1", None),
            (lab_b, fac_b, 1, f"{tag}-L2", None), (lab_a, fac_a, 2, f"{tag}-L2", None)]


# section key -> [(course, faculty, batch, sync_group, students)]
def _theory(*pairs):
    return [(c, f, None, None, None) for c, f in pairs]


ALLOCATIONS = {
    ("CSE", 2, "A"): _theory(("CS201", "AS"), ("CS202", "RV"), ("CS203", "MK"), ("MA201", "MT"), ("CS204", "SP"))
    + _rotation("CSE2A", "CS211", "AS", "CS213", "MK"),
    ("CSE", 2, "B"): _theory(("CS201", "SP"), ("CS202", "RV"), ("CS203", "MK"), ("MA201", "MT"), ("CS204", "VR"))
    + _rotation("CSE2B", "CS211", "SP", "CS213", "NB"),
    ("CSE", 3, "A"): _theory(("CS301", "DJ"), ("CS302", "NB"), ("CS303", "VR"), ("CS304", "RV"))
    + [("CS3E1", "AS", None, "CSE3A-E1", 30), ("CS3E2", "DJ", None, "CSE3A-E1", 30)]
    + _rotation("CSE3A", "CS311", "NB", "CS312", "VR"),
    ("ECE", 2, "A"): _theory(("EC201", "PN"), ("EC202", "AK"), ("EC203", "SG"), ("EC204", "RM"), ("MA202", "LS"))
    + _rotation("ECE2A", "EC211", "AK", "EC212", "SG"),
    ("ECE", 3, "A"): _theory(("EC301", "KI"), ("EC302", "RM"), ("EC303", "PN"), ("EC304", "AK"), ("EC305", "KI"))
    + _rotation("ECE3A", "EC311", "KI", "EC312", "RM"),
}


def demo_dataset() -> Dataset:
    """The demo as a plain Dataset (no database), handy for tests and benchmarks."""
    branch_id = {code: i + 1 for i, (code, _) in enumerate(BRANCHES)}
    room_id = {n: i + 1 for i, (n, _, _) in enumerate(ROOMS)}
    sec_id = {(b, y, n): i + 1 for i, (b, y, n, *_r) in enumerate(SECTIONS)}
    fac_id = {c: i + 1 for i, (c, *_r) in enumerate(FACULTY)}
    course_id = {c: i + 1 for i, (c, *_r) in enumerate(COURSES)}
    allocs, n = [], 0
    for key, items in ALLOCATIONS.items():
        for course, fac, batch, group, students in items:
            n += 1
            allocs.append(AllocRec(n, sec_id[key], course_id[course], fac_id[fac], None, batch, group, students))
    return Dataset(
        config=Config(SETTINGS["days_per_week"], SETTINGS["periods_per_day"], SETTINGS["lunch_after"]),
        rooms=[RoomRec(room_id[n], n, t, c) for n, t, c in ROOMS],
        sections=[SectionRec(sec_id[(b, y, n)], section_label(b, y, n), branch_id[b], y, st, ba, room_id[h], b)
                  for b, y, n, st, ba, h in SECTIONS],
        courses=[CourseRec(course_id[c], c, nm, t, h, ln, branch_id[b]) for c, nm, b, t, h, ln in COURSES],
        faculty=[FacultyRec(fac_id[c], nm, c, md, mw, branch_id[b], frozenset(un))
                 for c, nm, b, md, mw, un in FACULTY],
        allocations=allocs,
    )


TABLES = [m.SubstitutionPlan, m.Session, m.TimetableMeta, m.Allocation, m.Section, m.Faculty,
          m.Course, m.Room, m.Branch]


def wipe(db: DbSession, owner_id: int, commit: bool = True) -> None:
    """Delete all of a college's data (not the account).

    With commit=False the deletes stay inside the caller's transaction (used by the Excel
    importer so "replace" and the new rows succeed or fail together)."""
    for model in TABLES:
        db.execute(delete(model).where(model.owner_id == owner_id))
    if commit:
        db.commit()


def is_empty(db: DbSession, owner_id: int) -> bool:
    return not any(db.query(model).filter(model.owner_id == owner_id).first()
                   for model in (m.Branch, m.Room, m.Section, m.Faculty, m.Course, m.Allocation))


def load_demo(db: DbSession, owner_id: int) -> dict:
    settings = get_or_create_settings(db, owner_id)
    for k, v in SETTINGS.items():
        setattr(settings, k, v)
    branches = {c: m.Branch(owner_id=owner_id, code=c, name=n) for c, n in BRANCHES}
    rooms = {n: m.Room(owner_id=owner_id, name=n, room_type=t, capacity=c) for n, t, c in ROOMS}
    db.add_all([*branches.values(), *rooms.values()])
    db.flush()
    sections = {(b, y, n): m.Section(owner_id=owner_id, branch_id=branches[b].id, year=y, name=n,
                                     strength=st, batches=ba, home_room_id=rooms[h].id)
                for b, y, n, st, ba, h in SECTIONS}
    faculty = {c: m.Faculty(owner_id=owner_id, code=c, name=nm, branch_id=branches[b].id, max_per_day=md,
                            max_per_week=mw, unavailable=[list(u) for u in un])
               for c, nm, b, md, mw, un in FACULTY}
    courses = {c: m.Course(owner_id=owner_id, code=c, name=nm, branch_id=branches[b].id, course_type=t,
                           hours_per_week=h, session_length=ln) for c, nm, b, t, h, ln in COURSES}
    db.add_all([*sections.values(), *faculty.values(), *courses.values()])
    db.flush()
    count = 0
    for key, items in ALLOCATIONS.items():
        for course, fac, batch, group, students in items:
            db.add(m.Allocation(owner_id=owner_id, section_id=sections[key].id, course_id=courses[course].id,
                                faculty_id=faculty[fac].id, batch=batch, sync_group=group, students=students))
            count += 1
    db.commit()
    touch(db, owner_id)
    return {"branches": len(branches), "rooms": len(rooms), "sections": len(sections),
            "faculty": len(faculty), "courses": len(courses), "allocations": count}
