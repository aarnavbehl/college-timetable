"""Analytics: metrics are derived from real data, defined in words, and honest about invalid timetables."""
import pytest
from sqlalchemy import func, select

from app.scheduler.problem import AllocRec, Config, CourseRec, Dataset, FacultyRec, RoomRec, SectionRec, build_problem
from app.scheduler.solver import solve
from app.services.analytics import DEFINITIONS, analyze
from app.services.demo import demo_dataset

from .conftest import register


def tiny(hours=2, rooms=1):
    """1 section, 1 faculty, 1 theory course; 5 days x 7 periods, lunch after 4."""
    return Dataset(Config(5, 7, 4), [RoomRec(i + 1, f"R{i + 1}", "CLASSROOM", 60) for i in range(rooms)],
                   [SectionRec(1, "CSE Y1-A", 1, 1, 30, 1, None, "CSE")],
                   [CourseRec(1, "C1", "Course One", "THEORY", hours, 1)], [FacultyRec(1, "Dr A", "A", 5, 20)],
                   [AllocRec(1, 1, 1, 1)])


def row(i, day, start, length=1, room=1, alloc=1, key=None):
    return {"id": i, "allocation_id": alloc, "task_key": key or f"A{alloc}#{i}", "day": day, "start": start,
            "length": length, "room_id": room}


@pytest.fixture(scope="module")
def solved():
    ds = demo_dataset()
    sol = solve(build_problem(ds), time_limit=6, seed=5)
    rows = [{**r, "id": n} for n, r in enumerate(sol.sessions(), 1)]
    return ds, sol, rows


# ------------------------------------------------------------------ real solver output
def test_demo_timetable_metrics(solved):
    ds, sol, rows = solved
    a = analyze(ds, rows, 50)
    assert a["available"] and a["feasible"]
    s = a["summary"]
    assert (s["required_classes"], s["scheduled_classes"], s["unscheduled_classes"], s["hard_violations"]) == \
        (len(rows), len(rows), 0, 0)
    assert a["hard"]["faculty_conflicts"] == a["hard"]["room_conflicts"] == a["hard"]["section_conflicts"] == 0
    assert 0 <= a["quality"]["score"] <= 100 and a["quality"]["reason"] is None
    assert sum(d["classes"] for d in a["days"]) == len(rows)
    assert sum(d["periods"] for d in a["days"]) == sum(r["length"] for r in rows)
    assert all(d["hours"] == round(d["periods"] * 50 / 60, 2) for d in a["days"])


def test_soft_components_add_up_to_exactly_the_solvers_own_cost(solved):
    """The analytics re-derive the soft units from raw rows; they must equal what the solver minimised."""
    ds, sol, rows = solved
    a = analyze(ds, rows, 50)
    assert a["soft"]["weighted_total"] == sol.soft
    assert a["gaps"]["total"] == next(c["units"] for c in a["soft"]["components"] if c["key"] == "section_gap")


def test_room_utilisation_matches_an_independent_count(solved):
    ds, sol, rows = solved
    cells = {(r["room_id"], r["day"], p) for r in rows for p in range(r["start"], r["start"] + r["length"])}
    a = analyze(ds, rows, 50)
    assert a["rooms"]["used_slots"] == len(cells)
    assert a["rooms"]["available_slots"] == len(ds.rooms) * 5 * 7
    assert a["rooms"]["overall_pct"] == round(100 * len(cells) / (len(ds.rooms) * 35), 1)
    assert sum(x["used"] for x in a["rooms"]["by_room"]) == len(cells)


def test_faculty_workload_and_heatmap_shapes(solved):
    ds, sol, rows = solved
    a = analyze(ds, rows, 50)
    f = a["faculty"]
    assert sum(x["periods"] for x in f["list"]) == sum(r["length"] for r in rows)
    assert sum(h["count"] for h in f["histogram"]) == len(ds.faculty)
    assert f["stats"]["teaching"] == sum(1 for x in f["list"] if x["periods"] > 0)
    h = a["heatmap"]
    assert len(h["rooms_in_use"]) == 5 and all(len(r) == 7 for r in h["rooms_in_use"])
    assert max(max(r) for r in h["rooms_in_use"]) <= len(ds.rooms)
    assert sum(map(sum, h["classes"])) == sum(r["length"] for r in rows)
    assert sum(map(sum, h["conflicts"])) == 0


