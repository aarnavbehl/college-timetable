"""Excel (.xlsx) import of a college's setup data.

Pipeline (preview and commit share steps 1-2, so the server never trusts the preview):

  1. read_workbook()  open the file, find the worksheets, map header names to fields
  2. build_plan()     validate every row (types, ranges, references, duplicates) against the
                      file AND the college's existing data; nothing is written
  3. commit()         create the new rows in dependency order inside ONE transaction

Worksheets are processed in dependency order:
    Branches -> Rooms -> Sections -> Faculty -> Courses -> Allocations
so a Section can reference a Branch that is created in the same file, and an Allocation can
reference anything from the other five sheets or anything already in the database.

Row outcomes
    new      valid, will be created
    skip     already exists (matched by natural key) - existing data is NEVER modified
    error    rejected, with one or more readable messages
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable

from openpyxl import load_workbook
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..schemas import AllocationIn, BranchIn, CourseIn, FacultyIn, RoomIn, SectionIn
from .dataset import get_or_create_settings, section_label

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000                  # data rows per worksheet
MAX_PREVIEW_ROWS = 500           # rows returned per worksheet in a preview
DAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
        "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
        "sat": 5, "saturday": 5, "sun": 6, "sunday": 6}
DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
ROOM_TYPES = {"classroom": "CLASSROOM", "lab": "LAB"}
COURSE_TYPES = {"theory": "THEORY", "lab": "LAB"}


class ImportFileError(Exception):
    """The file itself cannot be used (not xlsx, no recognised sheets, ...)."""


class ImportRefused(Exception):
    """The import was not performed; `.detail` explains why (shown to the user)."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.detail = {"message": message, "errors": errors or []}


# --------------------------------------------------------------------------- sheet specs
@dataclass(frozen=True)
class Col:
    field: str
    header: str
    required: bool = False
    kind: str = "text"            # text | int | room_type | course_type | unavailable
    default: Any = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SheetSpec:
    key: str
    name: str
    cols: tuple[Col, ...]


SPECS: tuple[SheetSpec, ...] = (        # dependency order
    SheetSpec("branches", "Branches", (
        Col("code", "Code", True), Col("name", "Name", True))),
    SheetSpec("rooms", "Rooms", (
        Col("name", "Name", True), Col("room_type", "Type", True, "room_type"),
        Col("capacity", "Capacity", True, "int"))),
    SheetSpec("sections", "Sections", (
        Col("branch", "Branch", True), Col("year", "Year", True, "int"), Col("name", "Section", True),
        Col("strength", "Students", True, "int", aliases=("Strength",)),
        Col("batches", "Batches", False, "int", 1), Col("home_room", "Home Room"))),
    SheetSpec("faculty", "Faculty", (
        Col("code", "Code", True), Col("name", "Name", True),
        Col("branch", "Department", aliases=("Dept", "Branch")),
        Col("max_per_day", "Max Per Day", False, "int", 5, ("Max Periods Per Day",)),
        Col("max_per_week", "Max Per Week", False, "int", 20, ("Max Periods Per Week",)),
        Col("unavailable", "Unavailable", False, "unavailable"))),
    SheetSpec("courses", "Courses", (
        Col("code", "Code", True), Col("name", "Name", True),
        Col("branch", "Department", aliases=("Dept", "Branch")),
        Col("course_type", "Type", True, "course_type"),
        Col("hours_per_week", "Hours Per Week", True, "int", aliases=("Hours",)),
        Col("session_length", "Session Length", False, "int", aliases=("Periods Per Session",)))),
    SheetSpec("allocations", "Allocations", (
        Col("branch", "Branch", True), Col("year", "Year", True, "int"), Col("section", "Section", True),
        Col("course", "Course", True), Col("faculty", "Faculty", True),
        Col("hours_per_week", "Hours Per Week", False, "int", aliases=("Hours",)),
        Col("batch", "Batch", False, "int"), Col("sync_group", "Sync Group"),
        Col("students", "Students Enrolled", False, "int", aliases=("Enrolled",)))),
)
SPEC_BY_KEY = {s.key: s for s in SPECS}


def _norm(text: Any) -> str:
    """'Max Per Day', 'max_per_day' and 'MAX-PER-DAY' are the same column."""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _k(text: Any) -> str:
    """Case-insensitive natural key."""
    return str(text).strip().casefold()


# --------------------------------------------------------------------------- cell parsing
def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)                      # a code typed as 101 arrives as 101.0
    s = str(v).strip()
    return s or None


