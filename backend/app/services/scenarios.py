"""What-if simulation.

A scenario is a LIST OF CHANGES. Running it applies them to an in-memory COPY of the college's data,
produces a candidate timetable with the existing scheduler, verifies it with the independent checker, and
compares it with the saved timetable. Only the `scenarios` table is written. The real data and the saved
timetable change only in `apply_scenario`, and only inside one transaction that re-verifies the result.

Supported changes (each has a `type`):
  faculty_availability  {faculty_id, add_unavailable:[[day,period]], remove_unavailable:[[day,period]]}
  faculty_limits        {faculty_id, max_per_day?, max_per_week?}
  room_closed           {room_id}                        the room is not scheduled any more
  add_section           {ref:"new:x", branch_id, year, name, strength, batches?, home_room_id?}
  add_course            {ref:"new:y", code, name, course_type, hours_per_week, session_length?, branch_id?}
  add_allocation        {section_id|ref, course_id|ref, faculty_id, hours_per_week?, batch?, sync_group?, students?}
  soft_weights          {section_gap?, late_start?, faculty_gap?, same_day_repeat?, long_run?, unbalanced?, max_run?}
  pin_class             {session_id, day?, start?, room_id?}   fix a class's time and/or room (strategy 'adjust' only)

Not supported (and rejected with a clear message rather than faked): closing a room for only some periods,
removing classes/sections, or changing which faculty teaches an existing allocation.
"""
from __future__ import annotations

import copy
import dataclasses
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..schemas import AllocationIn, CourseIn, SectionIn
from ..scheduler.adjust import Pins, PinError, adjust
from ..scheduler.check import check_violations
from ..scheduler.problem import (DAY_NAMES, AllocRec, CourseRec, Dataset, ProblemError, SectionRec, build_problem)
from ..scheduler.soft import soft_counts, weighted
from ..scheduler.solver import Solution, solve, state_from_sessions
from ..scheduler.weights import SoftWeights
from . import history
from .analytics import analyze
from .dataset import get_or_create_settings, load_dataset, load_session_rows, section_label

DATA_CHANGE_TYPES = {"faculty_availability", "faculty_limits", "room_closed", "add_section", "add_course", "add_allocation"}
WEIGHT_KEYS = ("section_gap", "late_start", "faculty_gap", "same_day_repeat", "long_run", "unbalanced", "max_run")
REF = re.compile(r"^new:[A-Za-z0-9_-]{1,20}$")
MAX_CHANGES = 50


class ScenarioError(Exception):
    def __init__(self, message: str, errors: list[str] | None = None, status: int = 422):
        super().__init__(message)
        self.message, self.errors, self.status = message, errors or [], status

    @property
    def detail(self):
        return {"message": self.message, "errors": self.errors}


# --------------------------------------------------------------------------- context
@dataclass
class Ctx:
    ds: Dataset
    rows: list[dict]
    branch_codes: dict[int, str]
    days: int
    periods: int
    period_minutes: int
    data_version: int


def build_context(db: DbSession, uid: int) -> Ctx:
    s = get_or_create_settings(db, uid)
    branches = db.scalars(select(m.Branch).where(m.Branch.owner_id == uid)).all()
    return Ctx(load_dataset(db, uid), load_session_rows(db, uid), {b.id: b.code for b in branches},
               s.days_per_week, s.periods_per_day, s.period_minutes, s.data_version)


# --------------------------------------------------------------------------- validation
def _pyd(model, payload: dict) -> list[str]:
    try:
        model(**payload)
        return []
    except ValidationError as e:
        return [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]


def _slots(value, days: int, periods: int, label: str) -> list[list[int]]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of [day, period] pairs")
    out = set()
    for pair in value:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in pair)):
            raise ValueError(f"{label}: each entry must be [day, period] with whole numbers")
        d, p = pair
        if not (0 <= d < days and 1 <= p <= periods):
            raise ValueError(f"{label}: [{d}, {p}] is outside the {days}-day, {periods}-period week (days start at 0 = Monday)")
        out.add((d, p))
    return [list(x) for x in sorted(out)]


def _int(c: dict, key: str, lo: int, hi: int, errs: list[str], tag: str, required=False):
    v = c.get(key)
    if v is None:
        if required:
            errs.append(f"{tag}: {key} is required")
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        errs.append(f"{tag}: {key} must be a whole number from {lo} to {hi}")
        return None
    return v


