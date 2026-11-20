import random

import pytest

from app.scheduler.check import check_sessions
from app.scheduler.problem import (AllocRec, Config, CourseRec, Dataset, FacultyRec, ProblemError,
                                   RoomRec, SectionRec, build_problem)
from app.scheduler.soft import soft_cost
from app.scheduler.solver import solve
from app.scheduler.state import State, recompute_hard
from app.services.demo import demo_dataset

from .helpers import big_college


@pytest.fixture(scope="module")
def demo_result():
    ds = demo_dataset()
    p = build_problem(ds)
    sol = solve(p, time_limit=10, seed=7)
    return ds, p, sol, sol.sessions()


# ------------------------------------------------------------------ the solver on the demo college
def test_demo_college_is_solved_without_clashes(demo_result):
    ds, p, sol, rows = demo_result
    assert sol.hard == 0
    assert check_sessions(ds, rows) == []               # independent verifier agrees


def test_every_class_is_scheduled_exactly_as_often_as_allocated(demo_result):
    ds, p, sol, rows = demo_result
    courses = {c.id: c for c in ds.courses}
    for a in ds.allocations:
        hours = a.hours or courses[a.course_id].hours
        scheduled = sum(r["length"] for r in rows if r["allocation_id"] == a.id)
        assert scheduled == hours, (a, scheduled, hours)


def test_lab_blocks_are_three_periods_and_never_span_lunch(demo_result):
    ds, p, sol, rows = demo_result
    labs = [r for r in rows if r["length"] == 3]
    assert labs
    for r in labs:
        assert r["start"] in (1, 2, 5), r              # lunch is after period 4 of 7


def test_sync_groups_run_at_the_same_time_with_different_faculty_and_rooms(demo_result):
    ds, p, sol, rows = demo_result
    fac = {a.id: a.faculty_id for a in ds.allocations}
    by_task = {}
    for r in rows:
        by_task.setdefault(r["task_key"], []).append(r)
    synced = [g for g in by_task.values() if len(g) > 1]
    assert len(synced) >= 11                           # 10 lab rotations + 1 elective slot
    for g in synced:
        assert len({(r["day"], r["start"]) for r in g}) == 1
        assert len({fac[r["allocation_id"]] for r in g}) == len(g)
        assert len({r["room_id"] for r in g}) == len(g)


def test_elective_members_are_in_classrooms_and_labs_in_labs(demo_result):
    ds, p, sol, rows = demo_result
    room = {r.id: r for r in ds.rooms}
    alloc = {a.id: a for a in ds.allocations}
    course = {c.id: c for c in ds.courses}
    for r in rows:
        want = "LAB" if course[alloc[r["allocation_id"]].course_id].type == "LAB" else "CLASSROOM"
        assert room[r["room_id"]].type == want


def test_faculty_unavailability_is_respected(demo_result):
    ds, p, sol, rows = demo_result
    fac = {f.id: f for f in ds.faculty}
    alloc = {a.id: a for a in ds.allocations}
    for r in rows:
        f = fac[alloc[r["allocation_id"]].faculty_id]
        for per in range(r["start"], r["start"] + r["length"]):
            assert (r["day"], per) not in f.unavailable


def test_theory_classes_prefer_the_sections_home_room(demo_result):
    ds, p, sol, rows = demo_result
    alloc = {a.id: a for a in ds.allocations}
    sec = {s.id: s for s in ds.sections}
    course = {c.id: c for c in ds.courses}
    mine = [r for r in rows
            if course[alloc[r["allocation_id"]].course_id].type == "THEORY"
            and not alloc[r["allocation_id"]].sync_group]
    at_home = sum(r["room_id"] == sec[alloc[r["allocation_id"]].section_id].home_room_id for r in mine)
    assert at_home / len(mine) > 0.8