def test_every_metric_has_a_plain_language_definition_and_hard_soft_label():
    for key in ("required_classes", "scheduled_classes", "unscheduled_classes", "hard_violations", "faculty_conflicts",
                "room_conflicts", "section_conflicts", "room_utilization", "faculty_workload", "by_day", "gaps",
                "quality_score", "section_gap", "late_start", "faculty_gap", "same_day_repeat", "long_run", "unbalanced"):
        d = DEFINITIONS[key]
        assert d["kind"] in ("hard", "soft", "info") and len(d["text"]) > 30, key
    assert DEFINITIONS["faculty_conflicts"]["kind"] == "hard" and DEFINITIONS["gaps"]["kind"] == "soft"


# ------------------------------------------------------------------ hand-computed expectations
def test_hand_built_timetable_gives_exactly_the_expected_numbers():
    ds = tiny(hours=2)
    # same section & course twice on Monday, periods 1 and 3 (a one-period gap) in room 1
    a = analyze(ds, [row(1, 0, 1), row(2, 0, 3)], 60)
    assert a["feasible"] and a["summary"]["required_classes"] == 2
    comp = {c["key"]: c["units"] for c in a["soft"]["components"]}
    assert comp == {"section_gap": 1, "late_start": 0, "faculty_gap": 1, "same_day_repeat": 1, "long_run": 0, "unbalanced": 0}
    assert a["gaps"]["total"] == 1 and a["gaps"]["by_day"][0] == {"name": "Monday", "gaps": 1}
    assert a["gaps"]["by_section"] == [{"section": "CSE Y1-A", "gaps": 1, "late_start": 0}]
    assert a["rooms"]["overall_pct"] == round(100 * 2 / 35, 1)
    assert a["days"][0]["classes"] == 2 and a["days"][0]["hours"] == 2.0 and a["days"][1]["classes"] == 0
    q = {c["key"]: c["score"] for c in a["quality"]["components"]}
    assert q["compactness"] == round(100 * (1 - 1 / (1 + 2)), 1)          # 1 gap among 2 class periods
    assert q["subject_spread"] == 50.0                                     # 100 * (1 - 1 repeat / 2 single-period classes)
    assert q["workload_balance"] == 100.0                                  # one teaching faculty member: CV 0


def test_late_start_is_counted():
    a = analyze(tiny(hours=1), [row(1, 2, 3)], 50)
    assert next(c["units"] for c in a["soft"]["components"] if c["key"] == "late_start") == 2


def test_unscheduled_classes_are_reported_and_block_the_score():
    ds = tiny(hours=4)
    a = analyze(ds, [row(1, 0, 1), row(2, 1, 1)], 50)                       # only 2 of 4 sessions exist
    assert a["summary"]["required_classes"] == 4 and a["summary"]["unscheduled_classes"] == 2
    assert a["feasible"] is False and a["quality"]["score"] is None and "No score" in a["quality"]["reason"]
    assert a["hard"]["unscheduled"] == [{"section": "CSE Y1-A", "course": "C1", "course_name": "Course One",
                                         "faculty": "Dr A", "batch": None, "required": 4, "scheduled": 2}]
    assert a["quality"]["components"]                                      # components stay visible for diagnosis


def test_hard_violations_are_counted_by_kind_and_remove_the_score():
    ds = tiny(hours=2, rooms=1)
    a = analyze(ds, [row(1, 0, 1), row(2, 0, 1)], 50)                      # same section, faculty AND room in one period
    assert a["hard"]["section_conflicts"] == 1 and a["hard"]["faculty_conflicts"] == 1 and a["hard"]["room_conflicts"] == 1
    assert a["summary"]["hard_violations"] == 3 and a["feasible"] is False and a["quality"]["score"] is None
    assert a["heatmap"]["conflicts"][0][0] == 3 and len(a["hard"]["samples"]) == 3