def validate_changes(ctx: Ctx, changes, strategy: str = "adjust") -> list[dict]:
    """Check every change against the college's current data. Raises ScenarioError listing ALL problems."""
    if not isinstance(changes, list) or not changes:
        raise ScenarioError("Add at least one change to simulate.")
    if len(changes) > MAX_CHANGES:
        raise ScenarioError(f"A scenario can have at most {MAX_CHANGES} changes.")
    ds = ctx.ds
    errs: list[str] = []
    out: list[dict] = []
    fac = {f.id: f for f in ds.faculty}
    rooms = {r.id: r for r in ds.rooms}
    sections = {s.id: s for s in ds.sections}
    courses = {c.id: c for c in ds.courses}
    sessions = {r["id"]: r for r in ctx.rows}
    new_sections: dict[str, dict] = {}
    new_courses: dict[str, dict] = {}
    labels = {s.label for s in ds.sections}
    codes = {c.code.casefold() for c in ds.courses}
    alloc_keys = {(a.section_id, a.course_id, a.batch or 0) for a in ds.allocations}
    closed: set[int] = set()
    pinned: set[str] = set()
    weights_seen = False

    for n, c in enumerate(changes, 1):
        tag = f"Change {n}"
        if not isinstance(c, dict) or "type" not in c:
            errs.append(f"{tag}: missing 'type'")
            continue
        t = c["type"]
        before = len(errs)

        if t == "faculty_availability":
            f = fac.get(c.get("faculty_id"))
            if f is None:
                errs.append(f"{tag}: faculty_id {c.get('faculty_id')!r} does not exist")
            try:
                add = _slots(c.get("add_unavailable"), ctx.days, ctx.periods, "add_unavailable")
                rem = _slots(c.get("remove_unavailable"), ctx.days, ctx.periods, "remove_unavailable")
            except ValueError as e:
                errs.append(f"{tag}: {e}")
                continue
            if not add and not rem:
                errs.append(f"{tag}: choose slots to make unavailable and/or available again")
            if f is not None and not errs[before:]:
                out.append({"type": t, "faculty_id": f.id, "add_unavailable": add, "remove_unavailable": rem})
        elif t == "faculty_limits":
            f = fac.get(c.get("faculty_id"))
            if f is None:
                errs.append(f"{tag}: faculty_id {c.get('faculty_id')!r} does not exist")
            day = _int(c, "max_per_day", 1, 12, errs, tag)
            week = _int(c, "max_per_week", 1, 84, errs, tag)
            if day is None and week is None and len(errs) == before:
                errs.append(f"{tag}: set max_per_day and/or max_per_week")
            if f is not None and len(errs) == before:
                out.append({"type": t, "faculty_id": f.id, "max_per_day": day, "max_per_week": week})
        elif t == "room_closed":
            r = rooms.get(c.get("room_id"))
            if r is None:
                errs.append(f"{tag}: room_id {c.get('room_id')!r} does not exist or is already closed")
            elif r.id in closed:
                errs.append(f"{tag}: {r.name} is already closed in this scenario")
            else:
                closed.add(r.id)
                out.append({"type": t, "room_id": r.id})
        elif t == "add_section":
            ref = c.get("ref")
            if not isinstance(ref, str) or not REF.match(ref) or ref in new_sections:
                errs.append(f"{tag}: ref must be unique text like 'new:cse4a'")
            code = ctx.branch_codes.get(c.get("branch_id"))
            if code is None:
                errs.append(f"{tag}: branch_id {c.get('branch_id')!r} does not exist")
            payload = {"branch_id": c.get("branch_id") or 0, "year": c.get("year"), "name": c.get("name"),
                       "strength": c.get("strength"), "batches": c.get("batches", 1), "home_room_id": c.get("home_room_id")}
            errs += [f"{tag}: {e}" for e in _pyd(SectionIn, payload)]
            hr = c.get("home_room_id")
            if hr is not None and (hr not in rooms or rooms[hr].type != "CLASSROOM" or hr in closed):
                errs.append(f"{tag}: home_room_id must be an open classroom")
            if len(errs) == before and code:
                label = section_label(code, payload["year"], payload["name"])
                if label in labels:
                    errs.append(f"{tag}: section {label} already exists")
                else:
                    labels.add(label)
                    rec = {"type": t, "ref": ref, "branch_id": c["branch_id"], "year": payload["year"], "name": payload["name"],
                           "strength": payload["strength"], "batches": payload["batches"], "home_room_id": hr, "label": label}
                    new_sections[ref] = rec
                    out.append(rec)
        elif t == "add_course":
            ref = c.get("ref")
            if not isinstance(ref, str) or not REF.match(ref) or ref in new_courses:
                errs.append(f"{tag}: ref must be unique text like 'new:ma301'")
            bid = c.get("branch_id")
            if bid is not None and bid not in ctx.branch_codes:
                errs.append(f"{tag}: branch_id {bid!r} does not exist")
            payload = {"code": c.get("code"), "name": c.get("name"), "branch_id": bid, "course_type": c.get("course_type", "THEORY"),
                       "hours_per_week": c.get("hours_per_week"), "session_length": c.get("session_length", 1)}
            errs += [f"{tag}: {e}" for e in _pyd(CourseIn, payload)]
            if len(errs) == before:
                if payload["hours_per_week"] % payload["session_length"]:
                    errs.append(f"{tag}: hours_per_week must be a multiple of session_length")
                elif str(payload["code"]).strip().casefold() in codes:
                    errs.append(f"{tag}: course code {payload['code']} already exists")
                else:
                    codes.add(str(payload["code"]).strip().casefold())
                    rec = {"type": t, "ref": ref, **{k: payload[k] for k in ("code", "name", "course_type", "hours_per_week", "session_length")},
                           "branch_id": bid}
                    new_courses[ref] = rec
                    out.append(rec)
        elif t == "add_allocation":
            sref, cref = c.get("section_id"), c.get("course_id")
            sec = sections.get(sref) if isinstance(sref, int) else new_sections.get(sref)
            course = courses.get(cref) if isinstance(cref, int) else new_courses.get(cref)
            f = fac.get(c.get("faculty_id"))
            if sec is None:
                errs.append(f"{tag}: section_id {sref!r} is not an existing section or a section added earlier in this scenario")
            if course is None:
                errs.append(f"{tag}: course_id {cref!r} is not an existing course or a course added earlier in this scenario")
            if f is None:
                errs.append(f"{tag}: faculty_id {c.get('faculty_id')!r} does not exist")
            payload = {"section_id": 0, "course_id": 0, "faculty_id": 0, "hours_per_week": c.get("hours_per_week"),
                       "batch": c.get("batch"), "sync_group": c.get("sync_group"), "students": c.get("students")}
            errs += [f"{tag}: {e}" for e in _pyd(AllocationIn, payload)]
            if len(errs) == before:
                batches = sec["batches"] if isinstance(sec, dict) else sec.batches
                length = course["session_length"] if isinstance(course, dict) else course.length
                chours = course["hours_per_week"] if isinstance(course, dict) else course.hours
                hours = payload["hours_per_week"] or chours
                key = (sref, cref, payload["batch"] or 0)
                if payload["batch"] and payload["batch"] > max(batches, 1):
                    errs.append(f"{tag}: batch {payload['batch']} does not exist (the section has {batches})")
                elif hours % length:
                    errs.append(f"{tag}: {hours} hours/week is not a multiple of the session length ({length})")
                elif key in alloc_keys:
                    errs.append(f"{tag}: this course is already allocated to that section/batch")
                else:
                    alloc_keys.add(key)
                    out.append({"type": t, "section_id": sref, "course_id": cref, "faculty_id": f.id,
                                "hours_per_week": payload["hours_per_week"], "batch": payload["batch"],
                                "sync_group": payload["sync_group"] or None, "students": payload["students"]})
        elif t == "soft_weights":
            if weights_seen:
                errs.append(f"{tag}: only one soft_weights change is allowed")
            weights_seen = True
            given = {}
            for k in WEIGHT_KEYS:
                if c.get(k) is not None:
                    lo, hi = (1, 12) if k == "max_run" else (0, 20)
                    v = _int(c, k, lo, hi, errs, tag)
                    if v is not None:
                        given[k] = v
            if not given and len(errs) == before:
                errs.append(f"{tag}: set at least one weight")
            if len(errs) == before:
                out.append({"type": t, **given})
        elif t == "pin_class":
            if strategy != "adjust":
                errs.append(f"{tag}: pinned classes need the 'adjust' strategy (a full rebuild cannot keep them fixed)")
            s_row = sessions.get(c.get("session_id"))
            if s_row is None:
                errs.append(f"{tag}: session_id {c.get('session_id')!r} is not a class in the saved timetable")
                continue
            day, start, room = c.get("day"), c.get("start"), c.get("room_id")
            if (day is None) != (start is None):
                errs.append(f"{tag}: give both day and start, or neither")
            if day is not None and not (isinstance(day, int) and 0 <= day < ctx.days and isinstance(start, int) and 1 <= start <= ctx.periods):
                errs.append(f"{tag}: day/start are outside the teaching week")
            if room is not None and (room not in rooms or room in closed):
                errs.append(f"{tag}: room_id {room!r} does not exist or is closed")
            if day is None and room is None:
                errs.append(f"{tag}: pin a time (day + start) and/or a room")
            if s_row["task_key"] in pinned:
                errs.append(f"{tag}: that class is already pinned in this scenario")
            if len(errs) == before:
                pinned.add(s_row["task_key"])
                out.append({"type": t, "session_id": s_row["id"], "day": day, "start": start, "room_id": room})
        else:
            errs.append(f"{tag}: unknown change type {t!r}")
    if errs:
        raise ScenarioError(f"{len(errs)} problem(s) in the scenario's changes.", errs)
    return out


