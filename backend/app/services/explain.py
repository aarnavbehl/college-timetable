"""'Why this slot?': explain a saved placement from the solver's own constraint checks and scoring.

HONESTY NOTE. The solver is a randomised search that does not log its decisions, so this module never
claims to know why the search chose a slot. It answers questions that CAN be answered exactly from the saved
timetable:
  * which hard constraints the placement satisfies (checked from the session rows, with evidence);
  * the marginal soft-preference effect of this class being here (re-score with and without it);
  * for every other legal slot: whether it is feasible, the real reason it is not (State.reasons_at, the
    same check the editor uses), and the exact change in the whole timetable's soft score if the class moved there.
Everything is computed on in-memory copies: nothing is written and the timetable is never altered.
"""
from __future__ import annotations

import random

from ..scheduler.problem import DAY_NAMES, ProblemError, build_problem, Dataset, students_for
from ..scheduler.soft import soft_breakdown
from ..scheduler.solver import state_from_sessions
from ..scheduler.weights import COMPONENTS

METHOD = ("Explained by re-checking the saved timetable with the solver's own constraint checks and scoring on an "
          "in-memory copy. The solver does not record the order in which it considered slots, so this describes why the "
          "placement is valid and how good it is, not the search's internal reasoning.")

_UP = {"section_gap": "Leaves a free period between this section's classes",
       "late_start": "Pushes this section's first class of the day later",
       "faculty_gap": "Creates a free period in the faculty member's day",
       "same_day_repeat": "Adds a second single-period session of this course on the same day",
       "long_run": "Extends a faculty member's teaching run beyond 3 periods",
       "unbalanced": "Makes this section's day heavier than its weekly average + 1"}
_DOWN = {"section_gap": "Fills a free period in this section's day",
         "late_start": "Lets this section start its day earlier",
         "faculty_gap": "Fills a free period in the faculty member's day",
         "same_day_repeat": "Avoids a same-day repeat",
         "long_run": "Breaks up a long teaching run",
         "unbalanced": "Helps balance this section's days"}


class ExplainError(Exception):
    """The saved timetable cannot be explained (message is shown to the user)."""


def _periods(r: dict) -> range:
    return range(r["start"], r["start"] + r["length"])


