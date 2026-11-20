"""Change history and undo: real timestamps, exact before/after values, safe and re-validated undo."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import get_db
from app.main import app
from app.services import history

from .conftest import register
from .test_scenarios import by_code, generate, make, monday_off, run, snapshot


def placements(client, auth):
    """Order-independent placements + (hard, soft) meta: session ids change when a timetable is restored."""
    t = client.get("/api/timetable", headers=auth).json()
    return (sorted((s["task_key"], s["allocation_id"], s["day"], s["start"], s["length"], s["room_id"]) for s in t["sessions"]),
            None if t["meta"] is None else (t["meta"]["hard"], t["meta"]["soft"]))


def entries(client, auth, **q):
    return client.get("/api/history", headers=auth, params=q).json()["entries"]


def movable(client, auth):
    sessions = client.get("/api/timetable", headers=auth).json()["sessions"]
    t = next(s for s in sessions if s["length"] == 1 and not s["sync_group"])
    opts = client.get(f"/api/timetable/sessions/{t['id']}/options", headers=auth).json()["options"]
    return t, [o for o in opts if o["ok"] and not o["current"]], [o for o in opts if not o["ok"]]


def move(client, auth, sid, o, force=False):
    r = client.put(f"/api/timetable/sessions/{sid}/move", headers=auth, json={"day": o["day"], "start": o["start"], "force": force})
    assert r.status_code == 200, r.text


def undo(client, auth, eid, expect=200):
    r = client.post(f"/api/history/{eid}/undo", headers=auth, json={"confirm": True})
    assert r.status_code == expect, r.text
    return r


@pytest.fixture()
def ready(client, demo):
    generate(client, demo)
    return demo


# ------------------------------------------------------------------ recording
def test_generation_is_recorded_with_a_real_timestamp_and_the_account(client, demo):
    assert entries(client, demo) == []
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    generate(client, demo)
    e = entries(client, demo)
    assert len(e) == 1 and e[0]["kind"] == "generated" and "114 scheduled classes" in e[0]["summary"]
    assert e[0]["created_at"].endswith("Z")                       # explicit UTC, so a browser shows the right local time
    when = datetime.fromisoformat(e[0]["created_at"].replace("Z", "+00:00"))
    assert t0 <= when <= datetime.now(timezone.utc)
    assert e[0]["actor"]["email"] == "admin@college.test" and e[0]["actor"]["college_name"] == "Test College"
    assert "one login per college" in client.get("/api/history", headers=demo).json()["actor_note"]
    assert e[0]["details"]["classes"] == 114 and "before_rows" not in e[0]["details"]          # snapshots are not leaked to the list


def test_a_manual_move_records_exact_before_and_after_values(client, ready):
    t, free, _ = movable(client, ready)
    before = {"day": t["day"], "start": t["start"], "room": t["room"]}
    move(client, ready, t["id"], free[0])
    after = next(s for s in client.get("/api/timetable", headers=ready).json()["sessions"] if s["id"] == t["id"])
    e = entries(client, ready)[0]
    assert e["kind"] == "manual_edit" and e["can_undo"] and e["details"]["forced"] is False
    ch = e["details"]["changes"][0]
    assert ch["class"].startswith(t["course_code"])
    day = lambda d: ["Mon", "Tue", "Wed", "Thu", "Fri"][d]                                      # noqa: E731
    assert ch["from"] == f"{day(before['day'])} P{before['start']}, {before['room']}"
    assert ch["to"] == f"{day(after['day'])} P{after['start']}, {after['room']}"
    assert f"from {day(before['day'])} P{before['start']} to {day(after['day'])} P{after['start']}" in e["summary"]


def test_forced_moves_are_marked_and_reads_record_nothing(client, ready):
    t, _, busy = movable(client, ready)
    n = len(entries(client, ready))
    for url in ("/api/analytics", f"/api/timetable/sessions/{t['id']}/explain", f"/api/timetable/sessions/{t['id']}/options",
                "/api/timetable", "/api/timetable/check", "/api/validate"):
        client.get(url, headers=ready)
    run(client, ready, make(client, ready, [monday_off(by_code(client, ready)["NB"]["id"])])["id"])     # a simulation run
    assert len(entries(client, ready)) == n                                                      # nothing recorded for reads / simulations
    move(client, ready, t["id"], busy[0], force=True)
    e = entries(client, ready)[0]
    assert e["details"]["forced"] is True and "forced" in e["summary"] and e["details"]["hard_after"] > 0


def test_clearing_is_recorded_only_when_there_was_something_to_clear(client, demo):
    client.delete("/api/timetable", headers=demo)
    assert entries(client, demo) == []
    generate(client, demo)
    client.delete("/api/timetable", headers=demo)
    e = entries(client, demo)
    assert [x["kind"] for x in e] == ["cleared", "generated"] and "114" in e[0]["summary"]


# ------------------------------------------------------------------ undo: manual edits
def test_undoing_a_move_restores_the_exact_previous_timetable(client, ready):
    original = placements(client, ready)
    t, free, _ = movable(client, ready)
    move(client, ready, t["id"], free[0])
    assert placements(client, ready) != original
    move_entry = entries(client, ready)[0]
    res = undo(client, ready, move_entry["id"]).json()
    assert res["violations_after"] == 0
    assert placements(client, ready) == original                                                # placements, rooms AND meta restored
    assert client.get("/api/timetable/check", headers=ready).json()["ok"]
    after = entries(client, ready)
    assert [e["kind"] for e in after[:2]] == ["undo", "manual_edit"] and after[1]["undone"] is True
    assert after[0]["summary"].startswith("Undid: Moved") and after[0]["can_undo"] is False
    assert "Already undone" in undo(client, ready, move_entry["id"], 409).json()["detail"]["message"]
    assert "cannot be undone" in undo(client, ready, after[0]["id"], 409).json()["detail"]["message"]


def test_undoing_a_forced_clash_makes_the_timetable_valid_again(client, ready):
    t, _, busy = movable(client, ready)
    move(client, ready, t["id"], busy[0], force=True)
    assert client.get("/api/analytics", headers=ready).json()["feasible"] is False
    undo(client, ready, entries(client, ready)[0]["id"])
    a = client.get("/api/analytics", headers=ready).json()
    assert a["feasible"] and a["summary"]["hard_violations"] == 0
    assert client.get("/api/timetable", headers=ready).json()["meta"]["hard"] == 0


def test_later_edits_to_the_same_class_must_be_undone_first(client, ready):
    original = placements(client, ready)
    t, free, _ = movable(client, ready)
    move(client, ready, t["id"], free[0])
    first = entries(client, ready)[0]
    opts = client.get(f"/api/timetable/sessions/{t['id']}/options", headers=ready).json()["options"]
    move(client, ready, t["id"], next(o for o in opts if o["ok"] and not o["current"]))
    second = entries(client, ready)[0]
    listing = {e["id"]: e for e in entries(client, ready)}
    assert listing[first["id"]]["can_undo"] is False and "changed again" in listing[first["id"]]["undo_reason"]
    assert undo(client, ready, first["id"], 409).json()["detail"]["message"].startswith("These classes were changed again")
    undo(client, ready, second["id"])
    undo(client, ready, first["id"])
    assert placements(client, ready) == original


def test_edits_to_different_classes_can_be_undone_independently(client, ready):
    original = placements(client, ready)
    sessions = [s for s in client.get("/api/timetable", headers=ready).json()["sessions"] if s["length"] == 1 and not s["sync_group"]]
    moved = []
    for s in sessions:
        opts = client.get(f"/api/timetable/sessions/{s['id']}/options", headers=ready).json()["options"]
        free = next((o for o in opts if o["ok"] and not o["current"]), None)
        if free and s["task_key"] not in moved:
            move(client, ready, s["id"], free)
            moved.append(s["task_key"])
        if len(moved) == 2:
            break
    assert len(moved) == 2
    older, newer = entries(client, ready)[1], entries(client, ready)[0]
    assert older["can_undo"] and newer["can_undo"]
    undo(client, ready, older["id"])                                                           # undo the OLDER edit first: allowed
    assert client.get("/api/timetable/check", headers=ready).json()["ok"]
    undo(client, ready, newer["id"])
    assert placements(client, ready) == original


def test_undo_is_refused_when_data_changes_would_make_the_restored_state_invalid(client, ready):
    t, free, _ = movable(client, ready)
    fac = next(f for f in client.get("/api/faculty", headers=ready).json() if f["id"] == t["faculty_id"])
    move(client, ready, t["id"], free[0])
    entry = entries(client, ready)[0]
    # the teacher becomes unavailable in the ORIGINAL slot: restoring it would now break a hard rule
    client.put(f"/api/faculty/{fac['id']}", headers=ready,
               json={**fac, "unavailable": [[t["day"], p] for p in range(t["start"], t["start"] + t["length"])]})
    before = snapshot(client, ready)
    r = undo(client, ready, entry["id"], 409)
    assert "new hard-constraint violation" in r.json()["detail"]["message"] and r.json()["detail"]["errors"]
    assert snapshot(client, ready) == before                                                    # nothing changed
    assert entries(client, ready)[0]["id"] == entry["id"] and not entries(client, ready)[0]["undone"]


# ------------------------------------------------------------------ undo: snapshots
def test_undoing_a_regeneration_restores_the_previous_timetable_newest_first(client, ready):
    original = placements(client, ready)
    client.post("/api/timetable/generate", headers=ready, json={"time_limit": 3, "seed": 99})   # a different timetable
    regen = entries(client, ready)[0]
    assert regen["kind"] == "generated" and regen["can_undo"]
    t, free, _ = movable(client, ready)
    move(client, ready, t["id"], free[0])
    newer = {e["id"]: e for e in entries(client, ready)}[regen["id"]]
    assert newer["can_undo"] is False and "Newer changes exist" in newer["undo_reason"]
    undo(client, ready, regen["id"], 409)
    undo(client, ready, entries(client, ready)[0]["id"])                                        # undo the edit first
    undo(client, ready, regen["id"])                                                            # now the regeneration
    assert placements(client, ready) == original
    assert client.get("/api/timetable/check", headers=ready).json()["ok"]


def test_undoing_the_very_first_generation_leaves_no_timetable(client, demo):
    generate(client, demo)
    undo(client, demo, entries(client, demo)[0]["id"])
    t = client.get("/api/timetable", headers=demo).json()
    assert t["meta"] is None and t["sessions"] == []


def test_undoing_a_deleted_timetable_brings_it_back(client, ready):
    original = placements(client, ready)
    client.delete("/api/timetable", headers=ready)
    assert client.get("/api/timetable", headers=ready).json()["meta"] is None
    undo(client, ready, entries(client, ready)[0]["id"])
    assert placements(client, ready) == original


def test_changes_made_outside_the_history_block_snapshot_undo(client, ready):
    gen = entries(client, ready)[0]
    assert gen["can_undo"]
    example = client.get("/api/import/template?example=true", headers=ready).content
    r = client.post("/api/import/commit", headers=ready, files={"file": ("a.xlsx", example, "application/octet-stream")},
                    data={"replace_existing": "true", "confirm_replace": "REPLACE"})
    assert r.status_code == 200                                                                 # replace wiped the timetable
    e = next(x for x in entries(client, ready) if x["id"] == gen["id"])
    assert e["can_undo"] is False and "outside the history" in e["undo_reason"]
    assert "outside the history" in undo(client, ready, gen["id"], 409).json()["detail"]["message"]


# ------------------------------------------------------------------ simulations in the history
def test_applied_simulations_are_recorded_and_pure_timetable_ones_can_be_undone(client, ready):
    original = placements(client, ready)
    soft = make(client, ready, [{"type": "soft_weights", "section_gap": 20, "late_start": 0, "faculty_gap": 0, "same_day_repeat": 0,
                                 "long_run": 0, "unbalanced": 0}], time_limit=4, name="Compact days")
    run(client, ready, soft["id"])
    assert client.post(f"/api/scenarios/{soft['id']}/apply", headers=ready, json={"confirm": True}).status_code == 200
    e = entries(client, ready)[0]
    assert e["kind"] == "simulation" and "Compact days" in e["summary"] and e["details"]["data_changes"] is False and e["can_undo"]
    assert client.get("/api/timetable", headers=ready).json()["meta"]["stale"] is False
    undo(client, ready, e["id"])
    assert placements(client, ready) == original
    assert client.get("/api/timetable", headers=ready).json()["meta"]["stale"] is False


def test_simulations_that_changed_data_are_honestly_not_undoable(client, ready):
    sc = make(client, ready, [monday_off(by_code(client, ready)["NB"]["id"])], name="NB off Mondays")
    run(client, ready, sc["id"])
    client.post(f"/api/scenarios/{sc['id']}/apply", headers=ready, json={"confirm": True})
    e = entries(client, ready)[0]
    assert e["kind"] == "simulation" and e["details"]["data_changes"] is True and e["can_undo"] is False
    assert "faculty/room/section/course data" in e["undo_reason"]
    undo(client, ready, e["id"], 409)


# ------------------------------------------------------------------ safety
def test_undo_needs_confirmation_and_failures_roll_back(client, ready, monkeypatch):
    t, free, _ = movable(client, ready)
    move(client, ready, t["id"], free[0])
    entry = entries(client, ready)[0]
    assert client.post(f"/api/history/{entry['id']}/undo", headers=ready, json={}).status_code == 422
    before = snapshot(client, ready)
    real = history.record

    def failing(db, user, kind, *a, **k):
        if kind == "undo":
            raise RuntimeError("could not write history")
        return real(db, user, kind, *a, **k)

    monkeypatch.setattr(history, "record", failing)
    crashing = TestClient(app, raise_server_exceptions=False)
    assert crashing.post(f"/api/history/{entry['id']}/undo", headers=ready, json={"confirm": True}).status_code == 500
    monkeypatch.undo()
    assert snapshot(client, ready) == before and not entries(client, ready)[0]["undone"]       # the placement change was rolled back too


def test_history_is_private_to_each_college(client, ready):
    t, free, _ = movable(client, ready)
    move(client, ready, t["id"], free[0])
    eid = entries(client, ready)[0]["id"]
    other = register(client, email="other@college.test")
    assert entries(client, other) == []
    undo(client, other, eid, 404)
    assert client.get("/api/history").status_code == 401
    assert client.post(f"/api/history/{eid}/undo", json={"confirm": True}).status_code == 401
    assert not entries(client, ready)[0]["undone"]


def test_history_keeps_only_the_newest_entries(client, auth):
    db = next(app.dependency_overrides[get_db]())
    user = db.get(models.User, 1)
    for i in range(history.MAX_ENTRIES + 7):
        history.record(db, user, "generated", f"entry {i}", {})
    db.commit()
    rows = entries(client, auth, limit=200)
    assert len(rows) == history.MAX_ENTRIES
    assert rows[0]["summary"] == f"entry {history.MAX_ENTRIES + 6}" and rows[-1]["summary"] == "entry 7"
    db.close()


def test_every_api_timestamp_is_marked_as_utc(client, ready):
    """A zone-less timestamp is read by browsers as LOCAL time, which is wrong by the user's UTC offset."""
    t, free, _ = movable(client, ready)
    move(client, ready, t["id"], free[0])
    sc = make(client, ready, [monday_off(by_code(client, ready)["NB"]["id"])])
    run(client, ready, sc["id"])
    stamps = [entries(client, ready)[0]["created_at"], client.get("/api/timetable", headers=ready).json()["meta"]["generated_at"],
              client.get("/api/stats", headers=ready).json()["timetable"]["generated_at"],
              client.get("/api/analytics", headers=ready).json()["timetable"]["generated_at"],
              client.get(f"/api/scenarios/{sc['id']}", headers=ready).json()["run_at"],
              client.get(f"/api/scenarios/{sc['id']}", headers=ready).json()["created_at"]]
    assert all(x.endswith("Z") for x in stamps), stamps