def describe_change(ctx: Ctx, c: dict) -> str:
    fac = {f.id: f for f in ctx.ds.faculty}
    rooms = {r.id: r for r in ctx.ds.rooms}
    t = c["type"]
    slot = lambda pairs: ", ".join(f"{DAY_NAMES[d][:3]} P{p}" for d, p in pairs)   # noqa: E731
    if t == "faculty_availability":
        parts = []
        if c["add_unavailable"]:
            parts.append(f"unavailable {slot(c['add_unavailable'])}")
        if c["remove_unavailable"]:
            parts.append(f"available again {slot(c['remove_unavailable'])}")
        return f"{fac[c['faculty_id']].name}: " + "; ".join(parts)
    if t == "faculty_limits":
        bits = [f"max {c['max_per_day']}/day" if c.get("max_per_day") else None, f"max {c['max_per_week']}/week" if c.get("max_per_week") else None]
        return f"{fac[c['faculty_id']].name}: " + ", ".join(b for b in bits if b)
    if t == "room_closed":
        return f"Room {rooms[c['room_id']].name} is closed"
    if t == "add_section":
        return f"New section {c['label']} ({c['strength']} students, {c['batches']} batch(es))"
    if t == "add_course":
        return f"New course {c['code']} · {c['name']} ({c['course_type'].lower()}, {c['hours_per_week']} h/week)"
    if t == "add_allocation":
        sec = next((s.label for s in ctx.ds.sections if s.id == c["section_id"]), None) if isinstance(c["section_id"], int) else c["section_id"]
        course = next((x.code for x in ctx.ds.courses if x.id == c["course_id"]), None) if isinstance(c["course_id"], int) else c["course_id"]
        return f"{fac[c['faculty_id']].name} teaches {course} to {sec}" + (f" batch {c['batch']}" if c.get("batch") else "")
    if t == "soft_weights":
        return "Preference weights: " + ", ".join(f"{k.replace('_', ' ')} = {c[k]}" for k in WEIGHT_KEYS if k in c)
    if t == "pin_class":
        row = next((r for r in ctx.rows if r["id"] == c["session_id"]), None)
        a = next((x for x in ctx.ds.allocations if row and x.id == row["allocation_id"]), None)
        course = next((x.code for x in ctx.ds.courses if a and x.id == a.course_id), "class")
        sec = next((s.label for s in ctx.ds.sections if a and s.id == a.section_id), "")
        bits = []
        if c.get("day") is not None:
            bits.append(f"fixed at {DAY_NAMES[c['day']]} P{c['start']}")
        if c.get("room_id"):
            bits.append(f"in room {rooms[c['room_id']].name}")
        return f"Pin {course} ({sec}): " + ", ".join(bits)
    return t


