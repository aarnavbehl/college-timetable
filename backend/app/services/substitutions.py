"""Find stand-in teachers for absent faculty on a given day."""
from __future__ import annotations

from ..scheduler.problem import Dataset


def suggest_substitutes(ds: Dataset, described: list[dict], day: int, absent_ids: set[int]) -> list[dict]:
    """For every class of an absent teacher on `day`, pick a free colleague.

    A substitute must be present, free for the whole class (including 3-period labs),
    not unavailable, and under their daily limit. Same-department teachers with the
    lightest day are preferred. Returns one row per affected class (substitute may be None).
    """
    faculty = {f.id: f for f in ds.faculty}
    busy: dict[int, set[int]] = {}
    load: dict[int, int] = {}
    todays = [r for r in described if r["day"] == day]
    for r in todays:
        if r["faculty_id"] in absent_ids:
            continue
        busy.setdefault(r["faculty_id"], set()).update(range(r["start"], r["start"] + r["length"]))
        load[r["faculty_id"]] = load.get(r["faculty_id"], 0) + r["length"]

    out = []
    for r in sorted((x for x in todays if x["faculty_id"] in absent_ids), key=lambda x: (x["start"], x["section"])):
        periods = set(range(r["start"], r["start"] + r["length"]))
        absent = faculty[r["faculty_id"]]
        best, best_key = None, None
        for f in ds.faculty:
            if f.id in absent_ids or busy.get(f.id, set()) & periods:
                continue
            if any((day, p) in f.unavailable for p in periods):
                continue
            if load.get(f.id, 0) + r["length"] > f.max_day:
                continue
            same = 0 if (f.branch_id is not None and f.branch_id in (absent.branch_id,)) else 1
            key = (same, load.get(f.id, 0), f.name)
            if best_key is None or key < best_key:
                best, best_key = f, key
        if best:
            busy.setdefault(best.id, set()).update(periods)
            load[best.id] = load.get(best.id, 0) + r["length"]
        out.append({
            "day": day, "start": r["start"], "length": r["length"], "section": r["section"],
            "batch": r["batch"], "course_code": r["course_code"], "course_name": r["course_name"],
            "room": r["room"], "absent_id": absent.id, "absent": absent.name,
            "substitute_id": best.id if best else None,
            "substitute": best.name if best else None,
        })
    return out
