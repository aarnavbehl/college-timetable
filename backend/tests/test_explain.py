"""'Why this slot?': explanations are derived from real checks and match what the editor and scorer really do."""
import pytest

from app.scheduler.problem import build_problem
from app.scheduler.soft import soft_breakdown
from app.scheduler.solver import solve, state_from_sessions
from app.services.demo import demo_dataset
from app.services.explain import explain

from .conftest import register


@pytest.fixture(scope="module")
def solved():
    ds = demo_dataset()
    p = build_problem(ds)
    sol = solve(p, time_limit=6, seed=9)
    rows = [{**r, "id": n} for n, r in enumerate(sol.sessions(), 1)]
    return ds, p, rows


def first(rows, pred):
    return next(r for r in rows if pred(r))


# ------------------------------------------------------------------ service level
def test_valid_placement_satisfies_every_hard_constraint_with_evidence(solved):
    ds, p, rows = solved
    e = explain(ds, rows, rows[0]["id"])
    assert e["hard"]["all_satisfied"]
    ids = {c["id"] for c in e["hard"]["constraints"]}
    assert {"placement", "students", "faculty_free", "faculty_available", "faculty_daily", "room_type",
            "room_capacity", "room_free"} <= ids
    assert all(c["satisfied"] and c["evidence"] for c in e["hard"]["constraints"])
    assert "does not record" in e["method"]


def test_marginal_soft_effect_equals_re_scoring_without_the_class(solved):
    ds, p, rows = solved
    for r in rows[:25]:
        e = explain(ds, rows, r["id"])
        st = state_from_sessions(p, rows)
        slots = list(st.slot)
        task = p.task_by_key()[r["task_key"]]
        without = list(slots)
        without[task.index] = None
        truth = soft_breakdown(p, slots)["total"]["cost"] - soft_breakdown(p, without)["total"]["cost"]
        assert e["soft"]["marginal_total"] == truth
        assert e["soft"]["total"] == soft_breakdown(p, slots)["total"]["cost"]


def test_alternatives_cover_every_legal_slot_exactly_once(solved):
    ds, p, rows = solved
    r = first(rows, lambda x: x["length"] == 1)
    e = explain(ds, rows, r["id"])
    task = p.task_by_key()[r["task_key"]]
    slots = [(s["day"], s["start"]) for s in e["alternatives"]["slots"]]
    assert sorted(slots) == sorted(task.starts) and len(set(slots)) == len(slots)
    assert sum(1 for s in e["alternatives"]["slots"] if s["current"]) == 1
    s = e["alternatives"]["summary"]
    assert s["legal_slots"] == len(slots) and s["feasible_alternatives"] + s["infeasible_alternatives"] == len(slots) - 1
    assert s["better"] + s["equal"] + s["worse"] == s["feasible_alternatives"]
    for alt in e["alternatives"]["slots"]:
        assert alt["feasible"] or alt["reasons"]                         # every rejection has a real reason


def test_lab_rotation_explanation_lists_partners_and_each_members_room(solved):
    ds, p, rows = solved
    lab = first(rows, lambda x: x["length"] == 3)
    e = explain(ds, rows, lab["id"])
    assert len(e["members"]) == 2 and any(c["id"] == "sync" for c in e["hard"]["constraints"])
    assert sum(1 for c in e["hard"]["constraints"] if c["id"] == "room_free") == 2
    assert all(opt["status"] in ("assigned", "free", "busy") for blk in e["rooms"] for opt in blk["options"])
    assert all(any(o["status"] == "assigned" for o in blk["options"]) for blk in e["rooms"])


def test_violations_are_explained_not_hidden(solved):
    ds, p, rows = solved
    rows2 = [dict(r) for r in rows]
    a, b = [r for r in rows2 if r["length"] == 1][:2]
    b["day"], b["start"], b["room_id"] = a["day"], a["start"], a["room_id"]      # force a room clash
    e = explain(ds, rows2, b["id"])
    assert e["hard"]["all_satisfied"] is False
    bad = [c for c in e["hard"]["constraints"] if not c["satisfied"]]
    assert any(c["id"] == "room_free" and "Also used by" in c["evidence"] for c in bad)
    assert any("NOT satisfied" in w for w in e["warnings"])