def _int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("must be a whole number")
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v.is_integer():
            return int(v)
        raise ValueError(f"must be a whole number (got {v})")
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        f = float(s)
    except ValueError:
        raise ValueError(f"must be a whole number (got '{s}')") from None
    if f.is_integer():
        return int(f)
    raise ValueError(f"must be a whole number (got '{s}')")


def _choice(v: Any, options: dict[str, str]) -> str | None:
    s = _text(v)
    if s is None:
        return None
    if s.lower() in options:
        return options[s.lower()]
    raise ValueError(f"must be {' or '.join(o.title() for o in options)} (got '{s}')")


def parse_unavailable(text: str) -> list[list[int]]:
    """'Fri:5-7; Mon:1,2' -> [[0, 1], [0, 2], [4, 5], [4, 6], [4, 7]] (day 0 = Monday)."""
    pairs: set[tuple[int, int]] = set()
    for entry in re.split(r"[;\n]+", text):
        if not entry.strip():
            continue
        found = re.match(r"^\s*([A-Za-z]+)\s*[:=]?\s*(.+?)\s*$", entry)
        if not found:
            raise ValueError(f"'{entry.strip()}' does not look like 'Fri:5-7'")
        day = DAYS.get(found.group(1).lower())
        if day is None:
            raise ValueError(f"'{found.group(1)}' is not a day (use Mon, Tue, Wed, Thu, Fri, Sat or Sun)")
        for token in found.group(2).split(","):
            token = token.strip()
            if not token:
                continue
            span = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
            if span:
                lo, hi = int(span.group(1)), int(span.group(2))
                if lo > hi:
                    raise ValueError(f"period range '{token}' is backwards")
                periods = range(lo, hi + 1)
            elif token.isdigit():
                periods = [int(token)]
            else:
                raise ValueError(f"'{token}' is not a period number or a range like 5-7")
            for p in periods:
                if not 1 <= p <= 12:
                    raise ValueError(f"period {p} is out of range (1-12)")
                pairs.add((day, p))
    return [list(p) for p in sorted(pairs)]


def format_unavailable(pairs: list) -> str:
    """Inverse of parse_unavailable, compressing runs: [[4,5],[4,6],[4,7]] -> 'Fri:5-7'."""
    by_day: dict[int, list[int]] = {}
    for d, p in sorted((int(a), int(b)) for a, b in pairs):
        by_day.setdefault(d, []).append(p)
    parts = []
    for d, periods in by_day.items():
        runs, start, prev = [], periods[0], periods[0]
        for p in periods[1:] + [None]:
            if p is not None and p == prev + 1:
                prev = p
                continue
            runs.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = p
        parts.append(f"{DAY_ABBR[d]}:{','.join(runs)}")
    return "; ".join(parts)


def parse_cells(spec: SheetSpec, raw: dict[str, Any]):
    """raw cell values -> (typed values, display strings by header, error messages)."""
    values: dict[str, Any] = {}
    display: dict[str, str] = {}
    errors: list[str] = []
    for col in spec.cols:
        v = raw.get(col.field)
        try:
            if col.kind == "int":
                val = _int(v)
            elif col.kind == "room_type":
                val = _choice(v, ROOM_TYPES)
            elif col.kind == "course_type":
                val = _choice(v, COURSE_TYPES)
            elif col.kind == "unavailable":
                t = _text(v)
                val = parse_unavailable(t) if t else None
            else:
                val = _text(v)
        except ValueError as exc:
            errors.append(f"{col.header}: {exc}")
            display[col.header] = "" if v is None else str(v)
            continue
        if val is None:
            if col.required:
                errors.append(f"{col.header} is required")
            val = col.default
        values[col.field] = val
        if val is None:
            display[col.header] = ""
        elif col.kind == "unavailable":
            display[col.header] = format_unavailable(val)
        else:
            display[col.header] = str(val).title() if col.kind in ("room_type", "course_type") else str(val)
    return values, display, errors


# --------------------------------------------------------------------------- reading
@dataclass
class SheetData:
    spec: SheetSpec
    present: bool = False
    actual_name: str = ""
    errors: list[str] = field(default_factory=list)       # sheet-level problems (missing columns...)
    warnings: list[str] = field(default_factory=list)
    rows: list[tuple[int, dict[str, Any]]] = field(default_factory=list)   # (excel row, raw by field)
    row_count: int = 0                                     # non-blank data rows, even if unreadable


