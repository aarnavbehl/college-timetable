"""Turning stored sessions into display rows, period times and CSV."""
from __future__ import annotations

import csv
import io

from ..scheduler.problem import DAY_NAMES, Dataset


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def period_times(start_time: str, period_minutes: int, periods: int, lunch_after: int,
                 lunch_minutes: int) -> list[tuple[str, str]]:
    h, mi = (int(x) for x in start_time.split(":"))
    t, out = h * 60 + mi, []
    for p in range(1, periods + 1):
        out.append((_hhmm(t), _hhmm(t + period_minutes)))
        t += period_minutes
        if p == lunch_after:
            t += lunch_minutes
    return out


def describe_sessions(ds: Dataset, rows: list[dict]) -> list[dict]:
    """Attach readable names to raw session rows."""
    allocs = {a.id: a for a in ds.allocations}
    sections = {s.id: s for s in ds.sections}
    courses = {c.id: c for c in ds.courses}
    faculty = {f.id: f for f in ds.faculty}
    rooms = {r.id: r for r in ds.rooms}
    out = []
    for r in rows:
        a = allocs[r["allocation_id"]]
        s, c, f = sections[a.section_id], courses[a.course_id], faculty[a.faculty_id]
        out.append({
            **r, "section_id": s.id, "section": s.label, "branch_code": s.branch_code, "year": s.year,
            "batch": a.batch, "sync_group": a.sync_group,
            "course_id": c.id, "course_code": c.code, "course_name": c.name, "course_type": c.type,
            "faculty_id": f.id, "faculty": f.name, "faculty_code": f.code,
            "room": rooms[r["room_id"]].name if r["room_id"] in rooms else None,
        })
    return out


def to_csv(described: list[dict], times: list[tuple[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Section", "Batch", "Day", "From period", "To period", "Time", "Course code", "Course",
                "Type", "Faculty", "Room"])
    for r in sorted(described, key=lambda x: (x["section"], x["day"], x["start"], x["batch"] or 0)):
        end = r["start"] + r["length"] - 1
        w.writerow([r["section"], r["batch"] or "", DAY_NAMES[r["day"]], r["start"], end,
                    f"{times[r['start'] - 1][0]}-{times[end - 1][1]}", r["course_code"], r["course_name"],
                    r["course_type"], r["faculty"], r["room"] or ""])
    return buf.getvalue()