# --------------------------------------------------------------------------- applying changes in memory
@dataclass
class Applied:
    ds: Dataset
    weights: SoftWeights | None = None
    pins: Pins = field(default_factory=Pins)
    has_data_changes: bool = False
    new_alloc_count: int = 0


def apply_in_memory(ctx: Ctx, changes: list[dict]) -> Applied:
    """Return a modified COPY of the dataset. The original is never touched."""
    ds = copy.deepcopy(ctx.ds)
    out = Applied(ds)
    fac = {f.id: f for f in ds.faculty}
    sec_ids: dict[str, int] = {}
    course_ids: dict[str, int] = {}
    session_by_id = {r["id"]: r for r in ctx.rows}
    for c in changes:
        t = c["type"]
        if t == "faculty_availability":
            f = fac[c["faculty_id"]]
            f.unavailable = frozenset((f.unavailable - {tuple(x) for x in c["remove_unavailable"]}) | {tuple(x) for x in c["add_unavailable"]})
        elif t == "faculty_limits":
            f = fac[c["faculty_id"]]
            f.max_day = c["max_per_day"] or f.max_day
            f.max_week = c["max_per_week"] or f.max_week
        elif t == "room_closed":
            ds.rooms = [r for r in ds.rooms if r.id != c["room_id"]]
        elif t == "add_section":
            sid = -(len(sec_ids) + 1)
            sec_ids[c["ref"]] = sid
            ds.sections.append(SectionRec(sid, c["label"], c["branch_id"], c["year"], c["strength"], c["batches"],
                                          c["home_room_id"], ctx.branch_codes[c["branch_id"]]))
        elif t == "add_course":
            cid = -(len(course_ids) + 1)
            course_ids[c["ref"]] = cid
            ds.courses.append(CourseRec(cid, c["code"], c["name"], "LAB" if c["course_type"] == "LAB" else "THEORY",
                                        c["hours_per_week"], c["session_length"], c["branch_id"]))
        elif t == "add_allocation":
            out.new_alloc_count += 1
            sid = sec_ids[c["section_id"]] if isinstance(c["section_id"], str) else c["section_id"]
            cid = course_ids[c["course_id"]] if isinstance(c["course_id"], str) else c["course_id"]
            ds.allocations.append(AllocRec(-out.new_alloc_count, sid, cid, c["faculty_id"], c["hours_per_week"], c["batch"],
                                           c["sync_group"], c["students"]))
        elif t == "soft_weights":
            out.weights = dataclasses.replace(SoftWeights(), **{k: c[k] for k in WEIGHT_KEYS if k in c})
        elif t == "pin_class":
            row = session_by_id[c["session_id"]]
            if c.get("day") is not None:
                out.pins.times[row["task_key"]] = (c["day"], c["start"])
            if c.get("room_id") is not None:
                out.pins.rooms[row["allocation_id"]] = c["room_id"]
        if t in DATA_CHANGE_TYPES:
            out.has_data_changes = True
    return out