@dataclass
class ParsedWorkbook:
    sheets: dict[str, SheetData]
    ignored: list[str]


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def read_workbook(data: bytes) -> ParsedWorkbook:
    if len(data) > MAX_FILE_BYTES:
        raise ImportFileError(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    bad = ("This is not a valid .xlsx workbook. It may be an old .xls file, a CSV, or corrupted — "
           "open it in Excel and use File → Save As → Excel Workbook (.xlsx).")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ImportFileError(bad)
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 - openpyxl raises many types for malformed files
        raise ImportFileError(bad) from None

    sheets = {s.key: SheetData(s) for s in SPECS}
    by_norm = {_norm(s.name): s for s in SPECS}
    ignored: list[str] = []
    try:
        for ws in wb.worksheets:
            spec = by_norm.get(_norm(ws.title))
            if spec is None:
                ignored.append(ws.title)
                continue
            sd = sheets[spec.key]
            if sd.present:
                sd.warnings.append(f"A second sheet named '{ws.title}' was ignored.")
                continue
            sd.present, sd.actual_name = True, ws.title
            _read_sheet(ws, sd)
    finally:
        wb.close()
    if not any(s.present for s in sheets.values()):
        raise ImportFileError("No recognised worksheets found. Name your sheets exactly: "
                              + ", ".join(s.name for s in SPECS) + ". (Download the template to start.)")
    return ParsedWorkbook(sheets, ignored)


def _read_sheet(ws, sd: SheetData) -> None:
    spec = sd.spec
    header_cells: list[Any] | None = None
    colmap: dict[str, int] = {}
    for rown, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if header_cells is None:
            if all(_blank(c) for c in row):
                continue                                      # leading blank rows
            header_cells = list(row)
            colmap = _map_headers(spec, header_cells, sd)
            continue
        if all(_blank(c) for c in row):
            continue
        sd.row_count += 1
        if sd.row_count > MAX_ROWS:
            sd.errors.append(f"More than {MAX_ROWS} data rows. Split the file into smaller parts.")
            sd.rows.clear()
            return
        if not sd.errors:
            raw = {f: (row[i] if i < len(row) else None) for f, i in colmap.items()}
            sd.rows.append((rown, raw))
    if header_cells is None:
        sd.warnings.append("The sheet is empty.")


def _map_headers(spec: SheetSpec, cells: list[Any], sd: SheetData) -> dict[str, int]:
    wanted: dict[str, Col] = {}
    for col in spec.cols:
        for name in (col.header, *col.aliases):
            wanted[_norm(name)] = col
    colmap: dict[str, int] = {}
    for i, cell in enumerate(cells):
        if _blank(cell):
            continue
        col = wanted.get(_norm(cell))
        if col is None:
            sd.warnings.append(f"Ignored unknown column '{str(cell).strip()}'.")
        elif col.field in colmap:
            sd.errors.append(f"Column '{col.header}' appears more than once.")
        else:
            colmap[col.field] = i
    missing = [c.header for c in spec.cols if c.required and c.field not in colmap]
    if missing:
        sd.errors.append("Missing required column(s): " + ", ".join(missing)
                         + ". Expected headers: " + ", ".join(c.header for c in spec.cols) + ".")
    return colmap


# --------------------------------------------------------------------------- planning
@dataclass
class Ref:
    """Something other rows can point at: an existing DB row ('db') or a row from this file ('new')."""
    kind: str
    id: int | None = None
    row: int | None = None
    view: dict = field(default_factory=dict)
    obj: Any = None

    @property
    def pk(self) -> int:
        return self.id if self.kind == "db" else self.obj.id


@dataclass
class Row:
    row: int
    status: str                       # new | skip | error
    cells: list[str]
    errors: list[str] = field(default_factory=list)
    note: str = ""
    record: dict | None = None


@dataclass
class SheetPlan:
    spec: SheetSpec
    present: bool
    actual_name: str
    errors: list[str]
    warnings: list[str]
    rows: list[Row]
    row_count: int

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r.status == status)

    @property
    def rejected(self) -> int:
        return self.row_count if self.errors else self.count("error")