def explain(ds: Dataset, rows: list[dict], session_id: int, *, stale: bool = False) -> dict:
    try:
        p = build_problem(ds)
    except ProblemError as e:
        raise ExplainError("Explanations need valid college data, but it currently has problems: "
                           + " ".join(e.errors[:3])) from e
    try:
        st = state_from_sessions(p, rows)
    except ValueError as e:
        raise ExplainError(str(e)) from e

    row = next(r for r in rows if r["id"] == session_id)
    task = p.task_by_key()[row["task_key"]]
    i = task.index
    day, start = st.slot[i]
    cfg = ds.config
    allocs = {a.id: a for a in ds.allocations}
    sections = {s.id: s for s in ds.sections}
    courses = {c.id: c for c in ds.courses}
    faculty = {f.id: f for f in ds.faculty}
    rooms = {r.id: r for r in ds.rooms}
    mine = [r for r in rows if r["task_key"] == task.key]
    others = [r for r in rows if r["task_key"] != task.key]
    when = f"{DAY_NAMES[day]} period{'s' if task.length > 1 else ''} {start}" + (f"-{start + task.length - 1}" if task.length > 1 else "")

    def atoms_of(r):
        a = allocs[r["allocation_id"]]
        sec = sections[a.section_id]
        return {(sec.id, a.batch)} if a.batch else {(sec.id, b) for b in range(1, max(sec.batches, 1) + 1)}

    def overlapping(pred, rs):
        return [r for r in rs if r["day"] == day and set(_periods(r)) & set(_periods(row)) and pred(r)]

    def label(r):
        a = allocs[r["allocation_id"]]
        c = courses[a.course_id]
        return f"{c.code} ({sections[a.section_id].label}{' b' + str(a.batch) if a.batch else ''})"

    # ------------------------------------------------------------------ members
    members = []
    for r in mine:
        a = allocs[r["allocation_id"]]
        sec, c, f, room = sections[a.section_id], courses[a.course_id], faculty[a.faculty_id], rooms.get(r["room_id"])
        members.append({"section": sec.label, "batch": a.batch, "course_code": c.code, "course_name": c.name,
                        "course_type": c.type, "faculty": f.name, "room": room.name if room else None,
                        "room_capacity": room.capacity if room else None, "students": students_for(sec, a)})

    # ------------------------------------------------------------------ hard constraints
    checks = []

    def add(cid, text, ok, evidence):
        checks.append({"id": cid, "label": text, "satisfied": ok, "evidence": evidence})

    in_week = 0 <= day < cfg.days and cfg.block_ok(start, task.length)
    add("placement", "Inside the teaching day and not across the lunch break", in_week,
        f"{when}; the day has {cfg.periods} periods" + (f" with lunch after period {cfg.lunch_after}" if cfg.lunch_after else ""))
    for r in mine:
        a = allocs[r["allocation_id"]]
        sec, c, f = sections[a.section_id], courses[a.course_id], faculty[a.faculty_id]
        who = label(r)
        clash = overlapping(lambda x, mine_atoms=atoms_of(r): bool(atoms_of(x) & mine_atoms), others)
        add("students", f"{who}: students have no other class", not clash,
            f"No other class for {sec.label}{' batch ' + str(a.batch) if a.batch else ''} at {when}" if not clash
            else "Clashes with " + ", ".join(label(x) for x in clash))
        fclash = overlapping(lambda x, fid=f.id: allocs[x["allocation_id"]].faculty_id == fid, [x for x in rows if x is not r])
        add("faculty_free", f"{who}: {f.name} is free", not fclash,
            f"{f.name} has no other class at {when}" if not fclash else "Also teaching " + ", ".join(label(x) for x in fclash))
        bad = [pp for pp in _periods(row) if (day, pp) in f.unavailable]
        add("faculty_available", f"{who}: {f.name} is available", not bad,
            f"{f.name} has no unavailable periods at {when}" if not bad else f"Marked unavailable at period(s) {bad}")
        load = sum(x["length"] for x in rows if x["day"] == day and allocs[x["allocation_id"]].faculty_id == f.id)
        add("faculty_daily", f"{who}: within {f.name}'s daily limit", load <= f.max_day,
            f"{f.name} teaches {load} period(s) on {DAY_NAMES[day]} (limit {f.max_day})")
        room = rooms.get(r["room_id"])
        if room is None:
            add("room", f"{who}: has a room", False, "No room is assigned (it may have been deleted or closed)")
            continue
        want = "LAB" if c.type == "LAB" else "CLASSROOM"
        add("room_type", f"{who}: room type fits", room.type == want,
            f"{room.name} is a {room.type.lower()}; a {c.type.lower()} class needs a {want.lower()}")
        need = students_for(sec, a)
        add("room_capacity", f"{who}: room is big enough", room.capacity >= need,
            f"{room.name} holds {room.capacity}; the group has {need} students")
        rclash = overlapping(lambda x, rid=room.id: x["room_id"] == rid, [x for x in rows if x is not r])
        add("room_free", f"{who}: {room.name} is free", not rclash,
            f"No other class in {room.name} at {when}" if not rclash else "Also used by " + ", ".join(label(x) for x in rclash))
    if len(mine) > 1:
        add("sync", "Runs at the same time as its partner classes (lab rotation / elective)", True,
            "Scheduled together with " + ", ".join(label(x) for x in mine[1:]) + "; they share one slot but use different faculty and rooms")

    # ------------------------------------------------------------------ soft preferences (marginal effect)
    slots = list(st.slot)
    with_ = soft_breakdown(p, slots)
    without_slots = list(slots)
    without_slots[i] = None
    without = soft_breakdown(p, without_slots)
    marginal = []
    for k in COMPONENTS:
        units = with_[k]["count"] - without[k]["count"]
        cost = with_[k]["cost"] - without[k]["cost"]
        if units:
            marginal.append({"key": k, "label": COMPONENTS[k][0], "units": units, "cost": cost,
                             "text": f"{(_UP if units > 0 else _DOWN)[k]} ({'+' if cost > 0 else ''}{cost})"})
    current_total = with_["total"]["cost"]

    # ------------------------------------------------------------------ alternatives
    rng = random.Random(0)
    free_state = st.copy()
    free_state.unplace(i)
    slot_list = []
    for d, s in task.starts:
        cost, alt_rooms = free_state.eval_slot(i, d, s, rng)
        alt = list(slots)
        alt[i] = (d, s)
        bd = soft_breakdown(p, alt)
        slot_list.append({
            "day": d, "start": s, "current": (d, s) == (day, start), "feasible": cost == 0,
            "reasons": free_state.reasons_at(i, d, s) if cost else [],
            "soft_total": bd["total"]["cost"], "delta": bd["total"]["cost"] - current_total,
            "component_changes": {k: bd[k]["cost"] - with_[k]["cost"] for k in COMPONENTS if bd[k]["cost"] != with_[k]["cost"]},
            "rooms": [rooms[p.room_ids[x]].name for x in alt_rooms] if cost == 0 else []})
    slot_list.sort(key=lambda x: (not x["current"], not x["feasible"], x["delta"] if x["feasible"] else 0, x["day"], x["start"]))
    others_ = [x for x in slot_list if not x["current"]]
    feasible = [x for x in others_ if x["feasible"]]
    better = [x for x in feasible if x["delta"] < 0]
    summary = {"legal_slots": len(slot_list), "feasible_alternatives": len(feasible),
               "infeasible_alternatives": len(others_) - len(feasible), "better": len(better),
               "equal": sum(1 for x in feasible if x["delta"] == 0), "worse": sum(1 for x in feasible if x["delta"] > 0),
               "best": min(better, key=lambda x: x["delta"]) if better else None}

    # ------------------------------------------------------------------ rooms at this slot
    room_block = []
    for j, r in enumerate(mine):
        a = allocs[r["allocation_id"]]
        sec = sections[a.section_id]
        options = []
        for rid in (p.room_ids[x] for x in task.room_opts[j]):
            room = rooms[rid]
            occ = overlapping(lambda x, rid=rid: x["room_id"] == rid, [x for x in rows if x is not r])
            status = "assigned" if rid == r["room_id"] else ("busy" if occ else "free")
            options.append({"name": room.name, "capacity": room.capacity, "status": status,
                            "home_room": rid == sec.home_room_id, "occupied_by": [label(x) for x in occ]})
        options.sort(key=lambda o: ({"assigned": 0, "free": 1, "busy": 2}[o["status"]], o["name"]))
        room_block.append({"class": label(r), "options": options})

    # ------------------------------------------------------------------ warnings / trade-offs
    warnings = []
    failed = [c for c in checks if not c["satisfied"]]
    if failed:
        warnings.append(f"{len(failed)} hard constraint(s) are NOT satisfied for this class: "
                        + "; ".join(c["label"] for c in failed) + ". The timetable is invalid until this is fixed.")
    if better:
        b = summary["best"]
        warnings.append(f"{len(better)} feasible slot(s) would give a lower soft-preference score for the whole timetable "
                        f"(best: {DAY_NAMES[b['day']]} period {b['start']}, {b['delta']}). The solver is a heuristic and does not "
                        "guarantee an optimum; this placement is valid, but not provably the best one.")
    for block, r in zip(room_block, mine):
        a = allocs[r["allocation_id"]]
        home = sections[a.section_id].home_room_id
        if home and rooms.get(r["room_id"]) and r["room_id"] != home and courses[a.course_id].type != "LAB" and not a.batch:
            st_ = next((o for o in block["options"] if o["home_room"]), None)
            warnings.append(f"{block['class']} is not in the section's home room ({rooms[home].name}); "
                            + ("the home room was occupied then." if st_ and st_["status"] == "busy"
                               else "the home room was free then, so this was a choice the solver made."
                               if st_ else "the home room is not suitable for this class."))
    if stale:
        warnings.append("Your data changed after this timetable was generated; this explanation uses the current data.")

    return {
        "session_id": session_id, "task_key": task.key,
        "assignment": {"day": day, "day_name": DAY_NAMES[day], "start": start, "length": task.length, "label": when},
        "members": members,
        "hard": {"all_satisfied": not failed, "constraints": checks},
        "soft": {"total": current_total, "marginal": marginal, "marginal_total": sum(m["cost"] for m in marginal),
                 "note": ("Each line shows how the timetable's soft score changes because this class is in this slot "
                          "(re-scored with and without it). Negative = this placement helps.")
                 if marginal else "This placement adds and removes no soft-preference penalty."},
        "alternatives": {"summary": summary, "slots": slot_list},
        "rooms": room_block, "warnings": warnings, "method": METHOD,
    }