# --------------------------------------------------------------------------- running
def _metrics(an: dict, soft_default: int | None = None) -> dict:
    return {
        "hard_violations": an["summary"]["hard_violations"], "unscheduled_classes": an["summary"]["unscheduled_classes"],
        "scheduled_classes": an["summary"]["scheduled_classes"], "required_classes": an["summary"]["required_classes"],
        "quality_score": an["quality"]["score"], "soft_cost": an["soft"]["weighted_total"],
        "section_gaps": an["gaps"]["total"], "workload_cv": an["faculty"]["stats"]["cv"],
        "workload_max": an["faculty"]["stats"]["max"], "room_utilization": an["rooms"]["overall_pct"],
        "faculty_teaching": an["faculty"]["stats"]["teaching"]}


_COMPARE = [  # key, label, kind, lower_is_better (None = informational)
    ("hard_violations", "Hard-constraint violations", "hard", True), ("unscheduled_classes", "Unscheduled classes", "hard", True),
    ("scheduled_classes", "Scheduled classes", "info", None), ("quality_score", "Quality score (0-100)", "soft", False),
    ("soft_cost", "Soft-preference cost", "soft", True), ("section_gaps", "Section gaps", "soft", True),
    ("workload_cv", "Faculty workload spread (CV)", "soft", True), ("workload_max", "Heaviest faculty load (periods/week)", "info", None),
    ("room_utilization", "Room utilization %", "info", None)]


def _comparison(base: dict, cand: dict) -> list[dict]:
    rows = []
    for key, label, kind, lower in _COMPARE:
        b, c = base[key], cand[key]
        delta = None if b is None or c is None else round(c - b, 3)
        verdict = "info" if lower is None or delta is None else ("same" if delta == 0 else "better" if (delta < 0) == lower else "worse")
        rows.append({"key": key, "label": label, "kind": kind, "baseline": b, "candidate": c, "delta": delta, "verdict": verdict})
    return rows