@dataclass
class Plan:
    sheets: dict[str, SheetPlan]
    ignored: list[str]
    replace: bool

    def total(self, which: str) -> int:
        if which == "rejected":
            return sum(s.rejected for s in self.sheets.values())
        status = {"imported": "new", "skipped": "skip"}[which]
        return sum(s.count(status) for s in self.sheets.values())

    @property
    def has_errors(self) -> bool:
        return self.total("rejected") > 0 or any(s.errors for s in self.sheets.values())

    @property
    def total_rows(self) -> int:
        return sum(s.row_count for s in self.sheets.values() if s.present)


class Lookups:
    def __init__(self) -> None:
        self.branches: dict[str, Ref] = {}
        self.rooms: dict[str, Ref] = {}
        self.sections: dict[tuple, Ref] = {}
        self.faculty: dict[str, Ref] = {}
        self.courses: dict[str, Ref] = {}
        self.allocations: dict[tuple, Ref] = {}


def _load_existing(db: DbSession, uid: int) -> Lookups:
    def rows(model):
        return db.scalars(select(model).where(model.owner_id == uid).order_by(model.id)).all()

    L = Lookups()
    branches = {b.id: b for b in rows(m.Branch)}
    rooms = {r.id: r for r in rows(m.Room)}
    courses = {c.id: c for c in rows(m.Course)}
    faculty = {f.id: f for f in rows(m.Faculty)}
    for b in branches.values():
        L.branches[_k(b.code)] = Ref("db", b.id, view={"code": b.code, "name": b.name})
    for r in rooms.values():
        L.rooms[_k(r.name)] = Ref("db", r.id, view={"name": r.name, "type": r.room_type, "capacity": r.capacity})
    section_keys: dict[int, tuple] = {}
    for s in rows(m.Section):
        b = branches[s.branch_id]
        key = (_k(b.code), s.year, _k(s.name))
        section_keys[s.id] = key
        L.sections[key] = Ref("db", s.id, view={
            "label": section_label(b.code, s.year, s.name), "strength": s.strength, "batches": s.batches,
            "home_room": rooms[s.home_room_id].name if s.home_room_id in rooms else None})
    for f in faculty.values():
        L.faculty[_k(f.code)] = Ref("db", f.id, view={
            "name": f.name, "department": branches[f.branch_id].code if f.branch_id in branches else None,
            "max_per_day": f.max_per_day, "max_per_week": f.max_per_week,
            "unavailable": format_unavailable(f.unavailable or [])})
    for c in courses.values():
        L.courses[_k(c.code)] = Ref("db", c.id, view={
            "name": c.name, "department": branches[c.branch_id].code if c.branch_id in branches else None,
            "type": c.course_type, "hours_per_week": c.hours_per_week, "session_length": c.session_length})
    for a in rows(m.Allocation):
        c = courses[a.course_id]
        L.allocations[(section_keys[a.section_id], _k(c.code), a.batch or 0)] = Ref("db", a.id, view={
            "faculty": faculty[a.faculty_id].code, "hours": a.hours_per_week or c.hours_per_week,
            "sync_group": a.sync_group, "students": a.students})
    return L


def _friendly(err: dict) -> str:
    ctx = err.get("ctx") or {}
    kind = err.get("type", "")
    if kind == "greater_than_equal":
        return f"must be at least {ctx.get('ge')}"
    if kind == "less_than_equal":
        return f"must be at most {ctx.get('le')}"
    if kind == "string_too_long":
        return f"is too long (maximum {ctx.get('max_length')} characters)"
    if kind == "string_too_short":
        return "is required"
    return err.get("msg", "is invalid")


def _model_errors(model, payload: dict, spec: SheetSpec) -> list[str]:
    """Run the same pydantic schema the web forms use, with readable messages."""
    headers = {c.field: c.header for c in spec.cols}
    try:
        model(**payload)
        return []
    except ValidationError as exc:
        out = []
        for err in exc.errors():
            name = str(err["loc"][0]) if err["loc"] else ""
            out.append(f"{headers.get(name, name)} {_friendly(err)}")
        return out


def _same(a: Any, b: Any) -> bool:
    norm = lambda x: "" if x is None else str(x).strip().casefold()  # noqa: E731
    return norm(a) == norm(b)


def _skip_note(old: dict, new: dict, labels: dict[str, str]) -> str:
    changes = [f"{label}: {old.get(k) if old.get(k) not in (None, '') else '—'} → "
               f"{new.get(k) if new.get(k) not in (None, '') else '—'}"
               for k, label in labels.items() if not _same(old.get(k), new.get(k))]
    base = "Already exists — skipped"
    return f"{base}; existing values kept (file differs: {', '.join(changes)})" if changes else base


