"""End-to-end API tests: the same calls the browser frontend makes."""
from .conftest import register


# ------------------------------------------------------------------ auth
def test_register_login_me(client):
    r = client.post("/api/auth/register", json={"college_name": "Demo College", "email": "A@B.com", "password": "secret1"})
    assert r.status_code == 201 and r.json()["user"]["email"] == "a@b.com"
    login = client.post("/api/auth/login", json={"email": "a@b.com", "password": "secret1"})
    assert login.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {login.json()['token']}"})
    assert me.json()["college_name"] == "Demo College"


def test_auth_failures(client):
    body = {"college_name": "X College", "email": "a@b.com", "password": "secret1"}
    client.post("/api/auth/register", json=body)
    assert client.post("/api/auth/register", json=body).status_code == 409
    assert client.post("/api/auth/login", json={"email": "a@b.com", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "no@one.com", "password": "x"}).status_code == 401
    assert client.post("/api/auth/register", json={**body, "email": "bad"}).status_code == 422
    assert client.post("/api/auth/register", json={**body, "email": "c@d.com", "password": "123"}).status_code == 422
    assert client.get("/api/branches").status_code == 401
    assert client.get("/api/branches", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_change_password(client, auth):
    r = client.post("/api/auth/change-password", headers=auth,
                    json={"current_password": "wrong1", "new_password": "newpass1"})
    assert r.status_code == 422
    assert client.post("/api/auth/change-password", headers=auth,
                       json={"current_password": "secret123", "new_password": "newpass1"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "admin@college.test", "password": "newpass1"}).status_code == 200


def test_colleges_cannot_see_each_others_data(client, demo):
    other = register(client, email="other@college.test")
    assert client.get("/api/sections", headers=other).json() == []
    sec_id = client.get("/api/sections", headers=demo).json()[0]["id"]
    assert client.delete(f"/api/sections/{sec_id}", headers=other).status_code == 404
    assert client.put(f"/api/sections/{sec_id}", headers=other,
                      json={"branch_id": 1, "year": 1, "name": "Z", "strength": 10}).status_code == 404


# ------------------------------------------------------------------ settings
def test_settings_and_period_times(client, auth):
    s = client.get("/api/settings", headers=auth).json()
    assert (s["days_per_week"], s["periods_per_day"]) == (5, 7)
    assert s["period_times"][0] == ["09:00", "09:50"]
    assert s["period_times"][4][0] == "13:05"            # 09:00 + 4*50min = 12:20, + 45min lunch
    r = client.put("/api/settings", headers=auth, json={"days_per_week": 6, "periods_per_day": 8, "start_time": "08:30",
                                                        "period_minutes": 45, "lunch_after": 4, "lunch_minutes": 30})
    assert r.json()["days_per_week"] == 6 and r.json()["period_times"][0] == ["08:30", "09:15"]
    bad = client.put("/api/settings", headers=auth, json={"days_per_week": 6, "periods_per_day": 4, "start_time": "08:30",
                                                          "period_minutes": 45, "lunch_after": 4, "lunch_minutes": 30})
    assert bad.status_code == 422


# ------------------------------------------------------------------ CRUD
def test_branch_room_crud_and_guards(client, auth):
    b = client.post("/api/branches", headers=auth, json={"code": "CSE", "name": "Computer Science"})
    assert b.status_code == 201
    assert client.post("/api/branches", headers=auth, json={"code": "CSE", "name": "Dup"}).status_code == 409
    bid = b.json()["id"]
    assert client.put(f"/api/branches/{bid}", headers=auth, json={"code": "CS", "name": "CS Dept"}).json()["code"] == "CS"
    room = client.post("/api/rooms", headers=auth, json={"name": "R1", "room_type": "CLASSROOM", "capacity": 60}).json()
    sec = client.post("/api/sections", headers=auth, json={"branch_id": bid, "year": 2, "name": "A", "strength": 60,
                                                           "batches": 2, "home_room_id": room["id"]})
    assert sec.status_code == 201 and sec.json()["label"] == "CS Y2-A" and sec.json()["home_room"] == "R1"
    assert client.delete(f"/api/branches/{bid}", headers=auth).status_code == 409      # has a section
    assert client.delete(f"/api/rooms/{room['id']}", headers=auth).status_code == 200  # releases the home room
    assert client.get("/api/sections", headers=auth).json()[0]["home_room_id"] is None
    assert client.delete(f"/api/sections/{sec.json()['id']}", headers=auth).status_code == 200
    assert client.delete(f"/api/branches/{bid}", headers=auth).status_code == 200


def test_validation_of_section_faculty_course(client, auth):
    bid = client.post("/api/branches", headers=auth, json={"code": "ME", "name": "Mech"}).json()["id"]
    lab = client.post("/api/rooms", headers=auth, json={"name": "L1", "room_type": "LAB", "capacity": 30}).json()
    assert client.post("/api/sections", headers=auth, json={"branch_id": bid, "year": 1, "name": "A", "strength": 50,
                                                            "home_room_id": lab["id"]}).status_code == 422   # lab as home room
    assert client.post("/api/sections", headers=auth, json={"branch_id": 999, "year": 1, "name": "A",
                                                            "strength": 50}).status_code == 422                 # unknown branch
    assert client.post("/api/faculty", headers=auth, json={"name": "Dr X", "code": "X",
                                                           "unavailable": [[9, 1]]}).status_code == 422          # day 9 invalid
    ok = client.post("/api/faculty", headers=auth, json={"name": "Dr X", "code": "X", "unavailable": [[0, 1], [0, 1], [1, 2]]})
    assert ok.status_code == 201 and ok.json()["unavailable"] == [[0, 1], [1, 2]]
    assert client.post("/api/courses", headers=auth, json={"code": "L1", "name": "Lab", "course_type": "LAB",
                                                           "hours_per_week": 4, "session_length": 3}).status_code == 422
    assert client.post("/api/courses", headers=auth, json={"code": "L1", "name": "Lab", "course_type": "LAB",
                                                           "hours_per_week": 3, "session_length": 3}).status_code == 201


def test_allocation_rules_and_cascade(client, demo):
    secs = {s["label"]: s for s in client.get("/api/sections", headers=demo).json()}
    courses = {c["code"]: c for c in client.get("/api/courses", headers=demo).json()}
    fac = {f["code"]: f for f in client.get("/api/faculty", headers=demo).json()}
    sec = secs["CSE Y2-A"]
    base = {"section_id": sec["id"], "course_id": courses["CS201"]["id"], "faculty_id": fac["AS"]["id"]}
    assert client.post("/api/allocations", headers=demo, json=base).status_code == 409          # already allocated
    assert client.post("/api/allocations", headers=demo,
                       json={**base, "course_id": courses["CS211"]["id"], "batch": 5}).status_code == 422   # no batch 5
    assert client.post("/api/allocations", headers=demo,
                       json={**base, "course_id": courses["CS211"]["id"], "batch": 1, "hours_per_week": 4}).status_code == 422
    allocs = client.get("/api/allocations", headers=demo).json()
    first = next(a for a in allocs if a["course_code"] == "CS201" and a["section_label"] == "CSE Y2-A")
    assert first["faculty_name"] == "Dr. Anita Sharma" and first["effective_hours"] == 4
    assert client.delete(f"/api/faculty/{fac['AS']['id']}", headers=demo).status_code == 409     # still teaching
    assert client.delete(f"/api/courses/{courses['CS201']['id']}", headers=demo).status_code == 409
    n_before = len(allocs)
    assert client.delete(f"/api/sections/{sec['id']}", headers=demo).status_code == 200            # cascades
    assert len(client.get("/api/allocations", headers=demo).json()) < n_before


# ------------------------------------------------------------------ dashboard / demo / validate
def test_stats_checklist_and_demo(client, auth):
    s = client.get("/api/stats", headers=auth).json()
    assert not any(step["done"] for step in s["checklist"]) and s["timetable"] is None
    assert client.post("/api/demo", json={}, headers=auth).json()["allocations"] > 40
    s = client.get("/api/stats", headers=auth).json()
    assert all(step["done"] for step in s["checklist"])
    assert client.post("/api/demo", json={}, headers=auth).status_code == 409                  # would overwrite
    assert client.post("/api/demo", json={"reset": True}, headers=auth).status_code == 200
    assert len(client.get("/api/sections", headers=auth).json()) == 5


def test_validate_reports_problems_before_generating(client, demo):
    assert client.get("/api/validate", headers=demo).json()["ok"] is True
    fac = client.get("/api/faculty", headers=demo).json()[0]
    client.put(f"/api/faculty/{fac['id']}", headers=demo, json={**fac, "max_per_week": 5})
    v = client.get("/api/validate", headers=demo).json()
    assert v["ok"] is False and any("maximum is 5" in e for e in v["errors"])
    r = client.post("/api/timetable/generate", headers=demo, json={"time_limit": 2})
    assert r.status_code == 422 and r.json()["detail"]["errors"]


# ------------------------------------------------------------------ timetable
def generate(client, headers, seconds=8):
    r = client.post("/api/timetable/generate", headers=headers, json={"time_limit": seconds, "seed": 11})
    assert r.status_code == 200, r.text
    return r.json()


def test_generate_view_check_export(client, demo):
    assert client.get("/api/timetable", headers=demo).json()["meta"] is None
    res = generate(client, demo)
    assert res["ok"] and res["hard"] == 0 and res["classes"] == 101 and res["sessions"] > 101
    t = client.get("/api/timetable", headers=demo).json()
    assert t["meta"]["stale"] is False and t["meta"]["hard"] == 0 and len(t["sessions"]) == res["sessions"]
    s = t["sessions"][0]
    assert {"section", "course_code", "faculty", "room", "day", "start", "length", "course_type"} <= s.keys()
    assert client.get("/api/timetable/check", headers=demo).json() == {"ok": True, "problems": [], "sessions": res["sessions"]}
    csv = client.get("/api/timetable/export.csv", headers=demo)
    assert csv.status_code == 200 and csv.text.startswith("Section,Batch,Day")
    assert len(csv.text.strip().splitlines()) == res["sessions"] + 1
    assert client.get("/api/stats", headers=demo).json()["timetable"]["stale"] is False


def test_editing_data_marks_timetable_stale_and_old_one_cannot_be_edited(client, demo):
    generate(client, demo, 5)
    sec = client.get("/api/sections", headers=demo).json()[0]
    client.put(f"/api/sections/{sec['id']}", headers=demo, json={**sec, "strength": 55})
    assert client.get("/api/timetable", headers=demo).json()["meta"]["stale"] is True
    assert client.get("/api/stats", headers=demo).json()["timetable"]["stale"] is True


def test_clear_timetable(client, demo):
    generate(client, demo, 5)
    assert client.delete("/api/timetable", headers=demo).status_code == 200
    assert client.get("/api/timetable", headers=demo).json()["meta"] is None
    assert client.get("/api/timetable/check", headers=demo).status_code == 404


def test_manual_move_with_options_and_force(client, demo):
    generate(client, demo, 8)
    sessions = client.get("/api/timetable", headers=demo).json()["sessions"]
    target = next(s for s in sessions if s["length"] == 1 and not s["sync_group"])
    opts = client.get(f"/api/timetable/sessions/{target['id']}/options", headers=demo).json()["options"]
    assert any(o["current"] for o in opts)
    free = [o for o in opts if o["ok"] and not o["current"]]
    busy = [o for o in opts if not o["ok"]]
    assert free and busy and all(o["reasons"] for o in busy)

    ok = client.put(f"/api/timetable/sessions/{target['id']}/move", headers=demo,
                    json={"day": free[0]["day"], "start": free[0]["start"]})
    assert ok.status_code == 200 and ok.json()["hard"] == 0
    assert client.get("/api/timetable/check", headers=demo).json()["ok"] is True
    moved = next(s for s in client.get("/api/timetable", headers=demo).json()["sessions"] if s["id"] == target["id"])
    assert (moved["day"], moved["start"]) == (free[0]["day"], free[0]["start"])

    clash = client.put(f"/api/timetable/sessions/{target['id']}/move", headers=demo,
                       json={"day": busy[0]["day"], "start": busy[0]["start"]})
    assert clash.status_code == 409 and clash.json()["detail"]["reasons"]
    forced = client.put(f"/api/timetable/sessions/{target['id']}/move", headers=demo,
                        json={"day": busy[0]["day"], "start": busy[0]["start"], "force": True})
    assert forced.status_code == 200 and forced.json()["hard"] > 0
    assert client.get("/api/timetable/check", headers=demo).json()["ok"] is False          # checker notices
    assert client.get("/api/timetable", headers=demo).json()["meta"]["hard"] > 0


def test_moving_a_lab_rotation_moves_both_batches_together(client, demo):
    generate(client, demo, 8)
    sessions = client.get("/api/timetable", headers=demo).json()["sessions"]
    lab = next(s for s in sessions if s["length"] == 3)
    opts = client.get(f"/api/timetable/sessions/{lab['id']}/options", headers=demo).json()["options"]
    assert all(o["start"] in (1, 2, 5) for o in opts)                                       # none cross lunch
    free = next(o for o in opts if o["ok"] and not o["current"])
    assert client.put(f"/api/timetable/sessions/{lab['id']}/move", headers=demo,
                      json={"day": free["day"], "start": free["start"]}).status_code == 200
    after = client.get("/api/timetable", headers=demo).json()["sessions"]
    partners = [s for s in after if s["task_key"] == lab["task_key"]]
    assert len(partners) == 2 and {(s["day"], s["start"]) for s in partners} == {(free["day"], free["start"])}
    assert client.put(f"/api/timetable/sessions/{lab['id']}/move", headers=demo,
                      json={"day": 0, "start": 4}).status_code == 422                       # would span lunch


# ------------------------------------------------------------------ substitutions
def test_substitution_flow(client, demo):
    generate(client, demo, 8)
    fac = {f["code"]: f for f in client.get("/api/faculty", headers=demo).json()}
    sessions = client.get("/api/timetable", headers=demo).json()["sessions"]
    absent = fac["NB"]                                           # teaches theory + two labs
    day = next(s["day"] for s in sessions if s["faculty_id"] == absent["id"])
    date = f"2026-11-{2 + day:02d}"                              # 2 Nov 2026 is a Monday
    r = client.post("/api/substitutions/generate", headers=demo,
                    json={"date": date, "absent_faculty_ids": [absent["id"]]}).json()
    mine = [s for s in sessions if s["faculty_id"] == absent["id"] and s["day"] == day]
    assert len(r["rows"]) == len(mine) and r["day"] == day
    busy = {}
    for s in sessions:
        if s["day"] == day and s["faculty_id"] != absent["id"]:
            for p in range(s["start"], s["start"] + s["length"]):
                busy.setdefault(s["faculty_id"], set()).add(p)
    for row in r["rows"]:
        assert row["absent_id"] == absent["id"]
        if row["substitute_id"]:
            assert row["substitute_id"] != absent["id"]
            periods = set(range(row["start"], row["start"] + row["length"]))
            assert not (busy.get(row["substitute_id"], set()) & periods)    # genuinely free
            busy.setdefault(row["substitute_id"], set()).update(periods)    # and not double-assigned
    saved = client.post("/api/substitutions", headers=demo, json={"date": date, "absent_faculty_ids": [absent["id"]],
                                                                  "rows": r["rows"]})
    assert saved.status_code == 201
    client.post("/api/substitutions", headers=demo, json={"date": date, "absent_faculty_ids": [absent["id"]], "rows": []})
    plans = client.get("/api/substitutions", headers=demo).json()
    assert len(plans) == 1 and plans[0]["rows"] == []            # saving the same date updates, not duplicates
    assert client.delete(f"/api/substitutions/{plans[0]['id']}", headers=demo).status_code == 200
    assert client.get("/api/substitutions", headers=demo).json() == []


def test_substitution_edge_cases(client, demo):
    fid = client.get("/api/faculty", headers=demo).json()[0]["id"]
    assert client.post("/api/substitutions/generate", headers=demo,
                       json={"date": "2026-11-02", "absent_faculty_ids": [fid]}).status_code == 409   # no timetable yet
    generate(client, demo, 5)
    sat = client.post("/api/substitutions/generate", headers=demo,
                      json={"date": "2026-11-07", "absent_faculty_ids": [fid]}).json()                # Saturday
    assert sat["rows"] == [] and "not a teaching day" in sat["message"]
    assert client.post("/api/substitutions/generate", headers=demo,
                       json={"date": "2026-11-02", "absent_faculty_ids": [99999]}).status_code == 422
    assert client.post("/api/substitutions/generate", headers=demo,
                       json={"date": "not-a-date", "absent_faculty_ids": [fid]}).status_code == 422
    everyone = [f["id"] for f in client.get("/api/faculty", headers=demo).json()]
    all_absent = client.post("/api/substitutions/generate", headers=demo,
                             json={"date": "2026-11-02", "absent_faculty_ids": everyone}).json()
    assert all_absent["unfilled"] == len(all_absent["rows"]) > 0                   # nobody left to cover
