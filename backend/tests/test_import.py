"""Excel import: template, preview, validation, dependency order, duplicates, replace safety."""
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.main import app
from app.services import excel_import as xl
from app.services.demo import ALLOCATIONS

from .conftest import register

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
H = {
    "Branches": ["Code", "Name"],
    "Rooms": ["Name", "Type", "Capacity"],
    "Sections": ["Branch", "Year", "Section", "Students", "Batches", "Home Room"],
    "Faculty": ["Code", "Name", "Department", "Max Per Day", "Max Per Week", "Unavailable"],
    "Courses": ["Code", "Name", "Department", "Type", "Hours Per Week", "Session Length"],
    "Allocations": ["Branch", "Year", "Section", "Course", "Faculty", "Hours Per Week", "Batch", "Sync Group",
                    "Students Enrolled"],
}


def small(**override):
    """A tiny valid college. override['Sheet'] = rows (without header) replaces that sheet."""
    sheets = {
        "Branches": [["CSE", "Computer Science"], ["ECE", "Electronics"]],
        "Rooms": [["C101", "Classroom", 70], ["Lab-1", "Lab", 30]],
        "Sections": [["CSE", 2, "A", 60, 2, "C101"]],
        "Faculty": [["AS", "Dr Sharma", "CSE", 5, 20, "Fri:5-7"], ["RV", "Prof Verma", "CSE", None, None, None]],
        "Courses": [["CS201", "Data Structures", "CSE", "Theory", 4, None], ["CS211", "DS Lab", "CSE", "Lab", 3, None]],
        "Allocations": [["CSE", 2, "A", "CS201", "AS", None, None, None, None],
                        ["CSE", 2, "A", "CS211", "RV", None, 1, "G1", None]],
    }
    sheets.update(override)
    return {name: rows for name, rows in sheets.items() if rows is not None}


def xlsx(sheets: dict, headers: dict | None = None) -> bytes:
    """sheets: name -> data rows. Headers come from H unless overridden (name -> header list)."""
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        ws.append((headers or {}).get(name, H.get(name, ["A"])))
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def post(client, auth, path, data, filename="college.xlsx", **form):
    return client.post(f"/api/import/{path}", headers=auth, files={"file": (filename, data, XLSX)},
                       data={k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()})


def counts(client, auth):
    return {k: len(client.get(f"/api/{k}", headers=auth).json())
            for k in ("branches", "rooms", "sections", "faculty", "courses", "allocations")}


def sheet(result, key):
    return next(s for s in result["sheets"] if s["key"] == key)


def errors_by_row(result, key):
    return {r["row"]: " | ".join(r["errors"]) for r in sheet(result, key)["rows"] if r["status"] == "error"}


# ------------------------------------------------------------------ parsing helpers
def test_parse_and_format_unavailable():
    assert xl.parse_unavailable("Fri:5-7; mon 1,2") == [[0, 1], [0, 2], [4, 5], [4, 6], [4, 7]]
    assert xl.parse_unavailable("Friday = 6") == [[4, 6]]
    assert xl.parse_unavailable("Mon:1-2;Mon:2-3") == [[0, 1], [0, 2], [0, 3]]
    assert xl.format_unavailable([[4, 5], [4, 6], [4, 7], [0, 1], [0, 2], [0, 4]]) == "Mon:1-2,4; Fri:5-7"
    for text in ("Xyz:1", "Fri:", "5-7", "Fri:7-5", "Fri:a", "Fri:13"):
        with pytest.raises(ValueError):
            xl.parse_unavailable(text)
    pairs = [[0, 1], [2, 3], [2, 4], [4, 7]]
    assert xl.parse_unavailable(xl.format_unavailable(pairs)) == pairs