Handler = Callable[[Lookups, dict, Row, "Ctx"], None]


@dataclass
class Ctx:
    settings: m.CollegeSettings


def _run_sheet(sd: SheetData, L: Lookups, ctx: Ctx, handler: Handler) -> SheetPlan:
    sp = SheetPlan(sd.spec, sd.present, sd.actual_name, list(sd.errors), list(sd.warnings), [], sd.row_count)
    headers = [c.header for c in sd.spec.cols]
    for rown, raw in sd.rows:
        values, display, errors = parse_cells(sd.spec, raw)
        row = Row(rown, "error", [display.get(h, "") for h in headers], errors)
        if not errors:
            handler(L, values, row, ctx)
        sp.rows.append(row)
    return sp


def _duplicate_error(row: Row, ref: Ref, what: str) -> None:
    row.errors = [f"Duplicate of row {ref.row}: {what} appears more than once in this sheet"]


def _handle_branch(L: Lookups, v: dict, row: Row, ctx: Ctx) -> None:
    errs = _model_errors(BranchIn, v, SPEC_BY_KEY["branches"])
    if errs:
        row.errors = errs
        return
    key, ref = _k(v["code"]), L.branches.get(_k(v["code"]))
    if ref and ref.kind == "new":
        return _duplicate_error(row, ref, f"branch code '{v['code']}'")
    view = {"code": v["code"], "name": v["name"]}
    if ref:
        row.status, row.note = "skip", _skip_note(ref.view, view, {"name": "Name"})
        return
    L.branches[key] = Ref("new", row=row.row, view=view)
    row.status, row.record = "new", {"ref": L.branches[key], "fields": view}


def _handle_room(L: Lookups, v: dict, row: Row, ctx: Ctx) -> None:
    errs = _model_errors(RoomIn, v, SPEC_BY_KEY["rooms"])
    if errs:
        row.errors = errs
        return
    key, ref = _k(v["name"]), L.rooms.get(_k(v["name"]))
    if ref and ref.kind == "new":
        return _duplicate_error(row, ref, f"room '{v['name']}'")
    view = {"name": v["name"], "type": v["room_type"], "capacity": v["capacity"]}
    if ref:
        row.status, row.note = "skip", _skip_note(ref.view, view, {"type": "Type", "capacity": "Capacity"})
        return
    L.rooms[key] = Ref("new", row=row.row, view=view)
    row.status, row.record = "new", {"ref": L.rooms[key], "fields": {
        "name": v["name"], "room_type": v["room_type"], "capacity": v["capacity"]}}


def _need(table: dict, key: Any, what: str, shown: str, hint: str, errors: list[str]) -> Ref | None:
    ref = table.get(key)
    if ref is None:
        errors.append(f"{what} '{shown}' was not found{hint}")
    return ref


def _handle_section(L: Lookups, v: dict, row: Row, ctx: Ctx) -> None:
    spec = SPEC_BY_KEY["sections"]
    errs = _model_errors(SectionIn, {"branch_id": 0, "year": v["year"], "name": v["name"], "strength": v["strength"],
                                     "batches": v["batches"], "home_room_id": None}, spec)
    branch = _need(L.branches, _k(v["branch"]), "Branch", v["branch"],
                   " (add it to the Branches sheet or create it first)", errs)
    room = None
    if v["home_room"]:
        room = _need(L.rooms, _k(v["home_room"]), "Home Room", v["home_room"],
                     " (add it to the Rooms sheet or create it first)", errs)
        if room and room.view["type"] != "CLASSROOM":
            errs.append(f"Home Room '{v['home_room']}' is a lab; a home room must be a classroom")
    if errs:
        row.errors = errs
        return
    key = (_k(v["branch"]), v["year"], _k(v["name"]))
    ref = L.sections.get(key)
    if ref and ref.kind == "new":
        return _duplicate_error(row, ref, f"section {v['branch']} year {v['year']} '{v['name']}'")
    label = section_label(branch.view["code"], v["year"], v["name"])
    view = {"label": label, "strength": v["strength"], "batches": v["batches"],
            "home_room": room.view["name"] if room else None}
    if ref:
        row.status = "skip"
        row.note = _skip_note(ref.view, view, {"strength": "Students", "batches": "Batches", "home_room": "Home Room"})
        return
    L.sections[key] = Ref("new", row=row.row, view=view)
    row.status, row.record = "new", {"ref": L.sections[key], "branch": branch, "room": room, "fields": {
        "year": v["year"], "name": v["name"], "strength": v["strength"], "batches": v["batches"]}}