def _describe_row(ds: Dataset, r: dict) -> dict:
    allocs = {a.id: a for a in ds.allocations}
    a = allocs[r["allocation_id"]]
    sec = next(s for s in ds.sections if s.id == a.section_id)
    course = next(c for c in ds.courses if c.id == a.course_id)
    fac = next(f for f in ds.faculty if f.id == a.faculty_id)
    room = next((x.name for x in ds.rooms if x.id == r["room_id"]), None)
    return {"class": f"{course.code} · {sec.label}" + (f" batch {a.batch}" if a.batch else ""), "course": course.name,
            "faculty": fac.name, "day": r["day"], "start": r["start"], "length": r["length"], "room": room}


def _placement_changes(ds_base: Dataset, base_rows: list[dict], ds2: Dataset, cand_rows: list[dict]) -> dict:
    key = lambda r: (r["task_key"], r["allocation_id"])   # noqa: E731
    old = {key(r): r for r in base_rows}
    new = {key(r): r for r in cand_rows}
    items, counts = [], {"unchanged": 0, "moved": 0, "room_changed": 0, "added": 0, "removed": 0}
    for k, n in new.items():
        o = old.get(k)
        if o is None:
            counts["added"] += 1
            items.append({"change": "added", "to": _describe_row(ds2, n)})
        elif (o["day"], o["start"]) != (n["day"], n["start"]):
            counts["moved"] += 1
            items.append({"change": "moved", "from": _describe_row(ds_base, o), "to": _describe_row(ds2, n)})
        elif o["room_id"] != n["room_id"]:
            counts["room_changed"] += 1
            items.append({"change": "room_changed", "from": _describe_row(ds_base, o), "to": _describe_row(ds2, n)})
        else:
            counts["unchanged"] += 1
    for k, o in old.items():
        if k not in new:
            counts["removed"] += 1
            items.append({"change": "removed", "from": _describe_row(ds_base, o)})
    return {"summary": counts, "items": items[:200], "truncated": len(items) > 200}