# ------------------------------------------------------------------ templates
def test_blank_template_has_exact_headers_and_requires_login(client, auth):
    assert client.get("/api/import/template").status_code == 401
    r = client.get("/api/import/template", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith(XLSX)
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Instructions", *H]
    for name, headers in H.items():
        assert [c.value for c in wb[name][1]] == headers
        assert wb[name].max_row == 1                      # blank: headers only
    # the blank template is itself a valid (empty) import file
    pv = post(client, auth, "preview", r.content).json()
    assert pv["totals"] == {"rows": 0, "new": 0, "skipped": 0, "rejected": 0} and pv["can_import"] is False
    assert pv["ignored_sheets"] == ["Instructions"]
    assert post(client, auth, "commit", r.content).status_code == 409          # nothing to import


def test_example_workbook_round_trips_into_a_schedulable_college(client, auth):
    data = client.get("/api/import/template?example=true", headers=auth).content
    pv = post(client, auth, "preview", data).json()
    assert pv["totals"] == {"rows": 107, "new": 107, "skipped": 0, "rejected": 0} and pv["can_import_strict"]
    assert counts(client, auth) == dict.fromkeys(counts(client, auth), 0)       # preview wrote nothing

    res = post(client, auth, "commit", data).json()
    assert (res["imported"], res["skipped"], res["rejected"]) == (107, 0, 0)
    assert res["message"] == "Imported 107 records, skipped 0 duplicates, rejected 0."
    assert res["scheduling_check"] == {"ok": True, "errors": [], "warnings": []}
    assert counts(client, auth) == {"branches": 3, "rooms": 10, "sections": 5, "faculty": 14,
                                    "courses": 29, "allocations": 46}

    # the data matches the demo definition exactly
    got = {(a["section_label"], a["course_code"], a["faculty_code"], a["batch"], a["sync_group"], a["students"])
           for a in client.get("/api/allocations", headers=auth).json()}
    want = {(f"{b} Y{y}-{s}", c, f, ba, g, st) for (b, y, s), items in ALLOCATIONS.items()
            for c, f, ba, g, st in items}
    assert got == want
    djs = next(f for f in client.get("/api/faculty", headers=auth).json() if f["code"] == "DJ")
    assert djs["unavailable"] == [[4, 5], [4, 6], [4, 7]]

    # and existing generation + verification still work on imported data
    gen = client.post("/api/timetable/generate", headers=auth, json={"time_limit": 6, "seed": 3})
    assert gen.status_code == 200 and gen.json()["ok"] and gen.json()["classes"] == 101
    assert client.get("/api/timetable/check", headers=auth).json()["ok"] is True


# ------------------------------------------------------------------ dependency order & foreign keys
def test_commit_creates_records_in_dependency_order_with_correct_foreign_keys(client, auth):
    res = post(client, auth, "commit", xlsx(small())).json()
    assert (res["imported"], res["skipped"], res["rejected"]) == (11, 0, 0)
    assert [s["imported"] for s in res["sheets"]] == [2, 2, 1, 2, 2, 2]

    branches = {b["code"]: b for b in client.get("/api/branches", headers=auth).json()}
    rooms = {r["name"]: r for r in client.get("/api/rooms", headers=auth).json()}
    sec = client.get("/api/sections", headers=auth).json()[0]
    assert sec["branch_id"] == branches["CSE"]["id"] and sec["branch_code"] == "CSE"
    assert sec["home_room_id"] == rooms["C101"]["id"] and sec["batches"] == 2 and sec["strength"] == 60
    fac = {f["code"]: f for f in client.get("/api/faculty", headers=auth).json()}
    assert fac["AS"]["branch_id"] == branches["CSE"]["id"] and fac["AS"]["unavailable"] == [[4, 5], [4, 6], [4, 7]]
    assert (fac["RV"]["max_per_day"], fac["RV"]["max_per_week"]) == (5, 20)             # defaults
    courses = {c["code"]: c for c in client.get("/api/courses", headers=auth).json()}
    assert (courses["CS201"]["session_length"], courses["CS211"]["session_length"]) == (1, 3)   # theory 1, lab 3
    assert courses["CS211"]["course_type"] == "LAB" and courses["CS211"]["branch_id"] == branches["CSE"]["id"]
    allocs = {a["course_code"]: a for a in client.get("/api/allocations", headers=auth).json()}
    assert allocs["CS201"]["section_id"] == sec["id"] and allocs["CS201"]["course_id"] == courses["CS201"]["id"]
    assert allocs["CS201"]["faculty_id"] == fac["AS"]["id"]
    assert (allocs["CS211"]["batch"], allocs["CS211"]["sync_group"]) == (1, "G1")
    assert allocs["CS211"]["faculty_id"] == fac["RV"]["id"]


def test_rows_may_reference_records_that_already_exist(client, auth):
    post(client, auth, "commit", xlsx(small(Allocations=[])))                          # setup without allocations
    assert counts(client, auth)["allocations"] == 0
    only_alloc = xlsx({"Allocations": [["cse", 2, "a", "cs201", "as", None, None, None, None]]})   # any letter case
    res = post(client, auth, "commit", only_alloc).json()
    assert res["imported"] == 1
    assert client.get("/api/allocations", headers=auth).json()[0]["faculty_code"] == "AS"


# ------------------------------------------------------------------ preview
def test_preview_writes_nothing_and_describes_every_row(client, auth):
    pv = post(client, auth, "preview", xlsx(small())).json()
    assert pv["totals"] == {"rows": 11, "new": 11, "skipped": 0, "rejected": 0}
    assert counts(client, auth) == dict.fromkeys(counts(client, auth), 0)
    rows = sheet(pv, "faculty")["rows"]
    assert rows[0] == {"row": 2, "status": "new", "errors": [], "note": "",
                       "cells": ["AS", "Dr Sharma", "CSE", "5", "20", "Fri:5-7"]}
    assert rows[1]["cells"][3:5] == ["5", "20"]                                        # defaults are shown
    assert sheet(pv, "courses")["rows"][1]["cells"][5] == "3"                          # lab default session


def test_preview_is_truncated_but_counts_are_exact(client, auth):
    rooms = [[f"R{i}", "Classroom", 50] for i in range(600)]
    pv = post(client, auth, "preview", xlsx({"Rooms": rooms})).json()
    s = sheet(pv, "rooms")
    assert len(s["rows"]) == xl.MAX_PREVIEW_ROWS and s["truncated"] and s["counts"]["new"] == 600


def test_too_many_rows_is_a_sheet_error(client, auth):
    rooms = [[f"R{i}", "Classroom", 50] for i in range(xl.MAX_ROWS + 1)]
    pv = post(client, auth, "preview", xlsx({"Rooms": rooms})).json()
    assert "More than 5000 data rows" in sheet(pv, "rooms")["errors"][0] and pv["can_import"] is False


# ------------------------------------------------------------------ reading quirks
def test_header_variants_number_formats_blank_rows_and_row_numbers(client, auth):
    wb = Workbook()
    ws = wb.active
    ws.title = " rooms "                                  # sheet name: any case / spacing
    ws.append([])                                         # leading blank row
    ws.append(["name", "TYPE", "Capacity ", "Notes"])     # unknown column 'Notes' is ignored
    ws.append(["A1", "classroom", 60.0, "x"])             # float from Excel
    ws.append([None, None, None, None])                   # blank row in the middle
    ws.append([101, "LAB", "30", None])                   # numeric name, text number
    s2 = wb.create_sheet("sections")
    s2.append(["BRANCH", "year", "section_name", "Strength", "batches", "home_room"])
    s2.append(["X", 1, "A", 10, None, None])
    ws3 = wb.create_sheet("Branches")
    ws3.append(["code", "name"])
    ws3.append(["X", "Branch X"])
    buf = io.BytesIO()
    wb.save(buf)
    pv = post(client, auth, "preview", buf.getvalue()).json()
    rooms = sheet(pv, "rooms")
    assert [r["row"] for r in rooms["rows"]] == [3, 5]                                   # real Excel row numbers
    assert rooms["rows"][1]["cells"] == ["101", "Lab", "30"] and rooms["warnings"] == ["Ignored unknown column 'Notes'."]
    assert all(r["status"] == "new" for r in rooms["rows"])
    assert "Missing required column(s): Section" in sheet(pv, "sections")["errors"][0]   # 'section_name' is NOT an alias
    assert sheet(pv, "sections")["counts"]["rejected"] == 1 and sheet(pv, "branches")["counts"]["new"] == 1


def test_aliases_are_accepted(client, auth):
    sheets = {"Branches": [["CSE", "CS"]], "Sections": [["CSE", 1, "A", 40, None, None]],
              "Faculty": [["F1", "Fac", "CSE", 4, 18, None]]}
    headers = {"Sections": ["Branch", "Year", "Section", "Strength", "Batches", "Home Room"],
               "Faculty": ["Code", "Name", "Dept", "Max Periods Per Day", "Max_Per_Week", "Unavailable"]}
    pv = post(client, auth, "preview", xlsx(sheets, headers)).json()
    assert pv["totals"]["rejected"] == 0 and pv["totals"]["new"] == 3


# ------------------------------------------------------------------ validation
def test_every_kind_of_bad_value_is_reported_with_its_row_number(client, auth):
    sheets = small(
        Branches=[["CSE", "Computer Science"], ["TOOLONGCODE123", "Too long"], ["MECH", None]],
        Rooms=[["C101", "Classroom", 70], ["Hall", "Hall", 50], ["R2", "Lab", "abc"], ["R3", "Lab", 0],
               ["R4", "Lab", 60.5], ["R5", "Lab", True], ["Lab-1", "Lab", 30]],
        Sections=[["CSE", 2, "A", 60, 2, "C101"], ["NOPE", 1, "A", 60, 1, None], ["CSE", 9, "B", 60, 1, None],
                  ["CSE", 1, "C", 60, 0, None], ["CSE", 1, "D", 60, 1, "Lab-1"], ["CSE", 1, "E", 60, 1, "Ghost"],
                  ["CSE", 1, "F", 0, 1, None]],
        Faculty=[["AS", "Dr A", "CSE", 5, 20, None], ["F2", "Dr B", "CSE", 5, 20, "Xyz:1"],
                 ["F3", "Dr C", "CSE", 5, 20, "Sat:1"], ["F4", "Dr D", "CSE", 5, 20, "Fri:9"],
                 ["F5", "Dr E", "CSE", 0, 20, None], ["F6", "Dr F", "GHOST", 5, 20, None],
                 ["F7", "Dr G", "CSE", 5, 20, "Fri:0"]],
        Courses=[["CS201", "DS", "CSE", "Theory", 4, None], ["C2", "Bad", "CSE", "Lab", 4, None],
                 ["C3", "Bad", "CSE", "Workshop", 3, None], ["C4", "Bad", "CSE", "Theory", 3, 7],
                 ["C5", "Bad", "CSE", "Theory", None, None]],
        Allocations=[["CSE", 2, "A", "CS201", "AS", None, None, None, None], ["CSE", 2, "Z", "CS201", "AS", None, None, None, None],
                     ["CSE", 2, "A", "NOPE", "AS", None, None, None, None], ["CSE", 2, "A", "CS201", "NOBODY", None, 2, None, None],
                     ["CSE", 2, "A", "CS201", "AS", None, 3, None, None], ["CSE", 2, "A", "CS201", "AS", 5, None, None, None],
                     [None, 2, "A", "CS201", "AS", None, None, None, None]])
    pv = post(client, auth, "preview", xlsx(sheets)).json()

    b = errors_by_row(pv, "branches")
    assert "too long (maximum 12 characters)" in b[3] and "Name is required" in b[4]
    r = errors_by_row(pv, "rooms")
    assert "must be Classroom or Lab (got 'Hall')" in r[3] and "must be a whole number (got 'abc')" in r[4]
    assert "must be at least 1" in r[5] and "must be a whole number (got 60.5)" in r[6] and "must be a whole number" in r[7]
    s = errors_by_row(pv, "sections")
    assert "Branch 'NOPE' was not found" in s[3] and "must be at most 6" in s[4] and "Batches must be at least 1" in s[5]
    assert "is a lab; a home room must be a classroom" in s[6] and "Home Room 'Ghost' was not found" in s[7]
    assert "Students must be at least 1" in s[8]
    f = errors_by_row(pv, "faculty")
    assert "'Xyz' is not a day" in f[3] and "Sat is outside your 5-day week" in f[4]
    assert "period 9 is outside your 7 periods per day" in f[5] and "Max Per Day must be at least 1" in f[6]
    assert "Department 'GHOST' was not found" in f[7] and "period 0 is out of range" in f[8]
    c = errors_by_row(pv, "courses")
    assert "must be a multiple of the session length (3)" in c[3] and "must be Theory or Lab (got 'Workshop')" in c[4]
    assert "Session Length must be at most 6" in c[5] and "Hours Per Week is required" in c[6]
    a = errors_by_row(pv, "allocations")
    assert "Section 'CSE year 2 Z' was not found" in a[3] and "Course 'NOPE' was not found" in a[4]
    assert "Faculty 'NOBODY' was not found" in a[5] and "Batch 3 does not exist" in a[6]
    assert "Duplicate of row 2: CS201 for CSE Y2-A appears more than once" in a[7]   # row 7 repeats row 2's section+course
    assert "Branch is required" in a[8]
    assert pv["has_errors"] and pv["totals"]["rejected"] > 0 and not pv["can_import_strict"] and pv["can_import"]


def test_lab_default_session_message_and_hours_multiple(client, auth):
    sheets = {"Branches": [["CSE", "CS"]], "Courses": [["L1", "Lab", "CSE", "Lab", 4, None], ["L2", "Lab2", "CSE", "Lab", 4, 2]]}
    pv = post(client, auth, "preview", xlsx(sheets)).json()
    assert "labs default to 3-period sessions" in errors_by_row(pv, "courses")[2]
    assert sheet(pv, "courses")["rows"][1]["status"] == "new"


def test_missing_required_column_rejects_the_whole_sheet(client, auth):
    sheets = {"Branches": [["CSE", "CS"]], "Rooms": [["C1", "Classroom"], ["C2", "Lab"]]}
    headers = {"Rooms": ["Name", "Type"]}
    pv = post(client, auth, "preview", xlsx(sheets, headers)).json()
    rooms = sheet(pv, "rooms")
    assert "Missing required column(s): Capacity" in rooms["errors"][0]
    assert rooms["counts"] == {"new": 0, "skipped": 0, "rejected": 2, "total": 2}
    strict = post(client, auth, "commit", xlsx(sheets, headers))
    assert strict.status_code == 409 and counts(client, auth)["branches"] == 0
    partial = post(client, auth, "commit", xlsx(sheets, headers), mode="partial").json()
    assert (partial["imported"], partial["rejected"]) == (1, 2)


def test_duplicate_column_header_is_an_error(client, auth):
    pv = post(client, auth, "preview", xlsx({"Branches": [["A", "B", "C"]]}, {"Branches": ["Code", "Name", "code"]})).json()
    assert "appears more than once" in sheet(pv, "branches")["errors"][0]


# ------------------------------------------------------------------ bad files
def test_unusable_files_get_clear_errors(client, auth):
    assert "not a valid .xlsx" in post(client, auth, "preview", b"Code,Name\nCSE,CS\n").json()["detail"]
    assert "not a valid .xlsx" in post(client, auth, "preview", b"PK\x03\x04garbagegarbage").json()["detail"]
    assert post(client, auth, "preview", xlsx(small()), filename="data.csv").status_code == 422
    assert post(client, auth, "preview", xlsx(small()), filename="old.xls").status_code == 422
    big = post(client, auth, "preview", b"x" * (xl.MAX_FILE_BYTES + 10))
    assert big.status_code == 413
    other = post(client, auth, "preview", xlsx({"Notes": [["hello"]], "Summary": [["x"]]}))
    assert other.status_code == 422 and "No recognised worksheets" in other.json()["detail"]
    assert counts(client, auth) == dict.fromkeys(counts(client, auth), 0)


def test_import_requires_authentication(client):
    data = xlsx(small())
    assert client.post("/api/import/preview", files={"file": ("a.xlsx", data, XLSX)}).status_code == 401
    assert client.post("/api/import/commit", files={"file": ("a.xlsx", data, XLSX)}).status_code == 401


# ------------------------------------------------------------------ duplicates
def test_duplicates_inside_the_file_are_rejected_with_the_first_row_number(client, auth):
    sheets = {"Branches": [["CSE", "A"], ["cse", "B"], ["ECE", "E"]], "Rooms": [["R1", "Lab", 30], ["r1", "Lab", 30]]}
    pv = post(client, auth, "preview", xlsx(sheets)).json()
    assert "Duplicate of row 2" in errors_by_row(pv, "branches")[3]
    assert "Duplicate of row 2" in errors_by_row(pv, "rooms")[3]
    assert sheet(pv, "branches")["counts"]["new"] == 2


def test_importing_the_same_file_twice_adds_nothing_and_changes_nothing(client, auth):
    data = xlsx(small())
    assert post(client, auth, "commit", data).json()["imported"] == 11
    before = counts(client, auth)
    again = post(client, auth, "commit", data).json()
    assert (again["imported"], again["skipped"], again["rejected"]) == (0, 11, 0)
    assert counts(client, auth) == before
    assert all(len(s["skipped_rows"]) == s["skipped"] for s in again["sheets"])
    assert again["message"] == "Imported 0 records, skipped 11 duplicates, rejected 0."


def test_existing_records_are_never_modified_and_differences_are_reported(client, auth):
    post(client, auth, "commit", xlsx(small()))
    changed = small(Rooms=[["c101", "Classroom", 90]], Branches=[["CSE", "Computer Science & Eng"]])
    pv = post(client, auth, "preview", xlsx({k: changed[k] for k in ("Rooms", "Branches")})).json()
    notes = {r["row"]: r["note"] for s in pv["sheets"] for r in s["rows"] if r["status"] == "skip"}
    assert "Capacity: 70 → 90" in notes[2] and "existing values kept" in notes[2]
    res = post(client, auth, "commit", xlsx({k: changed[k] for k in ("Rooms", "Branches")})).json()
    assert res["imported"] == 0 and res["skipped"] == 2
    room = next(r for r in client.get("/api/rooms", headers=auth).json() if r["name"] == "C101")
    assert room["capacity"] == 70
    assert next(b for b in client.get("/api/branches", headers=auth).json() if b["code"] == "CSE")["name"] == "Computer Science"


def test_new_rows_are_added_next_to_existing_ones(client, auth):
    post(client, auth, "commit", xlsx(small()))
    more = xlsx({"Branches": [["CSE", "Computer Science"], ["MECH", "Mechanical"]],
                 "Sections": [["CSE", 2, "A", 60, 2, "C101"], ["MECH", 1, "A", 50, 1, None]]})
    res = post(client, auth, "commit", more).json()
    assert [(s["name"], s["imported"], s["skipped"]) for s in res["sheets"] if s["present"]] == \
        [("Branches", 1, 1), ("Sections", 1, 1)]
    assert counts(client, auth)["sections"] == 2 and counts(client, auth)["branches"] == 3


# ------------------------------------------------------------------ strict vs partial
def broken():
    return small(Branches=[["CSE", "Computer Science"], ["MECH", None]],
                 Sections=[["CSE", 2, "A", 60, 2, "C101"], ["MECH", 1, "A", 50, 1, None]])


def test_strict_mode_imports_nothing_when_any_row_is_bad(client, auth):
    r = post(client, auth, "commit", xlsx(broken()))
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "nothing was imported" in detail["message"] and any("Branches row 3" in e for e in detail["errors"])
    assert counts(client, auth) == dict.fromkeys(counts(client, auth), 0)


def test_partial_mode_imports_valid_rows_and_rejects_dependents_of_rejected_rows(client, auth):
    res = post(client, auth, "commit", xlsx(broken()), mode="partial").json()
    assert (res["imported"], res["skipped"], res["rejected"]) == (10, 0, 2)    # 12 rows: 10 valid + 2 bad
    assert next(s for s in res["sheets"] if s["key"] == "branches")["rejected_rows"] == [
        {"row": 3, "errors": ["Name is required"]}]
    sec = next(s for s in res["sheets"] if s["key"] == "sections")
    assert sec["rejected_rows"][0]["row"] == 3 and "Branch 'MECH' was not found" in sec["rejected_rows"][0]["errors"][0]
    assert counts(client, auth) == {"branches": 1, "rooms": 2, "sections": 1, "faculty": 2, "courses": 2, "allocations": 2}


def test_bad_mode_value(client, auth):
    assert post(client, auth, "commit", xlsx(small()), mode="yolo").status_code == 422


# ------------------------------------------------------------------ replace safety
@pytest.fixture()
def loaded(client, auth):
    client.post("/api/demo", json={}, headers=auth)
    return auth


def test_replace_needs_explicit_confirmation_and_deletes_nothing_without_it(client, loaded):
    before = counts(client, loaded)
    r = post(client, loaded, "commit", xlsx(small()), replace_existing=True)
    assert r.status_code == 422 and "explicit confirmation" in r.json()["detail"]
    r = post(client, loaded, "commit", xlsx(small()), replace_existing=True, confirm_replace="yes")
    assert r.status_code == 422
    assert counts(client, loaded) == before


def test_replace_with_errors_in_the_file_deletes_nothing(client, loaded):
    client.post("/api/timetable/generate", headers=loaded, json={"time_limit": 3, "seed": 1})
    before = counts(client, loaded)
    r = post(client, loaded, "commit", xlsx(broken()), replace_existing=True, confirm_replace="REPLACE", mode="partial")
    assert r.status_code == 409                                     # partial is overridden to strict when replacing
    assert counts(client, loaded) == before
    assert client.get("/api/timetable", headers=loaded).json()["meta"] is not None      # timetable untouched


def test_replace_preview_lists_what_would_be_deleted_without_deleting(client, loaded):
    pv = post(client, loaded, "preview", xlsx(small()), replace_existing=True).json()
    assert pv["will_delete"]["sections"] == 5 and pv["will_delete"]["allocations"] == 46
    assert pv["totals"]["skipped"] == 0 and pv["totals"]["new"] == 11          # existing data is ignored for the plan
    assert counts(client, loaded)["sections"] == 5


def test_confirmed_clean_replace_swaps_all_data_and_clears_the_timetable(client, loaded):
    client.post("/api/timetable/generate", headers=loaded, json={"time_limit": 3, "seed": 1})
    res = post(client, loaded, "commit", xlsx(small()), replace_existing=True, confirm_replace="REPLACE").json()
    assert res["replaced"] is True and res["imported"] == 11 and res["skipped"] == 0
    assert counts(client, loaded) == {"branches": 2, "rooms": 2, "sections": 1, "faculty": 2, "courses": 2, "allocations": 2}
    assert client.get("/api/timetable", headers=loaded).json()["meta"] is None
    assert client.get("/api/stats", headers=loaded).json()["timetable"] is None


def test_failure_in_the_middle_of_a_replace_rolls_everything_back(client, loaded, monkeypatch):
    before = counts(client, loaded)
    real, calls = xl._create, {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 6:
            raise RuntimeError("disk exploded")
        return real(*args, **kwargs)

    monkeypatch.setattr(xl, "_create", flaky)
    crashing = TestClient(app, raise_server_exceptions=False)
    r = crashing.post("/api/import/commit", headers=loaded, files={"file": ("a.xlsx", xlsx(small()), XLSX)},
                      data={"replace_existing": "true", "confirm_replace": "REPLACE"})
    assert r.status_code == 500
    assert counts(client, loaded) == before                          # old data fully restored
    monkeypatch.undo()
    # and a normal add-only import that fails midway leaves no partial rows either
    monkeypatch.setattr(xl, "_create", flaky)
    calls["n"] = 0
    crashing.post("/api/import/commit", headers=loaded, files={"file": ("a.xlsx", xlsx({"Branches": [["N1", "x"], ["N2", "y"], ["N3", "z"],
                  ["N4", "x"], ["N5", "y"], ["N6", "z"]]}), XLSX)})
    assert counts(client, loaded) == before


# ------------------------------------------------------------------ isolation & side effects
def test_two_colleges_never_see_or_affect_each_other(client, auth):
    post(client, auth, "commit", xlsx(small()))
    other = register(client, email="other@college.test")
    assert counts(client, other) == dict.fromkeys(counts(client, other), 0)
    res = post(client, other, "commit", xlsx(small())).json()           # same codes: NOT treated as duplicates
    assert (res["imported"], res["skipped"]) == (11, 0)
    assert counts(client, auth)["branches"] == 2 and counts(client, other)["branches"] == 2
    post(client, other, "commit", xlsx(small()), replace_existing=True, confirm_replace="REPLACE")
    assert counts(client, auth)["allocations"] == 2                       # other college's replace left ours alone


def test_import_flags_an_existing_timetable_as_out_of_date(client, loaded):
    client.post("/api/timetable/generate", headers=loaded, json={"time_limit": 3, "seed": 1})
    assert client.get("/api/timetable", headers=loaded).json()["meta"]["stale"] is False
    post(client, loaded, "commit", xlsx({"Branches": [["NEWB", "New branch"]]}))
    assert client.get("/api/timetable", headers=loaded).json()["meta"]["stale"] is True


def test_scheduling_check_reports_data_that_cannot_be_scheduled(client, auth):
    sheets = small(Faculty=[["AS", "Dr Sharma", "CSE", 5, 2, None], ["RV", "Prof Verma", "CSE", 5, 20, None]])
    res = post(client, auth, "commit", xlsx(sheets)).json()
    assert res["imported"] == 11 and res["scheduling_check"]["ok"] is False
    assert any("maximum is 2" in e for e in res["scheduling_check"]["errors"])


def test_demo_data_feature_still_works_alongside_import(client, auth):
    post(client, auth, "commit", xlsx(small()))
    assert client.post("/api/demo", json={}, headers=auth).status_code == 409        # not empty: demo refuses to overwrite
    assert client.post("/api/demo", json={"reset": True}, headers=auth).status_code == 200
    assert counts(client, auth)["sections"] == 5