def _handle_faculty(L: Lookups, v: dict, row: Row, ctx: Ctx) -> None:
    spec = SPEC_BY_KEY["faculty"]
    pairs = v["unavailable"] or []
    errs = _model_errors(FacultyIn, {"name": v["name"], "code": v["code"], "branch_id": None,
                                     "max_per_day": v["max_per_day"], "max_per_week": v["max_per_week"],
                                     "unavailable": pairs}, spec)
    s = ctx.settings
    bad_days = sorted({d for d, _ in pairs if d >= s.days_per_week})
    bad_periods = sorted({p for _, p in pairs if p > s.periods_per_day})
    if bad_days:
        errs.append("Unavailable: " + ", ".join(DAY_ABBR[d] for d in bad_days)
                    + f" is outside your {s.days_per_week}-day week")
    if bad_periods:
        errs.append("Unavailable: period " + ", ".join(map(str, bad_periods))
                    + f" is outside your {s.periods_per_day} periods per day")
    dept = None
    if v["branch"]:
        dept = _need(L.branches, _k(v["branch"]), "Department", v["branch"],
                     " (add it to the Branches sheet or create it first)", errs)
    if errs:
        row.errors = errs
        return
    key, ref = _k(v["code"]), L.faculty.get(_k(v["code"]))
    if ref and ref.kind == "new":
        return _duplicate_error(row, ref, f"faculty code '{v['code']}'")
    view = {"name": v["name"], "department": dept.view["code"] if dept else None,
            "max_per_day": v["max_per_day"], "max_per_week": v["max_per_week"],
            "unavailable": format_unavailable(pairs)}
    if ref:
        row.status = "skip"
        row.note = _skip_note(ref.view, view, {"name": "Name", "department": "Department", "max_per_day": "Max per day",
                                               "max_per_week": "Max per week", "unavailable": "Unavailable"})
        return
    L.faculty[key] = Ref("new", row=row.row, view=view)
    row.status, row.record = "new", {"ref": L.faculty[key], "branch": dept, "fields": {
        "name": v["name"], "code": v["code"], "max_per_day": v["max_per_day"],
        "max_per_week": v["max_per_week"], "unavailable": pairs}}


def _handle_course(L: Lookups, v: dict, row: Row, ctx: Ctx) -> None:
    spec = SPEC_BY_KEY["courses"]
    length = v["session_length"] or (3 if v["course_type"] == "LAB" else 1)
    row.cells[[c.header for c in spec.cols].index("Session Length")] = str(length)    # show the value that will be saved
    errs = _model_errors(CourseIn, {"code": v["code"], "name": v["name"], "branch_id": None,
                                    "course_type": v["course_type"], "hours_per_week": v["hours_per_week"],
                                    "session_length": length}, spec)
    if not errs and v["hours_per_week"] % length:
        errs.append(f"Hours Per Week ({v['hours_per_week']}) must be a multiple of the session length ({length})"
                    + ("" if v["session_length"] else " — labs default to 3-period sessions; set Session Length "
                       "explicitly if that is wrong"))
    dept = None
    if v["branch"]:
        dept = _need(L.branches, _k(v["branch"]), "Department", v["branch"],
                     " (add it to the Branches sheet or create it first)", errs)
    if errs:
        row.errors = errs
        return
    key, ref = _k(v["code"]), L.courses.get(_k(v["code"]))
    if ref and ref.kind == "new":
        return _duplicate_error(row, ref, f"course code '{v['code']}'")
    view = {"name": v["name"], "department": dept.view["code"] if dept else None, "type": v["course_type"],
            "hours_per_week": v["hours_per_week"], "session_length": length}
    if ref:
        row.status = "skip"
        row.note = _skip_note(ref.view, view, {"name": "Name", "department": "Department", "type": "Type",
                                               "hours_per_week": "Hours", "session_length": "Session length"})
        return
    L.courses[key] = Ref("new", row=row.row, view=view)
    row.status, row.record = "new", {"ref": L.courses[key], "branch": dept, "fields": {
        "code": v["code"], "name": v["name"], "course_type": v["course_type"],
        "hours_per_week": v["hours_per_week"], "session_length": length}}


