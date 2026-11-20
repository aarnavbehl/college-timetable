"""What-if simulation: isolation from the saved baseline, independent verification, comparison, safe apply."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.database import ensure_schema
from app.main import app
from app.scheduler.adjust import Pins, adjust
from app.scheduler.check import check_sessions
from app.scheduler.problem import build_problem
from app.scheduler.solver import solve
from app.services import scenarios as svc
from app.services.demo import demo_dataset

from .conftest import register


# ------------------------------------------------------------------ helpers
def generate(client, auth, seconds=3):
    r = client.post("/api/timetable/generate", headers=auth, json={"time_limit": seconds, "seed": 1})
    assert r.status_code == 200 and r.json()["ok"], r.text


def by_code(client, auth, kind="faculty", field="code"):
    return {x[field]: x for x in client.get(f"/api/{kind}", headers=auth).json()}


def snapshot(client, auth):
    """Everything a simulation must NOT change."""
    return {"timetable": client.get("/api/timetable", headers=auth).json(),
            **{k: client.get(f"/api/{k}", headers=auth).json() for k in ("branches", "rooms", "sections", "faculty", "courses", "allocations")}}


def make(client, auth, changes, name="Scenario", **kw):
    r = client.post("/api/scenarios", headers=auth, json={"name": name, "changes": changes, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def run(client, auth, sid):
    r = client.post(f"/api/scenarios/{sid}/run", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def monday_off(fid):
    return {"type": "faculty_availability", "faculty_id": fid, "add_unavailable": [[0, p] for p in range(1, 8)]}


@pytest.fixture()
def ready(client, demo):
    generate(client, demo)
    return demo


# ------------------------------------------------------------------ schema migration + closed rooms
def test_migration_adds_the_missing_column_once_and_keeps_existing_rows():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE rooms (id INTEGER PRIMARY KEY, owner_id INTEGER, name VARCHAR(60), room_type VARCHAR(10), capacity INTEGER)"))
        c.execute(text("INSERT INTO rooms (owner_id, name, room_type, capacity) VALUES (1, 'Old Room', 'CLASSROOM', 50)"))
    assert ensure_schema(eng) == ["rooms.is_active"]
    assert "is_active" in {c["name"] for c in inspect(eng).get_columns("rooms")}
    with eng.connect() as c:
        assert c.execute(text("SELECT is_active FROM rooms")).scalar() in (1, True)        # existing rooms stay open
    assert ensure_schema(eng) == []                                                         # idempotent
    assert {"scenarios", "change_log"} <= set(inspect(eng).get_table_names())              # new tables were created too


def test_closed_rooms_are_not_scheduled(client, demo):
    room = by_code(client, demo, "rooms", "name")["C202"]
    client.put(f"/api/rooms/{room['id']}", headers=demo, json={**room, "is_active": False})
    assert client.get("/api/validate", headers=demo).json()["ok"]
    generate(client, demo)
    assert all(s["room"] != "C202" for s in client.get("/api/timetable", headers=demo).json()["sessions"])
    assert client.get("/api/timetable/check", headers=demo).json()["ok"]


# ------------------------------------------------------------------ solver: warm start and pins
@pytest.fixture(scope="module")
def base():
    ds = demo_dataset()
    p = build_problem(ds)
    return ds, p, [{**r, "id": n} for n, r in enumerate(solve(p, time_limit=5, seed=1).sessions(), 1)]


def test_adjust_with_no_changes_keeps_every_class_where_it_was(base):
    ds, p, rows = base
    sol, notes = adjust(p, rows, time_limit=3, seed=1)
    assert sol.hard == 0 and "started from their saved placement" in notes[0]
    moved = {(r["task_key"], r["allocation_id"]) for r in sol.sessions()} - {(r["task_key"], r["allocation_id"]) for r in rows}
    assert not moved
    old = {(r["task_key"], r["allocation_id"]): (r["day"], r["start"], r["room_id"]) for r in rows}
    assert all(old[(r["task_key"], r["allocation_id"])] == (r["day"], r["start"], r["room_id"]) for r in sol.sessions())


def test_adjust_pinned_classes_are_never_moved_even_when_that_leaves_a_clash(base):
    ds, p, rows = base
    # pin two classes of the same faculty member onto the same slot: impossible, and repair must not "fix" it by moving a pin
    fac = {a.id: a.faculty_id for a in ds.allocations}
    a = rows[0]
    b = next(r for r in rows if fac[r["allocation_id"]] == fac[a["allocation_id"]] and r["task_key"] != a["task_key"] and r["length"] == a["length"] == 1)
    pins = Pins(times={a["task_key"]: (a["day"], a["start"]), b["task_key"]: (a["day"], a["start"])})
    sol, _ = adjust(p, rows, pins=pins, time_limit=3, seed=1)
    placed = {r["task_key"]: (r["day"], r["start"]) for r in sol.sessions()}
    assert placed[a["task_key"]] == placed[b["task_key"]] == (a["day"], a["start"])         # pins honoured exactly
    assert sol.hard > 0 and check_sessions(ds, sol.sessions())                              # and the clash is reported, not hidden


# ------------------------------------------------------------------ validation of the change list
def test_every_kind_of_bad_change_is_rejected_with_a_specific_message(client, ready):
    fac = by_code(client, ready)
    sec = client.get("/api/sections", headers=ready).json()[0]
    bad = [
        {"type": "faculty_availability", "faculty_id": 99999, "add_unavailable": [[0, 1]]},
        {"type": "faculty_availability", "faculty_id": fac["AS"]["id"], "add_unavailable": [[9, 1]]},
        {"type": "faculty_availability", "faculty_id": fac["AS"]["id"]},
        {"type": "faculty_limits", "faculty_id": fac["AS"]["id"], "max_per_day": 0},
        {"type": "room_closed", "room_id": 99999},
        {"type": "soft_weights", "section_gap": 99},
        {"type": "add_section", "ref": "bad ref", "branch_id": 1, "year": 9, "name": "X", "strength": 0},
        {"type": "add_allocation", "section_id": 99999, "course_id": 99999, "faculty_id": 99999},
        {"type": "add_allocation", "section_id": sec["id"], "course_id": "new:ghost", "faculty_id": fac["AS"]["id"]},
        {"type": "pin_class", "session_id": 99999, "day": 0, "start": 1},
        {"type": "teleport"},
        {"no_type": True},
    ]
    r = client.post("/api/scenarios", headers=ready, json={"name": "bad", "changes": bad})
    assert r.status_code == 422
    msg = " | ".join(r.json()["detail"]["errors"])
    for expected in ["faculty_id 99999 does not exist", "[9, 1] is outside the 5-day, 7-period week", "choose slots",
                     "max_per_day must be a whole number from 1 to 12", "room_id 99999 does not exist or is already closed",
                     "section_gap must be a whole number from 0 to 20", "ref must be unique text", "section_id 99999 is not an existing section",
                     "course_id 'new:ghost' is not an existing course", "session_id 99999 is not a class", "unknown change type 'teleport'",
                     "missing 'type'"]:
        assert expected in msg, expected
    assert client.get("/api/scenarios", headers=ready).json() == []                             # nothing was saved
    # entries that are not even objects are rejected by request validation before reaching the checks above
    assert client.post("/api/scenarios", headers=ready, json={"name": "x", "changes": ["not an object"]}).status_code == 422


def test_pins_require_the_adjust_strategy_and_other_rules(client, ready):
    sess = client.get("/api/timetable", headers=ready).json()["sessions"][0]
    pin = {"type": "pin_class", "session_id": sess["id"], "day": 0, "start": 1}
    r = client.post("/api/scenarios", headers=ready, json={"name": "x", "strategy": "rebuild", "changes": [pin]})
    assert r.status_code == 422 and "need the 'adjust' strategy" in " ".join(r.json()["detail"]["errors"])
    assert client.post("/api/scenarios", headers=ready, json={"name": "x", "changes": []}).status_code == 422
    two = client.post("/api/scenarios", headers=ready, json={"name": "x", "changes": [pin, pin]})
    assert two.status_code == 422 and "already pinned" in " ".join(two.json()["detail"]["errors"])
    both = client.post("/api/scenarios", headers=ready, json={"name": "x", "changes": [{**pin, "start": None}]})
    assert both.status_code == 422 and "both day and start" in " ".join(both.json()["detail"]["errors"])


# ------------------------------------------------------------------ isolation + verification + comparison
def test_running_a_simulation_changes_nothing_that_is_saved(client, ready):
    fac = by_code(client, ready)
    before = snapshot(client, ready)
    sc = make(client, ready, [monday_off(fac["NB"]["id"])])
    after_create = snapshot(client, ready)
    res = run(client, ready, sc["id"])
    assert res["status"] == "ready" and res["result"]["candidate_valid"]
    assert snapshot(client, ready) == before == after_create                                    # byte-for-byte identical
    assert client.get("/api/analytics", headers=ready).json()["feasible"]
    assert client.get("/api/timetable/check", headers=ready).json()["ok"]
    assert client.get(f"/api/scenarios/{sc['id']}", headers=ready).json()["outdated"] is False


def test_candidate_is_independently_verified_and_the_comparison_is_truthful(client, ready):
    fac = by_code(client, ready)
    nb = fac["NB"]
    base_sessions = client.get("/api/timetable", headers=ready).json()["sessions"]
    nb_monday = [s for s in base_sessions if s["faculty_id"] == nb["id"] and s["day"] == 0]
    assert nb_monday, "test needs NB to teach on Monday"
    res = run(client, ready, make(client, ready, [monday_off(nb["id"])])["id"])["result"]
    v = res["verification"]
    assert v["violations"] == 0 and v["solver_reported_hard"] == 0 and "Independent verifier" in v["checker"]
    pc = res["placement_changes"]["summary"]
    assert pc["moved"] >= len({s["task_key"] for s in nb_monday}) and pc["moved"] < 20           # only the affected classes move
    assert pc["unchanged"] + pc["moved"] + pc["room_changed"] == 114 and pc["added"] == pc["removed"] == 0
    for item in res["placement_changes"]["items"]:                                              # nobody moved INTO NB's blocked Monday
        if item["change"] == "moved" and item["to"]["faculty"] == nb["name"]:
            assert item["to"]["day"] != 0
    cmp_ = {r["key"]: r for r in res["comparison"]}
    assert cmp_["hard_violations"]["baseline"] == cmp_["hard_violations"]["candidate"] == 0
    assert cmp_["scheduled_classes"]["candidate"] == 114
    assert res["strategy_used"] == "adjust" and res["data_changes"] is True
    assert {r["verdict"] for r in res["comparison"]} <= {"better", "worse", "same", "info"}


def test_infeasible_scenarios_explain_why_and_cannot_be_applied(client, ready):
    fac = by_code(client, ready)
    everything = {"type": "faculty_availability", "faculty_id": fac["AS"]["id"],
                  "add_unavailable": [[d, p] for d in range(5) for p in range(1, 8)]}
    sc = make(client, ready, [everything])
    out = run(client, ready, sc["id"])
    assert out["status"] == "infeasible" and out["result"]["candidate"] is None
    assert any("available for only 0" in d for d in out["result"]["diagnostics"])
    r = client.post(f"/api/scenarios/{sc['id']}/apply", headers=ready, json={"confirm": True})
    assert r.status_code == 409 and "verified candidate" in r.json()["detail"]["message"]
    # closing every lab: nothing can host the lab classes
    labs = [r_["id"] for r_ in client.get("/api/rooms", headers=ready).json() if r_["room_type"] == "LAB"]
    out2 = run(client, ready, make(client, ready, [{"type": "room_closed", "room_id": i} for i in labs])["id"])
    assert out2["status"] == "infeasible" and any("no lab with capacity" in d for d in out2["result"]["diagnostics"])


def test_an_unresolvable_candidate_is_reported_invalid_not_successful(client, ready):
    sessions = client.get("/api/timetable", headers=ready).json()["sessions"]
    a = sessions[0]
    b = next(s for s in sessions if s["faculty_id"] == a["faculty_id"] and s["task_key"] != a["task_key"] and s["length"] == a["length"] == 1)
    pins = [{"type": "pin_class", "session_id": a["id"], "day": a["day"], "start": a["start"]},
            {"type": "pin_class", "session_id": b["id"], "day": a["day"], "start": a["start"]}]    # same teacher, same slot
    sc = make(client, ready, pins)
    out = run(client, ready, sc["id"])
    assert out["status"] == "invalid" and out["result"]["candidate_valid"] is False
    assert out["result"]["verification"]["violations"] > 0 and out["result"]["diagnostics"]
    assert out["result"]["verification"]["by_kind"].get("faculty_conflict")
    assert client.post(f"/api/scenarios/{sc['id']}/apply", headers=ready, json={"confirm": True}).status_code == 409


def test_pinning_a_time_and_a_room_is_honoured(client, ready):
    sessions = client.get("/api/timetable", headers=ready).json()["sessions"]
    target = next(s for s in sessions if s["length"] == 1 and not s["sync_group"])
    opts = client.get(f"/api/timetable/sessions/{target['id']}/options", headers=ready).json()["options"]
    free = next(o for o in opts if o["ok"] and not o["current"])
    res = run(client, ready, make(client, ready, [{"type": "pin_class", "session_id": target["id"], "day": free["day"], "start": free["start"]}])["id"])
    assert res["status"] == "ready"
    moved = [i for i in res["result"]["placement_changes"]["items"] if i["change"] == "moved" and i["to"]["class"].startswith(target["course_code"])]
    assert any((i["to"]["day"], i["to"]["start"]) == (free["day"], free["start"]) for i in moved)


def test_a_lab_pinned_across_lunch_is_refused_with_a_reason(client, ready):
    lab = next(s for s in client.get("/api/timetable", headers=ready).json()["sessions"] if s["length"] == 3)
    out = run(client, ready, make(client, ready, [{"type": "pin_class", "session_id": lab["id"], "day": 0, "start": 4}])["id"])
    assert out["status"] == "infeasible" and "across the lunch break" in " ".join(out["result"]["diagnostics"])


# ------------------------------------------------------------------ other scenario types
def test_soft_preference_scenario_changes_scoring_not_validity(client, ready):
    before = snapshot(client, ready)
    sc = make(client, ready, [{"type": "soft_weights", "section_gap": 20, "late_start": 0, "faculty_gap": 0, "same_day_repeat": 0,
                               "long_run": 0, "unbalanced": 0}], time_limit=5)
    res = run(client, ready, sc["id"])["result"]
    sw = res["soft_under_scenario_weights"]
    assert res["candidate_valid"] and sw["weights"]["section_gap"] == 20 and sw["candidate"] <= sw["baseline"]
    assert res["data_changes"] is False and res["verification"]["violations"] == 0
    assert snapshot(client, ready) == before


def test_new_section_and_new_course_scenario_applies_with_correct_ids(client, ready):
    fac, courses, branches = by_code(client, ready), by_code(client, ready, "courses"), by_code(client, ready, "branches")
    sections = {s["label"]: s for s in client.get("/api/sections", headers=ready).json()}
    changes = [
        {"type": "add_section", "ref": "new:c", "branch_id": branches["CSE"]["id"], "year": 2, "name": "C", "strength": 55, "batches": 1},
        {"type": "add_allocation", "section_id": "new:c", "course_id": courses["CS201"]["id"], "faculty_id": fac["AS"]["id"]},
        {"type": "add_allocation", "section_id": "new:c", "course_id": courses["CS202"]["id"], "faculty_id": fac["RV"]["id"]},
        {"type": "add_course", "ref": "new:x", "code": "NEW9", "name": "Seminar", "course_type": "THEORY", "hours_per_week": 2},
        {"type": "add_allocation", "section_id": "new:c", "course_id": "new:x", "faculty_id": fac["MT"]["id"]},
        {"type": "add_allocation", "section_id": sections["CSE Y2-A"]["id"], "course_id": "new:x", "faculty_id": fac["MT"]["id"], "hours_per_week": 1},
    ]
    sc = make(client, ready, changes)
    out = run(client, ready, sc["id"])
    res = out["result"]
    assert out["status"] == "ready" and res["placement_changes"]["summary"]["added"] == 4 + 4 + 2 + 1
    assert res["comparison"][2]["candidate"] == 114 + 11                                        # scheduled classes
    assert client.get("/api/sections", headers=ready).json().__len__() == 5                    # nothing created yet
    r = client.post(f"/api/scenarios/{sc['id']}/apply", headers=ready, json={"confirm": True})
    assert r.status_code == 200, r.text
    assert len(client.get("/api/sections", headers=ready).json()) == 6
    allocs = client.get("/api/allocations", headers=ready).json()
    assert len(allocs) == 46 + 4
    new_sec = next(s for s in client.get("/api/sections", headers=ready).json() if s["label"] == "CSE Y2-C")
    assert sum(1 for a in allocs if a["section_id"] == new_sec["id"]) == 3
    tt = client.get("/api/timetable", headers=ready).json()
    assert tt["meta"]["stale"] is False and tt["meta"]["hard"] == 0 and len(tt["sessions"]) == 125
    seminar = [s for s in tt["sessions"] if s["course_code"] == "NEW9"]
    assert len(seminar) == 3 and {s["section"] for s in seminar} == {"CSE Y2-C", "CSE Y2-A"}  # temp ids were mapped to real rows
    assert client.get("/api/timetable/check", headers=ready).json() == {"ok": True, "problems": [], "sessions": 125}
    a = client.get("/api/analytics", headers=ready).json()
    assert a["feasible"] and a["summary"]["required_classes"] == 125 and a["summary"]["unscheduled_classes"] == 0
    assert client.get("/api/validate", headers=ready).json()["ok"]


def test_rebuild_strategy_runs_the_full_scheduler_and_is_verified(client, ready):
    fac = by_code(client, ready)
    before = snapshot(client, ready)
    sc = make(client, ready, [{"type": "faculty_limits", "faculty_id": fac["NB"]["id"], "max_per_day": 4}], strategy="rebuild", time_limit=4)
    res = run(client, ready, sc["id"])["result"]
    assert res["strategy_used"] == "rebuild" and res["verification"]["violations"] == 0 == res["verification"]["solver_reported_hard"]
    assert snapshot(client, ready) == before


# ------------------------------------------------------------------ applying safely
def test_apply_replaces_the_timetable_and_persists_data_changes(client, ready):
    fac = by_code(client, ready)
    dj = fac["DJ"]                                                                              # visiting faculty, blocked Fri 5-7
    sc = make(client, ready, [{"type": "faculty_availability", "faculty_id": dj["id"], "remove_unavailable": [[4, 5], [4, 6], [4, 7]]},
                              monday_off(fac["NB"]["id"])])
    res = run(client, ready, sc["id"])
    cand_moves = res["result"]["placement_changes"]["summary"]["moved"]
    r = client.post(f"/api/scenarios/{sc['id']}/apply", headers=ready, json={"confirm": True})
    assert r.status_code == 200 and r.json()["applied"] and r.json()["data_changes"]
    after = by_code(client, ready)
    assert after["DJ"]["unavailable"] == [] and len(after["NB"]["unavailable"]) == 7            # data changes are real
    tt = client.get("/api/timetable", headers=ready).json()
    assert tt["meta"]["stale"] is False and not any(s["day"] == 0 and s["faculty_id"] == fac["NB"]["id"] for s in tt["sessions"])
    assert client.get("/api/timetable/check", headers=ready).json()["ok"]
    assert client.get(f"/api/scenarios/{sc['id']}", headers=ready).json()["status"] == "applied"
    assert cand_moves >= 1
    again = client.post(f"/api/scenarios/{sc['id']}/apply", headers=ready, json={"confirm": True})
    assert again.status_code == 409 and "already applied" in again.json()["detail"]["message"]
    assert client.post(f"/api/scenarios/{sc['id']}/run", headers=ready).status_code == 409
    assert client.put(f"/api/scenarios/{sc['id']}", headers=ready, json={"name": "x", "changes": sc["changes"]}).status_code == 409


def test_apply_needs_explicit_confirmation(client, ready):
    sc = make(client, ready, [monday_off(by_code(client, ready)["NB"]["id"])])
    run(client, ready, sc["id"])
    before = snapshot(client, ready)
    for body in ({}, {"confirm": False}):
        assert client.post(f"/api/scenarios/{sc['id']}/apply", headers=ready, json=body).status_code == 422
    assert snapshot(client, ready) == before


def test_apply_is_refused_when_the_baseline_changed_after_the_simulation(client, ready):
    fac = by_code(client, ready)
    sc = make(client, ready, [monday_off(fac["NB"]["id"])])
    run(client, ready, sc["id"])
    sessions = client.get("/api/timetable", headers=ready).json()["sessions"]                   # a manual edit AFTER the run
    t = next(s for s in sessions if s["length"] == 1 and not s["sync_group"])
    free = next(o for o in client.get(f"/api/timetable/sessions/{t['id']}/options", headers=ready).json()["options"] if o["ok"] and not o["current"])
    client.put(f"/api/timetable/sessions/{t['id']}/move", headers=ready, json={"day": free["day"], "start": free["start"]})
    assert client.get(f"/api/scenarios/{sc['id']}", headers=ready).json()["outdated"] is True
    before = snapshot(client, ready)
    r = client.post(f"/api/scenarios/{sc['id']}/apply", headers=ready, json={"confirm": True})
    assert r.status_code == 409 and "changed after this simulation" in r.json()["detail"]["message"]
    assert snapshot(client, ready) == before
    # a data change also makes it outdated
    sc2 = make(client, ready, [monday_off(fac["NB"]["id"])], name="two")
    run(client, ready, sc2["id"])
    client.post("/api/branches", headers=ready, json={"code": "NEWB", "name": "New"})
    assert client.get(f"/api/scenarios/{sc2['id']}", headers=ready).json()["outdated"] is True


def test_a_failure_while_applying_rolls_everything_back(client, ready, monkeypatch):
    fac = by_code(client, ready)
    sc = make(client, ready, [monday_off(fac["NB"]["id"])])
    run(client, ready, sc["id"])
    before = snapshot(client, ready)

    def boom(*a, **k):
        raise RuntimeError("history write failed")

    monkeypatch.setattr(svc.history, "record", boom)                                           # fails AFTER data + sessions were written
    crashing = TestClient(app, raise_server_exceptions=False)
    assert crashing.post(f"/api/scenarios/{sc['id']}/apply", headers=ready, json={"confirm": True}).status_code == 500
    monkeypatch.undo()
    assert snapshot(client, ready) == before                                                    # data AND timetable fully restored
    assert client.get(f"/api/scenarios/{sc['id']}", headers=ready).json()["status"] == "ready"


# ------------------------------------------------------------------ lifecycle, authorization
def test_editing_a_scenario_discards_stale_results_and_delete_leaves_data_alone(client, ready):
    fac = by_code(client, ready)
    sc = make(client, ready, [monday_off(fac["NB"]["id"])], name="first")
    run(client, ready, sc["id"])
    before = snapshot(client, ready)
    upd = client.put(f"/api/scenarios/{sc['id']}", headers=ready, json={"name": "renamed", "changes": [monday_off(fac["AS"]["id"])]}).json()
    assert upd["status"] == "draft" and upd["result"] is None and upd["name"] == "renamed" and upd["run_at"] is None
    assert client.delete(f"/api/scenarios/{sc['id']}", headers=ready).status_code == 200
    assert client.get("/api/scenarios", headers=ready).json() == []
    assert snapshot(client, ready) == before


def test_running_needs_a_saved_baseline(client, demo):
    fac = by_code(client, demo)
    sc = make(client, demo, [monday_off(fac["NB"]["id"])])
    r = client.post(f"/api/scenarios/{sc['id']}/run", headers=demo)
    assert r.status_code == 409 and "no saved timetable" in r.json()["detail"]["message"]


def test_scenarios_are_private_to_their_college(client, ready):
    fac = by_code(client, ready)
    sc = make(client, ready, [monday_off(fac["NB"]["id"])])
    run(client, ready, sc["id"])
    other = register(client, email="other@college.test")
    sid = sc["id"]
    assert client.get("/api/scenarios", headers=other).json() == []
    for method, path, body in [("get", f"/{sid}", None), ("delete", f"/{sid}", None), ("post", f"/{sid}/run", {}),
                               ("post", f"/{sid}/apply", {"confirm": True}),
                               ("put", f"/{sid}", {"name": "x", "changes": [monday_off(fac["NB"]["id"])]})]:
        r = getattr(client, method)(f"/api/scenarios{path}", headers=other, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, path)
    # and cannot smuggle in another college's records
    r = client.post("/api/scenarios", headers=other, json={"name": "x", "changes": [monday_off(fac["NB"]["id"])]})
    assert r.status_code == 422 and "does not exist" in " ".join(r.json()["detail"]["errors"])
    assert client.get("/api/scenarios").status_code == 401
    assert client.post(f"/api/scenarios/{sid}/apply", json={"confirm": True}).status_code == 401
    assert client.get(f"/api/scenarios/{sid}", headers=ready).json()["status"] == "ready"
