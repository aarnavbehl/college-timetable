"""Excel import endpoints: template download, preview (writes nothing) and commit."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..database import get_db
from ..deps import current_user
from ..scheduler.problem import ProblemError, build_problem
from ..services import demo, excel_import as xl
from ..services.dataset import load_dataset
from ..services.excel_template import build_workbook

router = APIRouter(prefix="/import", tags=["import"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CONFIRM_WORD = "REPLACE"


def _read_upload(file: UploadFile) -> bytes:
    name = (file.filename or "").lower()
    if not name.endswith(".xlsx"):
        raise HTTPException(422, "Please upload an Excel .xlsx file (not .xls or .csv).")
    data = file.file.read(xl.MAX_FILE_BYTES + 1)
    if len(data) > xl.MAX_FILE_BYTES:
        raise HTTPException(413, f"The file is larger than {xl.MAX_FILE_BYTES // (1024 * 1024)} MB.")
    return data


def _plan(db: DbSession, user: m.User, file: UploadFile, replace: bool) -> xl.Plan:
    data = _read_upload(file)
    try:
        wb = xl.read_workbook(data)
    except xl.ImportFileError as e:
        raise HTTPException(422, str(e))
    return xl.build_plan(db, user.id, wb, replace=replace)


@router.get("/template")
def template(example: bool = False, user: m.User = Depends(current_user)):
    """Blank template (headers + instructions), or ?example=true for the demo college as a workbook."""
    name = "college-timetable-example.xlsx" if example else "college-timetable-template.xlsx"
    return Response(build_workbook(example), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/preview")
def preview(file: UploadFile = File(...), replace_existing: bool = Form(False),
            user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    """Validate the workbook and show what WOULD happen. Nothing is written."""
    out = xl.preview_json(_plan(db, user, file, replace_existing))
    if replace_existing:
        out["will_delete"] = xl.existing_counts(db, user.id)
    return out


@router.post("/commit")
def commit(file: UploadFile = File(...), mode: str = Form("strict"), replace_existing: bool = Form(False),
           confirm_replace: str = Form(""), user: m.User = Depends(current_user),
           db: DbSession = Depends(get_db)):
    """Import the workbook. The file is validated again here, so the preview is never trusted.

    mode=strict   (default) import only if every row is valid
    mode=partial  import the valid rows, reject the rest
    replace_existing=true deletes ALL existing setup data and the timetable first; it needs
    confirm_replace="REPLACE" and a completely clean file, and is atomic with the import."""
    if mode not in ("strict", "partial"):
        raise HTTPException(422, "mode must be 'strict' or 'partial'.")
    if replace_existing:
        if confirm_replace != CONFIRM_WORD:
            raise HTTPException(422, f"Replacing existing data needs explicit confirmation: send "
                                     f"confirm_replace={CONFIRM_WORD}.")
        mode = "strict"          # never delete data and then import only part of a file

    plan = _plan(db, user, file, replace_existing)
    try:
        xl.commit(db, user.id, plan, mode=mode,
                  wipe=(lambda: demo.wipe(db, user.id, commit=False)) if replace_existing else None)
    except xl.ImportRefused as e:
        raise HTTPException(409, e.detail)
    except IntegrityError:
        raise HTTPException(409, "Some records changed while importing (a duplicate appeared). "
                                 "Nothing was imported; please try again.")

    result = xl.summary_json(plan, mode=mode, replaced=replace_existing)
    try:      # tell the user right away if the imported data cannot be scheduled
        problem = build_problem(load_dataset(db, user.id))
        result["scheduling_check"] = {"ok": True, "errors": [], "warnings": problem.warnings}
    except ProblemError as e:
        result["scheduling_check"] = {"ok": False, "errors": e.errors[:10], "warnings": []}
    return result