def _handle_allocation(L: Lookups, v: dict, row: Row, ctx: Ctx) -> None:
    spec = SPEC_BY_KEY["allocations"]
    errs = _model_errors(AllocationIn, {"section_id": 0, "course_id": 0, "faculty_id": 0,
                                        "hours_per_week": v["hours_per_week"], "batch": v["batch"],
                                        "sync_group": v["sync_group"], "students": v["students"]}, spec)
    sec_key = (_k(v["branch"]), v["year"], _k(v["section"]))
    shown = f"{v['branch']} year {v['year']} {v['section']}"
    sec = _need(L.sections, sec_key, "Section", shown, " (add it to the Sections sheet or create it first)", errs)
    course = _need(L.courses, _k(v["course"]), "Course", v["course"],
                   " (add it to the Courses sheet or create it first)", errs)
    fac = _need(L.faculty, _k(v["faculty"]), "Faculty", v["faculty"],
                " (add it to the Faculty sheet or create it first)", errs)
    if sec and v["batch"] is not None and v["batch"] > sec.view["batches"]:
        errs.append(f"Batch {v['batch']} does not exist: {sec.view['label']} has {sec.view['batches']} batch(es) "
                    "(raise Batches on the Sections sheet)")
    hours = None
    if course:
        hours = v["hours_per_week"] or course.view["hours_per_week"]
        if hours % course.view["session_length"]:
            errs.append(f"{hours} hours/week is not a multiple of {v['course']}'s session length "
                        f"({course.view['session_length']})")
    if errs:
        row.errors = errs
        return
    key = (sec_key, _k(v["course"]), v["batch"] or 0)
    ref = L.allocations.get(key)
    what = f"{v['course']} for {sec.view['label']}" + (f" batch {v['batch']}" if v["batch"] else "")
    if ref and ref.kind == "new":
        return _duplicate_error(row, ref, what)
    view = {"faculty": v["faculty"], "hours": hours, "sync_group": v["sync_group"], "students": v["students"]}
    if ref:
        row.status = "skip"
        row.note = _skip_note(ref.view, view, {"faculty": "Faculty", "hours": "Hours",
                                               "sync_group": "Sync group", "students": "Students"})
        return
    L.allocations[key] = Ref("new", row=row.row, view=view)
    row.status, row.record = "new", {"ref": L.allocations[key], "section": sec, "course": course, "faculty": fac,
                                     "fields": {"hours_per_week": v["hours_per_week"], "batch": v["batch"],
                                                "sync_group": v["sync_group"], "students": v["students"]}}


HANDLERS: dict[str, Handler] = {
    "branches": _handle_branch, "rooms": _handle_room, "sections": _handle_section,
    "faculty": _handle_faculty, "courses": _handle_course, "allocations": _handle_allocation,
}


def build_plan(db: DbSession, uid: int, wb: ParsedWorkbook, *, replace: bool = False) -> Plan:
    """Validate everything and decide each row's outcome. Writes nothing.

    With replace=True the existing data is about to be deleted, so it is ignored here:
    references and duplicates are resolved against the file alone."""
    L = Lookups() if replace else _load_existing(db, uid)
    ctx = Ctx(get_or_create_settings(db, uid))
    sheets = {spec.key: _run_sheet(wb.sheets[spec.key], L, ctx, HANDLERS[spec.key]) for spec in SPECS}
    return Plan(sheets, wb.ignored, replace)


# --------------------------------------------------------------------------- committing
def _create(sheet: str, rec: dict, uid: int):
    f = rec["fields"]
    if sheet == "branches":
        return m.Branch(owner_id=uid, **f)
    if sheet == "rooms":
        return m.Room(owner_id=uid, **f)
    if sheet == "sections":
        return m.Section(owner_id=uid, branch_id=rec["branch"].pk,
                         home_room_id=rec["room"].pk if rec["room"] else None, **f)
    if sheet == "faculty":
        return m.Faculty(owner_id=uid, branch_id=rec["branch"].pk if rec["branch"] else None, **f)
    if sheet == "courses":
        return m.Course(owner_id=uid, branch_id=rec["branch"].pk if rec["branch"] else None, **f)
    return m.Allocation(owner_id=uid, section_id=rec["section"].pk, course_id=rec["course"].pk,
                        faculty_id=rec["faculty"].pk, **f)


def existing_counts(db: DbSession, uid: int) -> dict[str, int]:
    """How much data a 'replace' would delete (shown in the confirmation)."""
    def n(model):
        return db.scalar(select(func.count()).select_from(model).where(model.owner_id == uid)) or 0
    return {"branches": n(m.Branch), "rooms": n(m.Room), "sections": n(m.Section), "faculty": n(m.Faculty),
            "courses": n(m.Course), "allocations": n(m.Allocation), "timetable_classes": n(m.Session),
            "substitution_plans": n(m.SubstitutionPlan)}


