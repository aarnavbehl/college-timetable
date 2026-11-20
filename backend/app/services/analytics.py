"""Timetable quality analytics, computed ONLY from the saved timetable and the college's data.

Nothing here is estimated or hard-coded: every number is derived from the session rows, and the
hard-constraint numbers come from the independent verifier (scheduler/check.py).

Hard constraints (must be zero)  vs  soft preferences (quality). A timetable that violates a hard
constraint gets NO quality score: a nice-looking but invalid schedule must never look good.

QUALITY SCORE (0-100, deterministic)
    score = sum(weight_i * component_i) / sum(weight_i)         components are each 0-100
    compactness        30   100 * (1 - gap_periods / (gap_periods + class_periods))       section free periods between classes
    early_start        10   100 * (1 - late_periods / (late_periods + class_periods))     section free periods before its first class
    subject_spread     20   100 * (1 - repeats / single_period_theory_classes)            same course twice in one day
    faculty_continuity 15   100 * (1 - (faculty_gaps + long_run) / faculty_periods)       gaps / >3 in a row for teachers
    workload_balance   15   100 * max(0, 1 - CV)  CV = std/mean of weekly periods of faculty who teach
    day_balance        10   100 * (1 - uneven_periods / class_periods)                    days heavier than average+1
Components with nothing that could be penalised score 100.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

from ..scheduler import check as chk
from ..scheduler.problem import DAY_NAMES, Dataset
from ..scheduler.weights import COMPONENTS, DEFAULT_WEIGHTS

QUALITY_WEIGHTS = {"compactness": 30, "early_start": 10, "subject_spread": 20,
                   "faculty_continuity": 15, "workload_balance": 15, "day_balance": 10}

DEFINITIONS: dict[str, dict] = {
    "required_classes": {"label": "Required classes", "kind": "info", "text":
        "Weekly class sessions the allocations call for: for each allocation, hours per week divided by the "
        "session length. A lab rotation with two batches counts as two classes (one per batch)."},
    "scheduled_classes": {"label": "Scheduled classes", "kind": "info", "text":
        "Class sessions that actually exist in the saved timetable."},
    "unscheduled_classes": {"label": "Unscheduled classes", "kind": "hard", "text":
        "Required sessions that have no placement in the saved timetable (for example after allocations were "
        "added following generation). Any unscheduled class makes the timetable incomplete."},
    "hard_violations": {"label": "Hard-constraint violations", "kind": "hard", "text":
        "Rule breaks found by the independent verifier: double-booked students, faculty or rooms, classes while a "
        "faculty member is unavailable, daily-limit breaches, wrong or too-small rooms, and illegal placements. "
        "Must be zero for a valid timetable."},
    "faculty_conflicts": {"label": "Faculty conflicts", "kind": "hard", "text":
        "Period slots in which a faculty member is booked for more than one class at the same time."},
    "room_conflicts": {"label": "Room conflicts", "kind": "hard", "text":
        "Period slots in which a room hosts more than one class at the same time."},
    "section_conflicts": {"label": "Student-section conflicts", "kind": "hard", "text":
        "Period slots in which a section (or lab batch) has more than one class at the same time."},
    "room_utilization": {"label": "Room utilization", "kind": "info", "text":
        "Occupied room-periods divided by available room-periods (rooms x teaching days x periods per day). "
        "A room used by two overlapping classes still counts once."},
    "faculty_workload": {"label": "Faculty workload", "kind": "info", "text":
        "Periods per week each faculty member teaches, compared with their maximum per week. The spread (coefficient "
        "of variation, CV = standard deviation / mean) shows how evenly the load is shared."},
    "by_day": {"label": "Classes and hours by day", "kind": "info", "text":
        "Classes starting each day and the teaching hours they cover (periods x minutes per period)."},
    "gaps": {"label": "Section gaps", "kind": "soft", "text":
        "Free periods between a section's first and last class of a day (all lab batches together). Fewer is better."},
    "quality_score": {"label": "Quality score", "kind": "soft", "text":
        "A 0-100 summary of the soft preferences (see the breakdown). Only calculated when there are no hard "
        "violations and nothing is unscheduled. It never replaces the hard constraints."},
}
for _k, (_label, _text) in COMPONENTS.items():
    DEFINITIONS[_k] = {"label": _label, "kind": "soft", "text": _text + " Weight in the solver: "
                       f"{getattr(DEFAULT_WEIGHTS, _k)} per unit."}

FORMULA = ("score = (30*compactness + 10*early_start + 20*subject_spread + 15*faculty_continuity + "
           "15*workload_balance + 10*day_balance) / 100; each component is 0-100 (100 = nothing to penalise).")


def _ratio_score(penalty: float, base: float) -> float:
    return 100.0 if base <= 0 else 100.0 * (1 - min(1.0, penalty / base))


def analyze(ds: Dataset, rows: list[dict], period_minutes: int) -> dict:
    cfg = ds.config
    D, P = cfg.days, cfg.periods
    allocs = {a.id: a for a in ds.allocations}
    sections = {s.id: s for s in ds.sections}
    courses = {c.id: c for c in ds.courses}
    faculty = {f.id: f for f in ds.faculty}
    rooms = {r.id: r for r in ds.rooms}

    valid = [r for r in rows if r["allocation_id"] in allocs]
    if not valid:
        return {"available": False, "reason": "There are no scheduled classes to analyse yet. Generate a timetable first."}

    # ---------------------------------------------------------------- required vs scheduled
    sched_by_alloc: dict[int, int] = defaultdict(int)
    for r in valid:
        sched_by_alloc[r["allocation_id"]] += 1
    required = scheduled = unscheduled = extra = req_periods = 0
    missing = []
    for a in ds.allocations:
        c = courses[a.course_id]
        hours = a.hours or c.hours
        need = hours // max(c.length, 1)
        have = sched_by_alloc.get(a.id, 0)
        required += need
        req_periods += hours
        scheduled += min(have, need)
        extra += max(0, have - need)
        if have < need:
            unscheduled += need - have
            missing.append({"section": sections[a.section_id].label, "course": c.code, "course_name": c.name,
                            "faculty": faculty[a.faculty_id].name, "batch": a.batch, "required": need, "scheduled": have})
    scheduled_total = len(valid)

    # ---------------------------------------------------------------- hard constraints (independent verifier)
    violations = chk.check_violations(ds, rows)
    by_kind: dict[str, int] = defaultdict(int)
    for v in violations:
        by_kind[v.kind] += 1
    feasible = not violations and unscheduled == 0

    # ---------------------------------------------------------------- occupancy grids
    room_cells: set[tuple[int, int, int]] = set()
    fac_cells: dict[tuple[int, int], set[int]] = defaultdict(set)
    room_in_use: dict[tuple[int, int], set[int]] = defaultdict(set)
    class_cells: dict[tuple[int, int], int] = defaultdict(int)
    sec_mask: dict[tuple[int, int], int] = defaultdict(int)
    fac_mask: dict[tuple[int, int], int] = defaultdict(int)
    fac_periods: dict[int, int] = defaultdict(int)
    fac_sessions: dict[int, int] = defaultdict(int)
    fac_daily: dict[int, list[int]] = defaultdict(lambda: [0] * D)
    day_classes = [0] * D
    day_periods = [0] * D
    section_tasks: dict[int, dict[str, int]] = defaultdict(dict)
    repeats: dict[tuple[int, int], int] = defaultdict(int)
    theory_single = 0
    for r in valid:
        a = allocs[r["allocation_id"]]
        c = courses[a.course_id]
        d, s, ln = r["day"], r["start"], r["length"]
        if not (0 <= d < D and 1 <= s and s + ln - 1 <= P):
            continue                                        # illegal placement: reported as a violation
        m = ((1 << ln) - 1) << s
        sec_mask[(a.section_id, d)] |= m
        fac_mask[(a.faculty_id, d)] |= m
        section_tasks[a.section_id][r["task_key"]] = ln
        fac_periods[a.faculty_id] += ln
        fac_sessions[a.faculty_id] += 1
        fac_daily[a.faculty_id][d] += ln
        day_classes[d] += 1
        day_periods[d] += ln
        if ln == 1 and c.type != "LAB":
            theory_single += 1
            repeats[(a.id, d)] += 1
        for p in range(s, s + ln):
            fac_cells[(d, p)].add(a.faculty_id)
            class_cells[(d, p)] += 1
            if r["room_id"] in rooms:
                room_cells.add((r["room_id"], d, p))
                room_in_use[(d, p)].add(r["room_id"])

    # ---------------------------------------------------------------- room utilisation
    slots_per_room = D * P
    used_by_room: dict[int, int] = defaultdict(int)
    for rid, _d, _p in room_cells:
        used_by_room[rid] += 1
    room_rows = [{"id": r.id, "name": r.name, "type": r.type, "used": used_by_room.get(r.id, 0),
                  "available": slots_per_room, "pct": round(100 * used_by_room.get(r.id, 0) / slots_per_room, 1)}
                 for r in ds.rooms]
    by_type = {}
    for t in ("CLASSROOM", "LAB"):
        rs = [x for x in room_rows if x["type"] == t]
        if rs:
            by_type[t] = round(100 * sum(x["used"] for x in rs) / (len(rs) * slots_per_room), 1)
    total_room_slots = len(ds.rooms) * slots_per_room
    rooms_block = {
        "overall_pct": round(100 * len(room_cells) / total_room_slots, 1) if total_room_slots else None,
        "used_slots": len(room_cells), "available_slots": total_room_slots, "by_type": by_type, "by_room": room_rows}

    # ---------------------------------------------------------------- faculty workload
    fac_rows = []
    for f in ds.faculty:
        periods = fac_periods.get(f.id, 0)
        daily = fac_daily[f.id] if f.id in fac_daily else [0] * D
        fac_rows.append({"id": f.id, "name": f.name, "code": f.code, "periods": periods,
                         "sessions": fac_sessions.get(f.id, 0), "max_week": f.max_week,
                         "pct_of_max": round(100 * periods / f.max_week, 1) if f.max_week else None,
                         "days_taught": sum(1 for x in daily if x), "max_day_load": max(daily) if daily else 0,
                         "max_day": f.max_day, "daily": daily})
    loads = [x["periods"] for x in fac_rows if x["periods"] > 0]
    mean = statistics.fmean(loads) if loads else 0.0
    std = statistics.pstdev(loads) if len(loads) > 1 else 0.0
    cv = std / mean if mean else 0.0
    buckets = [("Not teaching", lambda p: p is not None and p == 0), ("1-25% of max", lambda p: p is not None and 0 < p <= 25),
               ("26-50%", lambda p: p is not None and 25 < p <= 50), ("51-75%", lambda p: p is not None and 50 < p <= 75),
               ("76-100%", lambda p: p is not None and 75 < p <= 100), ("Over max", lambda p: p is not None and p > 100)]
    faculty_block = {
        "stats": {"teaching": len(loads), "total_faculty": len(fac_rows), "mean": round(mean, 2), "std": round(std, 2),
                  "cv": round(cv, 3), "min": min(loads) if loads else 0, "max": max(loads) if loads else 0},
        "histogram": [{"label": lbl, "count": sum(1 for x in fac_rows if fn(x["pct_of_max"]))} for lbl, fn in buckets],
        "list": sorted(fac_rows, key=lambda x: (-x["periods"], x["name"]))}

    # ---------------------------------------------------------------- by day
    days_block = []
    for d in range(D):
        teaching = {fid for (dd, _p), fids in fac_cells.items() if dd == d for fid in fids}
        used_rooms = {rid for (dd, _p), rs in room_in_use.items() if dd == d for rid in rs}
        days_block.append({"day": d, "name": DAY_NAMES[d], "classes": day_classes[d], "periods": day_periods[d],
                           "hours": round(day_periods[d] * period_minutes / 60, 2), "faculty_teaching": len(teaching),
                           "rooms_used": len(used_rooms)})

    # ---------------------------------------------------------------- soft preference units (section / faculty view)
    section_gap = late_start = class_periods = 0
    gap_by_day = [0] * D
    gap_by_section: dict[int, dict] = {}
    for (sid, d), m in sec_mask.items():
        first, last, n = (m & -m).bit_length() - 1, m.bit_length() - 1, m.bit_count()
        g = last - first + 1 - n
        section_gap += g
        late_start += first - 1
        class_periods += n
        gap_by_day[d] += g
        e = gap_by_section.setdefault(sid, {"section": sections[sid].label, "gaps": 0, "late_start": 0})
        e["gaps"] += g
        e["late_start"] += first - 1
    unbalanced = 0
    for sid, tasks in section_tasks.items():
        total = sum(tasks.values())
        cap = -(-total // D) + 1 if total else 0
        for d in range(D):
            n = sec_mask.get((sid, d), 0).bit_count()
            unbalanced += max(0, n - cap)
    faculty_gap = long_run = 0
    for (_fid, _d), m in fac_mask.items():
        first, last = (m & -m).bit_length() - 1, m.bit_length() - 1
        faculty_gap += last - first + 1 - m.bit_count()
        run = 0
        for k in range(first, last + 1):
            if m >> k & 1:
                run += 1
                if run > DEFAULT_WEIGHTS.max_run:
                    long_run += 1
            else:
                run = 0
    same_day_repeat = sum(n - 1 for n in repeats.values() if n > 1)
    units = {"section_gap": section_gap, "late_start": late_start, "faculty_gap": faculty_gap,
             "same_day_repeat": same_day_repeat, "long_run": long_run, "unbalanced": unbalanced}
    soft_components = [{"key": k, "label": COMPONENTS[k][0], "units": units[k], "weight": getattr(DEFAULT_WEIGHTS, k),
                        "cost": units[k] * getattr(DEFAULT_WEIGHTS, k), "definition": COMPONENTS[k][1]} for k in units]

    # ---------------------------------------------------------------- quality score
    total_fac_periods = sum(fac_periods.values())
    comps = {
        "compactness": (_ratio_score(section_gap, section_gap + class_periods),
                        f"{section_gap} gap period(s) among {class_periods} class periods"),
        "early_start": (_ratio_score(late_start, late_start + class_periods),
                        f"{late_start} free period(s) before first classes"),
        "subject_spread": (_ratio_score(same_day_repeat, theory_single),
                           f"{same_day_repeat} same-day repeat(s) among {theory_single} single-period theory classes"),
        "faculty_continuity": (_ratio_score(faculty_gap + long_run, total_fac_periods),
                               f"{faculty_gap} faculty gap(s) + {long_run} long-run period(s) over {total_fac_periods} teaching periods"),
        "workload_balance": (100.0 * max(0.0, 1 - cv), f"coefficient of variation {cv:.3f} across {len(loads)} teaching faculty"),
        "day_balance": (_ratio_score(unbalanced, class_periods), f"{unbalanced} period(s) above a section's daily average + 1"),
    }
    wsum = sum(QUALITY_WEIGHTS.values())
    score = round(sum(QUALITY_WEIGHTS[k] * v[0] for k, v in comps.items()) / wsum, 1)
    reason = None
    if not feasible:
        reason = ("No score: the timetable has hard-constraint violations or unscheduled classes. "
                  "Fix those first; the component values below are shown for diagnosis only.")
    quality = {"score": score if feasible else None, "reason": reason, "formula": FORMULA,
               "components": [{"key": k, "label": k.replace("_", " ").capitalize(), "weight": QUALITY_WEIGHTS[k],
                               "score": round(v[0], 1), "detail": v[1]} for k, v in comps.items()]}

    # ---------------------------------------------------------------- heatmaps (day x period)
    conflicts = [[0] * P for _ in range(D)]
    for v in violations:
        if v.day is not None and v.period is not None and 0 <= v.day < D and 1 <= v.period <= P:
            conflicts[v.day][v.period - 1] += 1
    heat = {"days": [DAY_NAMES[d] for d in range(D)], "periods": P, "total_rooms": len(ds.rooms),
            "total_faculty": len(ds.faculty),
            "rooms_in_use": [[len(room_in_use.get((d, p), ())) for p in range(1, P + 1)] for d in range(D)],
            "faculty_teaching": [[len(fac_cells.get((d, p), ())) for p in range(1, P + 1)] for d in range(D)],
            "classes": [[class_cells.get((d, p), 0) for p in range(1, P + 1)] for d in range(D)],
            "conflicts": conflicts}

    return {
        "available": True, "feasible": feasible,
        "summary": {"required_classes": required, "scheduled_classes": scheduled_total,
                    "unscheduled_classes": unscheduled, "extra_classes": extra, "required_periods": req_periods,
                    "scheduled_periods": sum(r["length"] for r in valid),
                    "hard_violations": len(violations)},
        "hard": {"verifier": "Independent verifier (scheduler/check.py), re-run on every request",
                 "violations": len(violations),
                 "faculty_conflicts": by_kind.get(chk.FACULTY_CONFLICT, 0), "room_conflicts": by_kind.get(chk.ROOM_CONFLICT, 0),
                 "section_conflicts": by_kind.get(chk.SECTION_CONFLICT, 0),
                 "other": {k: n for k, n in by_kind.items() if k not in (chk.FACULTY_CONFLICT, chk.ROOM_CONFLICT, chk.SECTION_CONFLICT)},
                 "samples": [v.message for v in violations[:10]], "unscheduled": missing[:25]},
        "rooms": rooms_block, "faculty": faculty_block, "days": days_block,
        "gaps": {"total": section_gap, "by_day": [{"name": DAY_NAMES[d], "gaps": gap_by_day[d]} for d in range(D)],
                 "by_section": sorted(gap_by_section.values(), key=lambda x: (-x["gaps"], x["section"]))},
        "soft": {"components": soft_components, "weighted_total": sum(c["cost"] for c in soft_components)},
        "quality": quality, "heatmap": heat,
        "definitions": DEFINITIONS,
    }