def run_scenario(db: DbSession, uid: int, sc: m.Scenario) -> None:
    """Simulate `sc`. Writes ONLY to the scenario row (the caller commits)."""
    started = time.monotonic()
    ctx = build_context(db, uid)
    if not ctx.rows:
        raise ScenarioError("There is no saved timetable to compare against. Generate one first.", status=409)
    try:
        base_problem = build_problem(ctx.ds)
        base_state = state_from_sessions(base_problem, ctx.rows)
    except ProblemError as e:
        raise ScenarioError("Your current data has problems, so there is no valid baseline.", e.errors[:5], 409)
    except ValueError:
        raise ScenarioError("The saved timetable is out of date (your data changed after it was generated). Regenerate it first.", status=409)
    fingerprint = history.rows_fingerprint(ctx.rows)
    base_an = analyze(ctx.ds, ctx.rows, ctx.period_minutes)
    result: dict = {"strategy_requested": sc.strategy, "baseline_generated_rows": len(ctx.rows)}

    def finish(status: str, **extra):
        sc.status, sc.run_at = status, datetime.now(timezone.utc)
        sc.base_data_version, sc.base_fingerprint = ctx.data_version, fingerprint
        result.update(extra)
        result["status"], result["seconds"] = status, round(time.monotonic() - started, 2)
        sc.result = result

    try:
        changes = validate_changes(ctx, sc.changes, sc.strategy)
    except ScenarioError as e:
        sc.candidate = None
        finish("infeasible", changes=[], diagnostics=[e.message, *e.errors], candidate=None)
        return
    result["changes"] = [describe_change(ctx, c) for c in changes]
    applied = apply_in_memory(ctx, changes)
    try:
        problem = build_problem(applied.ds)
    except ProblemError as e:
        sc.candidate = None
        finish("infeasible", diagnostics=["After these changes the college's data cannot be scheduled at all:", *e.errors[:15]],
               candidate=None)
        return
    if applied.weights:
        problem = dataclasses.replace(problem, weights=applied.weights)

    notes: list[str] = []
    limit = max(1, min(sc.time_limit, 60))
    try:
        if sc.strategy == "adjust":
            sol, n = adjust(problem, ctx.rows, pins=applied.pins, time_limit=limit, seed=1, optimize_preferences=applied.weights is not None)
            notes += n
            used = "adjust"
            if sol.hard > 0 and not applied.pins:
                notes.append(f"Repairing the current timetable left {sol.hard} violation(s); trying a full rebuild with the genetic algorithm.")
                full = solve(problem, time_limit=limit, seed=1)
                if (full.hard, full.soft) < (sol.hard, sol.soft):
                    sol, used = full, "adjust, then rebuild"
        else:
            sol = solve(problem, time_limit=limit, seed=1)
            used = "rebuild"
    except PinError as e:
        sc.candidate = None
        finish("infeasible", diagnostics=[f"A pinned class cannot be honoured: {e}"], candidate=None)
        return

    rows2 = [{**r, "id": i} for i, r in enumerate(sol.sessions(), 1)]
    violations = check_violations(applied.ds, rows2)                    # the independent verifier decides validity
    valid = not violations
    cand_an = analyze(applied.ds, rows2, ctx.period_minutes)
    base_metrics, cand_metrics = _metrics(base_an), _metrics(cand_an)
    comparison = _comparison(base_metrics, cand_metrics)
    extra: dict = {}
    if applied.weights:
        extra["soft_under_scenario_weights"] = {
            "weights": applied.weights.as_dict(),
            "baseline": weighted(soft_counts(base_problem, base_state.slot, applied.weights.max_run), applied.weights),
            "candidate": sol.soft}
    by_kind: dict[str, int] = {}
    for v in violations:
        by_kind[v.kind] = by_kind.get(v.kind, 0) + 1
    diagnostics = []
    if not valid:
        diagnostics = [f"The candidate still has {len(violations)} hard-constraint violation(s) after {round(sol.seconds, 1)}s of searching:",
                       *[v.message for v in violations[:15]],
                       "Try the 'Rebuild' strategy with a longer time limit, relax a change (fewer unavailable slots, keep a room open), "
                       "or add capacity (rooms or faculty)."]
    sc.candidate = {"rows": [{k: r[k] for k in ("allocation_id", "task_key", "day", "start", "length", "room_id")} for r in rows2]}
    finish("ready" if valid else "invalid",
           strategy_used=used, notes=notes, diagnostics=diagnostics, candidate_valid=valid,
           verification={"checker": "Independent verifier (scheduler/check.py)", "violations": len(violations),
                         "by_kind": by_kind, "solver_reported_hard": sol.hard, "messages": [v.message for v in violations[:15]]},
           baseline=base_metrics, candidate=cand_metrics, comparison=comparison,
           placement_changes=_placement_changes(ctx.ds, ctx.rows, applied.ds, rows2),
           data_changes=applied.has_data_changes, **extra)


# --------------------------------------------------------------------------- applying for real
def persist_changes(db: DbSession, uid: int, changes: list[dict]) -> dict[int, int]:
    """Write the scenario's data changes to the database (inside the caller's transaction).
    Returns {temporary negative allocation id: real id}."""
    secs: dict[str, m.Section] = {}
    crs: dict[str, m.Course] = {}
    new_allocs: list[m.Allocation] = []

    def owned(model, obj_id):
        obj = db.get(model, obj_id)
        if obj is None or obj.owner_id != uid:
            raise ScenarioError("A record used by this scenario no longer exists. Run the scenario again.", status=409)
        return obj

    for c in changes:
        t = c["type"]
        if t == "faculty_availability":
            f = owned(m.Faculty, c["faculty_id"])
            cur = {tuple(x) for x in (f.unavailable or [])}
            cur = (cur - {tuple(x) for x in c["remove_unavailable"]}) | {tuple(x) for x in c["add_unavailable"]}
            f.unavailable = [list(x) for x in sorted(cur)]
        elif t == "faculty_limits":
            f = owned(m.Faculty, c["faculty_id"])
            f.max_per_day = c["max_per_day"] or f.max_per_day
            f.max_per_week = c["max_per_week"] or f.max_per_week
        elif t == "room_closed":
            owned(m.Room, c["room_id"]).is_active = False
        elif t == "add_section":
            obj = m.Section(owner_id=uid, branch_id=c["branch_id"], year=c["year"], name=c["name"], strength=c["strength"],
                            batches=c["batches"], home_room_id=c["home_room_id"])
            db.add(obj)
            db.flush()
            secs[c["ref"]] = obj
        elif t == "add_course":
            obj = m.Course(owner_id=uid, code=c["code"], name=c["name"], branch_id=c["branch_id"], course_type=c["course_type"],
                           hours_per_week=c["hours_per_week"], session_length=c["session_length"])
            db.add(obj)
            db.flush()
            crs[c["ref"]] = obj
        elif t == "add_allocation":
            sid = secs[c["section_id"]].id if isinstance(c["section_id"], str) else c["section_id"]
            cid = crs[c["course_id"]].id if isinstance(c["course_id"], str) else c["course_id"]
            obj = m.Allocation(owner_id=uid, section_id=sid, course_id=cid, faculty_id=c["faculty_id"],
                               hours_per_week=c["hours_per_week"], batch=c["batch"], sync_group=c["sync_group"], students=c["students"])
            db.add(obj)
            new_allocs.append(obj)
    db.flush()
    return {-(i + 1): a.id for i, a in enumerate(new_allocs)}