def commit(db: DbSession, uid: int, plan: Plan, *, mode: str, wipe: Callable[[], None] | None = None) -> None:
    """Create the plan's new rows in dependency order, atomically.

    mode='strict'  refuses if ANY row is rejected (nothing is written)
    mode='partial' creates the valid rows and leaves the rejected ones out
    `wipe`, if given, deletes the existing data first inside the same transaction."""
    if mode not in ("strict", "partial"):
        raise ImportRefused("Unknown import mode.")
    if plan.total_rows == 0:
        raise ImportRefused("The workbook has no data rows to import.")
    if mode == "strict" and plan.has_errors:
        problems = [f"{sp.spec.name}: {e}" for sp in plan.sheets.values() for e in sp.errors]
        problems += [f"{sp.spec.name} row {r.row}: {'; '.join(r.errors)}"
                     for sp in plan.sheets.values() for r in sp.rows if r.status == "error"]
        raise ImportRefused(f"{plan.total('rejected')} row(s) have problems, so nothing was imported. Fix them and "
                            "upload again, or choose 'Import valid rows only'.", problems[:15])
    try:
        if wipe:
            wipe()
        for spec in SPECS:
            for row in plan.sheets[spec.key].rows:
                if row.status != "new":
                    continue
                obj = _create(spec.key, row.record, uid)
                db.add(obj)
                row.record["ref"].obj = obj
            db.flush()                       # ids exist before the next sheet points at them
        settings = get_or_create_settings(db, uid)
        settings.data_version += 1
        db.commit()
    except Exception:
        db.rollback()
        raise


# --------------------------------------------------------------------------- JSON shaping
def preview_json(plan: Plan, limit: int = MAX_PREVIEW_ROWS) -> dict:
    sheets = []
    for spec in SPECS:
        sp = plan.sheets[spec.key]
        shown = sp.rows[:limit]
        sheets.append({
            "key": spec.key, "name": spec.name, "present": sp.present, "sheet_name": sp.actual_name,
            "columns": [{"header": c.header, "required": c.required} for c in spec.cols],
            "errors": sp.errors, "warnings": sp.warnings,
            "counts": {"new": sp.count("new"), "skipped": sp.count("skip"), "rejected": sp.rejected,
                       "total": sp.row_count},
            "rows": [{"row": r.row, "status": r.status, "cells": r.cells, "errors": r.errors, "note": r.note}
                     for r in shown],
            "truncated": len(sp.rows) > limit,
        })
    new, skipped, rejected = plan.total("imported"), plan.total("skipped"), plan.total("rejected")
    blocking = []
    if plan.total_rows == 0:
        blocking.append("The workbook has no data rows.")
    return {
        "replace_existing": plan.replace, "ignored_sheets": plan.ignored,
        "totals": {"rows": plan.total_rows, "new": new, "skipped": skipped, "rejected": rejected},
        "has_errors": plan.has_errors, "blocking": blocking,
        "can_import": new > 0 and not blocking, "can_import_strict": new > 0 and not plan.has_errors and not blocking,
        "sheets": sheets,
    }


def summary_json(plan: Plan, *, mode: str, replaced: bool, limit: int = 50) -> dict:
    sheets = []
    for spec in SPECS:
        sp = plan.sheets[spec.key]
        sheets.append({
            "key": spec.key, "name": spec.name, "present": sp.present,
            "imported": sp.count("new"), "skipped": sp.count("skip"), "rejected": sp.rejected,
            "errors": sp.errors,
            "skipped_rows": [{"row": r.row, "reason": r.note} for r in sp.rows if r.status == "skip"][:limit],
            "rejected_rows": [{"row": r.row, "errors": r.errors} for r in sp.rows if r.status == "error"][:limit],
        })
    imported, skipped, rejected = plan.total("imported"), plan.total("skipped"), plan.total("rejected")
    return {"mode": mode, "replaced": replaced, "imported": imported, "skipped": skipped, "rejected": rejected,
            "sheets": sheets,
            "message": f"Imported {imported} record{'s' if imported != 1 else ''}, skipped {skipped} duplicate"
                       f"{'s' if skipped != 1 else ''}, rejected {rejected}."}