def test_explaining_does_not_alter_the_inputs(solved):
    ds, p, rows = solved
    snapshot = [dict(r) for r in rows]
    for r in rows[:5]:
        explain(ds, rows, r["id"])
    assert rows == snapshot


# ------------------------------------------------------------------ API: truthfulness against the real editor
def generate(client, auth):
    r = client.post("/api/timetable/generate", headers=auth, json={"time_limit": 5, "seed": 2})
    assert r.status_code == 200 and r.json()["ok"], r.text


def test_explanation_feasibility_and_reasons_match_the_editor_exactly(client, demo):
    generate(client, demo)
    sessions = client.get("/api/timetable", headers=demo).json()["sessions"]
    for target in [s for s in sessions if s["length"] == 1][:3] + [s for s in sessions if s["length"] == 3][:1]:
        e = client.get(f"/api/timetable/sessions/{target['id']}/explain", headers=demo).json()
        opts = client.get(f"/api/timetable/sessions/{target['id']}/options", headers=demo).json()["options"]
        by_slot = {(o["day"], o["start"]): o for o in opts}
        for s in e["alternatives"]["slots"]:
            o = by_slot[(s["day"], s["start"])]
            assert s["feasible"] == o["ok"] and s["current"] == o["current"]
            assert sorted(s["reasons"]) == sorted(o["reasons"])


def test_predicted_score_change_equals_the_real_change_after_moving(client, demo):
    """The strongest check: do what the explanation says, and the saved soft score changes by exactly that amount."""
    generate(client, demo)
    sessions = client.get("/api/timetable", headers=demo).json()["sessions"]
    checked = 0
    for target in [s for s in sessions if s["length"] == 1 and not s["sync_group"]][:6]:
        e = client.get(f"/api/timetable/sessions/{target['id']}/explain", headers=demo).json()
        alt = next((s for s in e["alternatives"]["slots"] if s["feasible"] and not s["current"]), None)
        if alt is None:
            continue
        before = client.get("/api/timetable", headers=demo).json()["meta"]["soft"]
        r = client.put(f"/api/timetable/sessions/{target['id']}/move", headers=demo, json={"day": alt["day"], "start": alt["start"]})
        assert r.status_code == 200
        after = client.get("/api/timetable", headers=demo).json()["meta"]["soft"]
        assert after - before == alt["delta"], (alt, before, after)
        checked += 1
    assert checked >= 3


def test_forced_clash_is_explained_through_the_api(client, demo):
    generate(client, demo)
    sessions = client.get("/api/timetable", headers=demo).json()["sessions"]
    target = next(s for s in sessions if s["length"] == 1 and not s["sync_group"])
    opts = client.get(f"/api/timetable/sessions/{target['id']}/options", headers=demo).json()["options"]
    busy = next(o for o in opts if not o["ok"])
    client.put(f"/api/timetable/sessions/{target['id']}/move", headers=demo,
               json={"day": busy["day"], "start": busy["start"], "force": True})
    e = client.get(f"/api/timetable/sessions/{target['id']}/explain", headers=demo).json()
    assert e["hard"]["all_satisfied"] is False and any(not c["satisfied"] for c in e["hard"]["constraints"])


def test_explain_is_read_only(client, demo):
    generate(client, demo)
    before = client.get("/api/timetable", headers=demo).json()
    for s in before["sessions"][:8]:
        assert client.get(f"/api/timetable/sessions/{s['id']}/explain", headers=demo).status_code == 200
    assert client.get("/api/timetable", headers=demo).json() == before


def test_explain_errors_and_authorization(client, demo):
    assert client.get("/api/timetable/sessions/1/explain").status_code == 401
    assert client.get("/api/timetable/sessions/999999/explain", headers=demo).status_code == 404
    generate(client, demo)
    sid = client.get("/api/timetable", headers=demo).json()["sessions"][0]["id"]
    other = register(client, email="other@college.test")
    assert client.get(f"/api/timetable/sessions/{sid}/explain", headers=other).status_code == 404    # another college's class
    fac = client.get("/api/faculty", headers=demo).json()[0]
    client.put(f"/api/faculty/{fac['id']}", headers=demo, json={**fac, "max_per_week": 1})           # data now invalid
    r = client.get(f"/api/timetable/sessions/{sid}/explain", headers=demo)
    assert r.status_code == 409 and "valid college data" in r.json()["detail"]
