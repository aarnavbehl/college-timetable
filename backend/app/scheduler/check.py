"""Independent timetable verifier.

Deliberately does NOT use the solver's data structures: it re-derives every rule
from plain dictionaries, so a bug in the solver can't hide a clash from it.

`check_violations` returns structured `Violation`s (used by the analytics and explanations);
`check_sessions` returns just their messages (the original API).
"""
from __future__ import annotations

from dataclasses import dataclass

from .problem import CLASSROOM, DAY_NAMES, LAB, Dataset, students_for

# violation kinds
FACULTY_CONFLICT = "faculty_conflict"        # a faculty member in two places in one period
ROOM_CONFLICT = "room_conflict"              # a room used by two classes in one period
SECTION_CONFLICT = "section_conflict"        # a student group (section/batch) in two classes in one period
FACULTY_UNAVAILABLE = "faculty_unavailable"  # scheduled while marked unavailable
FACULTY_DAILY_LIMIT = "faculty_daily_limit"  # more periods in a day than their daily maximum
ROOM_TYPE = "room_type"                      # lab class in a classroom or vice versa
ROOM_CAPACITY = "room_capacity"              # room smaller than the group
MISSING_ROOM = "missing_room"                # no (or a deleted) room
PLACEMENT = "placement"                      # outside the week/day or across the lunch break
ORPHAN = "orphan"                            # refers to an allocation that no longer exists

HARD_KINDS = (SECTION_CONFLICT, FACULTY_CONFLICT, ROOM_CONFLICT, FACULTY_UNAVAILABLE, FACULTY_DAILY_LIMIT,
              ROOM_TYPE, ROOM_CAPACITY, MISSING_ROOM, PLACEMENT, ORPHAN)


@dataclass(frozen=True)
class Violation:
    kind: str
    message: str
    day: int | None = None
    period: int | None = None
    faculty_id: int | None = None
    room_id: int | None = None
    section_id: int | None = None
    task_key: str | None = None


def check_violations(ds: Dataset, rows: list[dict]) -> list[Violation]:
    cfg = ds.config
    allocs = {a.id: a for a in ds.allocations}
    sections = {s.id: s for s in ds.sections}
    courses = {c.id: c for c in ds.courses}
    faculty = {f.id: f for f in ds.faculty}
    rooms = {r.id: r for r in ds.rooms}
    found: list[Violation] = []

    atoms: dict[tuple, dict[str, str]] = {}      # (atom, day, period) -> {task_key: description}
    fac_use: dict[tuple, list[str]] = {}
    room_use: dict[tuple, list[str]] = {}
    fac_day: dict[tuple, int] = {}

    def when(day, period):
        return f"{DAY_NAMES[day]} period {period}"

    for r in rows:
        a = allocs.get(r["allocation_id"])
        if a is None:
            found.append(Violation(ORPHAN, f"Session {r['task_key']} refers to a missing allocation.",
                                   task_key=r["task_key"]))
            continue
        sec, course, fac = sections[a.section_id], courses[a.course_id], faculty[a.faculty_id]
        name = f"{course.code} ({sec.label}{' b' + str(a.batch) if a.batch else ''})"
        day, start, length = r["day"], r["start"], r["length"]
        if not 0 <= day < cfg.days or not cfg.block_ok(start, length):
            found.append(Violation(PLACEMENT, f"{name} is placed outside the teaching week or across the break "
                                              f"({when(day, start) if 0 <= day < 7 else f'day {day}'}, {length} periods).",
                                   day, start, fac.id, r["room_id"], sec.id, r["task_key"]))
            continue
        batches = [a.batch] if a.batch else range(1, max(sec.batches, 1) + 1)
        room = rooms.get(r["room_id"])
        if room is None:
            found.append(Violation(MISSING_ROOM, f"{name} has no room.", day, start, fac.id, None, sec.id, r["task_key"]))
        else:
            want = LAB if course.type == LAB else CLASSROOM
            if room.type != want:
                found.append(Violation(ROOM_TYPE, f"{name} is in {room.name}, which is not a {want.lower()}.",
                                       day, start, fac.id, room.id, sec.id, r["task_key"]))
            if room.capacity < students_for(sec, a):
                found.append(Violation(ROOM_CAPACITY, f"{name} is in {room.name} (capacity {room.capacity}) "
                                                      f"but has {students_for(sec, a)} students.",
                                       day, start, fac.id, room.id, sec.id, r["task_key"]))
        fac_day[(fac.id, day)] = fac_day.get((fac.id, day), 0) + length
        for p in range(start, start + length):
            for b in batches:
                atoms.setdefault(((sec.id, b), day, p), {})[r["task_key"]] = name
            fac_use.setdefault((fac.id, day, p), []).append(name)
            if room is not None:
                room_use.setdefault((room.id, day, p), []).append(name)
            if (day, p) in fac.unavailable:
                found.append(Violation(FACULTY_UNAVAILABLE,
                                       f"{fac.name} is scheduled for {name} at {when(day, p)} but is unavailable.",
                                       day, p, fac.id, None, sec.id, r["task_key"]))

    for (atom, day, p), tasks in atoms.items():
        if len(tasks) > 1:
            sec = sections[atom[0]]
            found.append(Violation(SECTION_CONFLICT, f"{sec.label} batch {atom[1]} has two classes at {when(day, p)}: "
                                   + " and ".join(sorted(tasks.values())), day, p, section_id=sec.id))
    for (fid, day, p), names in fac_use.items():
        if len(names) > 1:
            found.append(Violation(FACULTY_CONFLICT,
                                   f"{faculty[fid].name} is double-booked at {when(day, p)}: {' and '.join(names)}",
                                   day, p, faculty_id=fid))
    for (rid, day, p), names in room_use.items():
        if len(names) > 1:
            found.append(Violation(ROOM_CONFLICT,
                                   f"Room {rooms[rid].name} is double-booked at {when(day, p)}: {' and '.join(names)}",
                                   day, p, room_id=rid))
    for (fid, day), load in fac_day.items():
        if load > faculty[fid].max_day:
            found.append(Violation(FACULTY_DAILY_LIMIT, f"{faculty[fid].name} teaches {load} periods on "
                                   f"{DAY_NAMES[day]} (max {faculty[fid].max_day}).", day, None, faculty_id=fid))
    return found


def check_sessions(ds: Dataset, rows: list[dict]) -> list[str]:
    return [v.message for v in check_violations(ds, rows)]