def test_checker_detects_an_injected_clash(demo_result):
    ds, p, sol, rows = demo_result
    rows = [dict(r) for r in rows]
    a, b = [r for r in rows if r["length"] == 1][:2]
    b["day"], b["start"], b["room_id"] = a["day"], a["start"], a["room_id"]   # force a room clash
    assert any("double-booked" in x for x in check_sessions(ds, rows))


# ------------------------------------------------------------------ state invariants
def test_incremental_hard_count_matches_brute_force():
    p = build_problem(demo_dataset())
    rng = random.Random(3)
    st = State(p)
    for i, t in enumerate(p.tasks):                       # random (clashing) placement
        d, s = rng.choice(t.starts)
        _, rooms = st.eval_slot(i, d, s, rng)
        st.place(i, d, s, rooms)
        assert st.hard == recompute_hard(st)
    for _ in range(300):                                  # random moves
        i = rng.randrange(len(p.tasks))
        st.unplace(i)
        assert st.hard == recompute_hard(st)
        d, s = rng.choice(p.tasks[i].starts)
        _, rooms = st.eval_slot(i, d, s, rng)
        st.place(i, d, s, rooms)
        assert st.hard == recompute_hard(st)
    assert st.copy().hard == st.hard


def test_eval_slot_score_contract():
    p = build_problem(demo_dataset())
    rng = random.Random(5)
    st = State(p)
    for i, t in enumerate(p.tasks):
        d, s = rng.choice(t.starts)
        c, rooms = st.eval_slot(i, d, s, rng)
        before = st.hard
        st.place(i, d, s, rooms)
        rise = st.hard - before
        assert (c == 0) == (rise == 0)          # zero score <=> no new violation
        assert rise <= c                        # never under-reports


def test_solver_handles_a_larger_college():
    ds = big_college(branches=2, years=4)                 # 8 sections, ~180 classes
    sol = solve(build_problem(ds), time_limit=12, seed=2)
    assert sol.hard == 0
    assert check_sessions(ds, sol.sessions()) == []


def test_soft_cost_penalises_gaps_and_late_starts():
    ds = Dataset(Config(5, 7, 4), [RoomRec(1, "R", "CLASSROOM", 60)],
                 [SectionRec(1, "S", 1, 1, 30)], [CourseRec(1, "C", "Course", "THEORY", 2, 1)],
                 [FacultyRec(1, "F", "F")], [AllocRec(1, 1, 1, 1)])
    p = build_problem(ds)
    tight = [(0, 1), (1, 1)]            # period 1 on two different days -> no gaps
    gap = [(0, 1), (0, 4)]              # same day, periods 1 and 4 -> 2 gap periods + repeat
    late = [(0, 3), (1, 3)]             # starts at period 3 -> 2 late-start penalties
    assert soft_cost(p, tight) == 0
    assert soft_cost(p, gap) > soft_cost(p, tight)
    assert soft_cost(p, late) > soft_cost(p, tight)


# ------------------------------------------------------------------ validation messages
def _tiny(**over):
    base = dict(
        config=Config(5, 7, 4), rooms=[RoomRec(1, "R1", "CLASSROOM", 70), RoomRec(2, "L1", "LAB", 30)],
        sections=[SectionRec(1, "CSE Y1-A", 1, 1, 60, 2)],
        courses=[CourseRec(1, "T1", "Theory", "THEORY", 4, 1), CourseRec(2, "L1", "Lab", "LAB", 3, 3)],
        faculty=[FacultyRec(1, "Dr A", "A", 5, 20), FacultyRec(2, "Dr B", "B", 5, 20)],
        allocations=[AllocRec(1, 1, 1, 1)])
    base.update(over)
    return Dataset(**base)


def errors_of(ds):
    with pytest.raises(ProblemError) as e:
        build_problem(ds)
    return " | ".join(e.value.errors)


def test_valid_tiny_dataset_builds():
    assert len(build_problem(_tiny()).tasks) == 4