def apply_scenario(db: DbSession, user: m.User, sc: m.Scenario) -> dict:
    """Make the candidate the saved timetable (and persist the scenario's data changes), atomically."""
    uid = user.id
    if sc.status == "applied":
        raise ScenarioError("This scenario was already applied.", status=409)
    if sc.status != "ready" or not sc.candidate:
        raise ScenarioError("Only a verified candidate can be applied. Run the scenario and fix any problems first.", status=409)
    ctx = build_context(db, uid)
    if ctx.data_version != sc.base_data_version or history.rows_fingerprint(ctx.rows) != sc.base_fingerprint:
        raise ScenarioError("Your data or the saved timetable changed after this simulation ran. Run it again before applying.", status=409)
    changes = validate_changes(ctx, sc.changes, sc.strategy)
    applied = apply_in_memory(ctx, changes)
    cand = [{**r, "id": i} for i, r in enumerate(sc.candidate["rows"], 1)]
    violations = check_violations(applied.ds, cand)
    if violations:
        raise ScenarioError("Re-validation failed: the candidate no longer satisfies the hard constraints.", [v.message for v in violations[:10]], 409)
    try:
        problem = build_problem(applied.ds)
        state_from_sessions(problem, cand)
    except (ProblemError, ValueError) as e:
        raise ScenarioError("Re-validation failed: the candidate does not match the changed data.", getattr(e, "errors", [str(e)])[:5], 409)

    meta = db.get(m.TimetableMeta, uid)
    before_rows, before_meta = history.snapshot(ctx.rows), history.meta_snapshot(meta)
    try:
        alloc_map = persist_changes(db, uid, changes)
        db.execute(delete(m.Session).where(m.Session.owner_id == uid))
        db.add_all(m.Session(owner_id=uid, allocation_id=alloc_map.get(r["allocation_id"], r["allocation_id"]), task_key=r["task_key"],
                             day=r["day"], start=r["start"], length=r["length"], room_id=r["room_id"]) for r in sc.candidate["rows"])
        db.flush()
        ds_after, rows_after = load_dataset(db, uid), load_session_rows(db, uid)      # verify what is ACTUALLY in the database
        post = check_violations(ds_after, rows_after)
        if post:
            raise ScenarioError("Post-write verification failed; nothing was changed.", [v.message for v in post[:10]], 409)
        settings = get_or_create_settings(db, uid)
        if applied.has_data_changes:                       # a pure timetable replacement leaves the data (and its version) alone
            settings.data_version += 1
        meta = meta or m.TimetableMeta(owner_id=uid)
        meta.generated_at, meta.hard, meta.seconds, meta.generations = datetime.now(timezone.utc), 0, 0.0, 0
        meta.soft = sc.result["candidate"]["soft_cost"]
        meta.data_version = settings.data_version
        db.add(meta)
        sc.status, sc.applied_at = "applied", datetime.now(timezone.utc)
        data_changes = applied.has_data_changes
        history.record(db, user, "simulation", f"Applied what-if scenario “{sc.name}”",
                       {"scenario_id": sc.id, "changes": sc.result.get("changes", []), "data_changes": data_changes,
                        "before_rows": before_rows, "before_meta": before_meta,
                        "after_fingerprint": history.rows_fingerprint(rows_after), "classes": len(rows_after)},
                       undoable=not data_changes,
                       undo_note="This scenario also changed faculty/room/section/course data, which cannot be undone automatically. "
                                 "Revert those on their pages." if data_changes else None)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"applied": True, "classes": len(rows_after), "data_changes": data_changes}