def test_orphan_rows_and_empty_input_are_handled():
    ds = tiny(hours=2)
    ghost = row(9, 0, 1, alloc=999)
    assert analyze(ds, [ghost], 50)["available"] is False                  # nothing valid to analyse
    a = analyze(ds, [row(1, 0, 1), row(2, 1, 1), ghost], 50)
    assert a["summary"]["hard_violations"] == 1 and a["hard"]["other"] == {"orphan": 1} and not a["feasible"]
    assert analyze(ds, [], 50)["available"] is False


def test_extra_classes_and_illegal_placements_do_not_crash():
    a = analyze(tiny(hours=1), [row(1, 0, 1), row(2, 1, 1), row(3, 9, 1), row(4, 0, 4, length=2, key="X")], 50)
    assert a["summary"]["extra_classes"] == 3 and a["summary"]["hard_violations"] >= 2       # day 9 + lunch-spanning block
    assert a["available"]


# ------------------------------------------------------------------ API
def generate(client, auth, seconds=4):
    r = client.post("/api/timetable/generate", headers=auth, json={"time_limit": seconds, "seed": 4})
    assert r.status_code == 200 and r.json()["ok"], r.text


def test_api_requires_login_and_reports_no_timetable_yet(client, auth):
    assert client.get("/api/analytics").status_code == 401
    r = client.get("/api/analytics", headers=auth).json()
    assert r["available"] is False and "Generate" in r["reason"]


def test_api_analytics_after_generation_and_after_a_forced_clash(client, demo):
    generate(client, demo)
    a = client.get("/api/analytics", headers=demo).json()
    assert a["available"] and a["feasible"] and a["summary"]["hard_violations"] == 0
    assert a["timetable"]["stale"] is False and a["timetable"]["solver_reported_hard"] == 0
    # force a clash through the editor, then analytics must notice (it re-verifies on every request)
    sessions = client.get("/api/timetable", headers=demo).json()["sessions"]
    target = next(s for s in sessions if s["length"] == 1 and not s["sync_group"])
    opts = client.get(f"/api/timetable/sessions/{target['id']}/options", headers=demo).json()["options"]
    busy = next(o for o in opts if not o["ok"])
    client.put(f"/api/timetable/sessions/{target['id']}/move", headers=demo,
               json={"day": busy["day"], "start": busy["start"], "force": True})
    b = client.get("/api/analytics", headers=demo).json()
    assert b["summary"]["hard_violations"] > 0 and b["quality"]["score"] is None and not b["feasible"]
    assert b["hard"]["samples"]


def test_api_marks_stale_data_and_reports_new_allocations_as_unscheduled(client, demo):
    generate(client, demo)
    secs = client.get("/api/sections", headers=demo).json()
    fac = client.get("/api/faculty", headers=demo).json()
    sec = next(s for s in secs if s["label"] == "CSE Y2-A")
    new_course = client.post("/api/courses", headers=demo, json={"code": "NEW1", "name": "New", "hours_per_week": 2}).json()
    client.post("/api/allocations", headers=demo, json={"section_id": sec["id"], "course_id": new_course["id"],
                                                         "faculty_id": fac[0]["id"]})
    a = client.get("/api/analytics", headers=demo).json()
    assert a["timetable"]["stale"] is True and a["summary"]["unscheduled_classes"] == 2
    assert a["quality"]["score"] is None and a["hard"]["unscheduled"][0]["course"] == "NEW1"


def test_api_is_isolated_per_college_and_read_only(client, demo):
    generate(client, demo)
    other = register(client, email="other@college.test")
    assert client.get("/api/analytics", headers=other).json()["available"] is False
    before = client.get("/api/timetable", headers=demo).json()
    client.get("/api/analytics", headers=demo)
    assert client.get("/api/timetable", headers=demo).json() == before        # analytics changed nothing
