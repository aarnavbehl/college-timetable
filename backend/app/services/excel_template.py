"""Builds the downloadable Excel templates.

blank template   one sheet per entity with the exact headers, dropdowns, and an Instructions sheet
example workbook the same, filled with the demo college (so you can see every column in use)
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import demo
from .excel_import import SPECS, SheetSpec, format_unavailable

REQUIRED_FILL = PatternFill("solid", fgColor="3B5BDB")
OPTIONAL_FILL = PatternFill("solid", fgColor="7B8794")
WIDTHS = {"Name": 30, "Code": 12, "Unavailable": 26, "Home Room": 14, "Sync Group": 18, "Students Enrolled": 18}

INSTRUCTIONS = [
    ("College Timetable Scheduler — Excel import template", True),
    ("", False),
    ("HOW TO USE", True),
    ("1. Fill the sheets below. Keep the header row exactly as it is (order does not matter, capitals do not matter).", False),
    ("2. Blue headers are REQUIRED. Grey headers are optional (a default is used when blank).", False),
    ("3. You do not need every sheet. Rows may refer to records that already exist in the app, e.g. an Allocation can use "
     "a Faculty code that you created earlier in the website.", False),
    ("4. In the app open Import from Excel, upload this file and press Preview. Nothing is saved until you confirm.", False),
    ("", False),
    ("SHEETS (imported in this order)", True),
    ("Branches → Rooms → Sections → Faculty → Courses → Allocations", False),
    ("Sections refer to a Branch by its Code. Faculty/Courses refer to a Department by Branch Code. "
     "Allocations refer to a Section (Branch + Year + Section), a Course Code and a Faculty Code.", False),
    ("", False),
    ("RULES", True),
    ("• Existing records are never changed or deleted: a row that already exists (same Code / Name / section) is skipped.", False),
    ("• Matching is not case-sensitive. 'cse' and 'CSE' are the same branch.", False),
    ("• Type: Rooms → Classroom or Lab.  Courses → Theory or Lab.", False),
    ("• Unavailable (Faculty): days and periods the person cannot teach, e.g.  Fri:5-7; Mon:1,2", False),
    ("• Session Length (Courses): consecutive periods per sitting. Blank = 1 for Theory, 3 for Lab. "
     "Hours Per Week must be a multiple of it.", False),
    ("• Batch (Allocations): blank = whole section; 1, 2… = one lab batch (cannot exceed the section's Batches).", False),
    ("• Sync Group (Allocations): rows with the same label run at the same time (lab rotations, electives).", False),
    ("• Use plain values, not formulas. Dates/colours/merged cells are ignored.", False),
]


def _style(ws, spec: SheetSpec) -> None:
    for i, col in enumerate(spec.cols, start=1):
        cell = ws.cell(row=1, column=i, value=col.header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = REQUIRED_FILL if col.required else OPTIONAL_FILL
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS.get(col.header, max(12, len(col.header) + 4))
    ws.freeze_panes = "A2"


def _validations(ws, spec: SheetSpec) -> None:
    """Dropdowns / whole-number checks for the first 2000 data rows (Excel-side help only)."""
    for i, col in enumerate(spec.cols, start=1):
        letter = get_column_letter(i)
        rng = f"{letter}2:{letter}2000"
        if col.kind == "room_type":
            dv = DataValidation(type="list", formula1='"Classroom,Lab"', allow_blank=False)
        elif col.kind == "course_type":
            dv = DataValidation(type="list", formula1='"Theory,Lab"', allow_blank=False)
        elif col.field == "year":
            dv = DataValidation(type="whole", operator="between", formula1="1", formula2="6", allow_blank=False)
        elif col.kind == "int":
            dv = DataValidation(type="whole", operator="greaterThanOrEqual", formula1="1", allow_blank=True)
        else:
            continue
        dv.error, dv.errorTitle = "Please use a valid value for this column.", "Invalid value"
        ws.add_data_validation(dv)
        dv.add(rng)


def example_rows() -> dict[str, list[list]]:
    """The demo college as spreadsheet rows (column order = SPECS)."""
    rows: dict[str, list[list]] = {
        "branches": [[c, n] for c, n in demo.BRANCHES],
        "rooms": [[n, "Lab" if t == "LAB" else "Classroom", cap] for n, t, cap in demo.ROOMS],
        "sections": [[b, y, n, st, ba, home] for b, y, n, st, ba, home in demo.SECTIONS],
        "faculty": [[c, nm, br, md, mw, format_unavailable(un) or None] for c, nm, br, md, mw, un in demo.FACULTY],
        "courses": [[c, nm, br, "Lab" if t == "LAB" else "Theory", h, ln] for c, nm, br, t, h, ln in demo.COURSES],
        "allocations": [],
    }
    for (branch, year, section), items in demo.ALLOCATIONS.items():
        for course, fac, batch, group, students in items:
            rows["allocations"].append([branch, year, section, course, fac, None, batch, group, students])
    return rows


def build_workbook(example: bool = False) -> bytes:
    wb = Workbook()
    info = wb.active
    info.title = "Instructions"
    info.column_dimensions["A"].width = 120
    for r, (text, bold) in enumerate(INSTRUCTIONS, start=1):
        c = info.cell(row=r, column=1, value=text)
        c.font = Font(bold=bold, size=14 if r == 1 else 11)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    rows = example_rows() if example else {}
    for spec in SPECS:
        ws = wb.create_sheet(spec.name)
        _style(ws, spec)
        _validations(ws, spec)
        for data_row in rows.get(spec.key, []):
            ws.append(data_row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