def test_no_allocations():
    assert "no course allocations" in errors_of(_tiny(allocations=[]))


def test_hours_must_fit_session_length():
    ds = _tiny(courses=[CourseRec(1, "T1", "Theory", "THEORY", 4, 1), CourseRec(2, "L1", "Lab", "LAB", 3, 3)],
               allocations=[AllocRec(1, 1, 2, 1, hours=4)])
    assert "multiple of the session length" in errors_of(ds)


def test_missing_lab_room_and_capacity():
    assert "no lab with capacity" in errors_of(_tiny(rooms=[RoomRec(1, "R1", "CLASSROOM", 70)],
                                                     allocations=[AllocRec(1, 1, 2, 1, batch=1)]))
    assert "no classroom with capacity" in errors_of(_tiny(rooms=[RoomRec(1, "R1", "CLASSROOM", 20),
                                                                  RoomRec(2, "L1", "LAB", 30)]))


def test_faculty_overload_messages():
    ds = _tiny(courses=[CourseRec(1, "T1", "Theory", "THEORY", 25, 1)], allocations=[AllocRec(1, 1, 1, 1)])
    msg = errors_of(ds)
    assert "Dr A is allocated 25 periods a week but the maximum is 20" in msg


def test_section_cannot_exceed_the_week():
    ds = _tiny(courses=[CourseRec(1, "T1", "Theory", "THEORY", 20, 1), CourseRec(2, "T2", "Theory2", "THEORY", 20, 1)],
               faculty=[FacultyRec(1, "Dr A", "A", 7, 30), FacultyRec(2, "Dr B", "B", 7, 30)],
               allocations=[AllocRec(1, 1, 1, 1), AllocRec(2, 1, 2, 2)])
    assert "needs 40 periods a week but the week only has 35" in errors_of(ds)


def test_unfittable_session_length():
    ds = _tiny(courses=[CourseRec(1, "T1", "Theory", "THEORY", 5, 5)])   # 5 periods can't avoid lunch after 4
    assert "does not fit in a day" in errors_of(ds)


def test_sync_group_rules():
    # members with different weekly sessions
    ds = _tiny(allocations=[AllocRec(1, 1, 1, 1, sync_group="G"), AllocRec(2, 1, 2, 2, batch=1, sync_group="G")])
    assert "same sessions per week" in errors_of(ds)
    # same faculty twice at the same time
    ds = _tiny(courses=[CourseRec(1, "T1", "A", "THEORY", 3, 1), CourseRec(2, "T2", "B", "THEORY", 3, 1)],
               allocations=[AllocRec(1, 1, 1, 1, sync_group="G"), AllocRec(2, 1, 2, 1, sync_group="G")])
    assert "same faculty member is used twice" in errors_of(ds)


def test_bad_batch_number():
    assert "batch 3 does not exist" in errors_of(_tiny(allocations=[AllocRec(1, 1, 2, 1, batch=3)]))


def test_room_supply_check():
    # 1 lab, 2 batches x 3 periods x 12 sections would need far more than 35 lab-periods
    secs = [SectionRec(i, f"S{i}", 1, 1, 60, 2) for i in range(1, 14)]
    allocs = [AllocRec(i * 2 + b, i, 2, 1 + (i % 2), batch=b) for i in range(1, 14) for b in (1, 2)]
    fac = [FacultyRec(1, "A", "A", 12, 84), FacultyRec(2, "B", "B", 12, 84)]
    assert "Labs:" in errors_of(_tiny(sections=secs, allocations=allocs, faculty=fac))


def test_tiny_dataset_solves():
    ds = _tiny(allocations=[AllocRec(1, 1, 1, 1), AllocRec(2, 1, 2, 2, batch=1), AllocRec(3, 1, 2, 2, batch=2)])
    sol = solve(build_problem(ds), time_limit=3, seed=1)
    assert sol.hard == 0 and check_sessions(ds, sol.sessions()) == []
