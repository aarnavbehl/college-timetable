"""What-if scenarios. Creating, editing and running a scenario never changes the real data or the saved
timetable; only POST /scenarios/{id}/apply does, after explicit confirmation."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..database import get_db
from ..deps import current_user
from ..serialization import iso
from ..schemas import ApplyIn, ScenarioIn
from ..services import history
from ..services import scenarios as svc
from ..services.dataset import get_or_create_settings, load_session_rows

router = APIRouter(prefix="/scenarios", tags=["simulation"])


def _own(db: DbSession, user: m.User, scenario_id: int) -> m.Scenario:
    sc = db.get(m.Scenario, scenario_id)
    if sc is None or sc.owner_id != user.id:
        raise HTTPException(404, "Scenario not found.")
    return sc


def _state(db: DbSession, uid: int) -> tuple[int, str]:
    return get_or_create_settings(db, uid).data_version, history.rows_fingerprint(load_session_rows(db, uid))


def _json(sc: m.Scenario, version: int, fingerprint: str, ctx: svc.Ctx | None = None) -> dict:
    out = {
        "id": sc.id, "name": sc.name, "description": sc.description, "status": sc.status, "strategy": sc.strategy,
        "time_limit": sc.time_limit, "created_at": iso(sc.created_at), "updated_at": iso(sc.updated_at),
        "run_at": iso(sc.run_at), "applied_at": iso(sc.applied_at),
        "change_count": len(sc.changes or []),
        # a finished simulation is only trustworthy while the data and saved timetable are what it was run against
        "outdated": sc.status in ("ready", "invalid") and (sc.base_data_version != version or sc.base_fingerprint != fingerprint),
        "headline": None,
    }
    if sc.result:
        cmp_ = {r["key"]: r for r in sc.result.get("comparison", [])}
        out["headline"] = {"valid": sc.result.get("candidate_valid", False),
                           "hard_violations": cmp_.get("hard_violations", {}).get("candidate"),
                           "moved": sc.result.get("placement_changes", {}).get("summary", {}).get("moved")}
    if ctx is not None:
        out["changes"] = sc.changes or []
        descriptions = []
        for c in sc.changes or []:
            try:
                descriptions.append(svc.describe_change(ctx, c))
            except (KeyError, StopIteration):
                descriptions.append(f"{c.get('type')} (refers to data that no longer exists)")
        out["descriptions"] = descriptions
        out["result"] = sc.result
    return out


@router.get("")
def list_scenarios(user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    version, fp = _state(db, user.id)
    q = select(m.Scenario).where(m.Scenario.owner_id == user.id).order_by(m.Scenario.id.desc())
    return [_json(sc, version, fp) for sc in db.scalars(q)]


@router.post("", status_code=201)
def create_scenario(body: ScenarioIn, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    ctx = svc.build_context(db, user.id)
    try:
        changes = svc.validate_changes(ctx, body.changes, body.strategy)
    except svc.ScenarioError as e:
        raise HTTPException(e.status, e.detail)
    sc = m.Scenario(owner_id=user.id, name=body.name, description=body.description, changes=changes,
                    strategy=body.strategy, time_limit=body.time_limit)
    db.add(sc)
    db.commit()
    version, fp = _state(db, user.id)
    return _json(sc, version, fp, ctx)


@router.get("/{scenario_id}")
def get_scenario(scenario_id: int, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    sc = _own(db, user, scenario_id)
    version, fp = _state(db, user.id)
    return _json(sc, version, fp, svc.build_context(db, user.id))


@router.put("/{scenario_id}")
def update_scenario(scenario_id: int, body: ScenarioIn, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    sc = _own(db, user, scenario_id)
    if sc.status == "applied":
        raise HTTPException(409, "An applied scenario cannot be edited. Create a new one.")
    ctx = svc.build_context(db, user.id)
    try:
        changes = svc.validate_changes(ctx, body.changes, body.strategy)
    except svc.ScenarioError as e:
        raise HTTPException(e.status, e.detail)
    sc.name, sc.description, sc.changes, sc.strategy, sc.time_limit = body.name, body.description, changes, body.strategy, body.time_limit
    sc.status, sc.candidate, sc.result, sc.run_at = "draft", None, None, None      # old results no longer describe these changes
    sc.base_data_version = sc.base_fingerprint = None
    sc.updated_at = datetime.now(timezone.utc)
    db.commit()
    version, fp = _state(db, user.id)
    return _json(sc, version, fp, ctx)


@router.delete("/{scenario_id}")
def delete_scenario(scenario_id: int, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    sc = _own(db, user, scenario_id)
    db.delete(sc)
    db.commit()
    return {"ok": True}


@router.post("/{scenario_id}/run")
def run_scenario(scenario_id: int, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    """Simulate. Writes only to this scenario row."""
    sc = _own(db, user, scenario_id)
    if sc.status == "applied":
        raise HTTPException(409, "This scenario was already applied. Create a new one to simulate further changes.")
    try:
        svc.run_scenario(db, user.id, sc)
    except svc.ScenarioError as e:
        db.rollback()
        raise HTTPException(e.status, e.detail)
    sc.updated_at = datetime.now(timezone.utc)
    db.commit()
    version, fp = _state(db, user.id)
    return _json(sc, version, fp, svc.build_context(db, user.id))


@router.post("/{scenario_id}/apply")
def apply_scenario(scenario_id: int, body: ApplyIn, user: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    """Replace the saved timetable with the verified candidate (and save the scenario's data changes)."""
    if not body.confirm:
        raise HTTPException(422, "Applying changes your saved timetable. Send confirm=true to proceed.")
    sc = _own(db, user, scenario_id)
    try:
        out = svc.apply_scenario(db, user, sc)
    except svc.ScenarioError as e:
        raise HTTPException(e.status, e.detail)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Applying would create a duplicate record. Nothing was changed.")
    version, fp = _state(db, user.id)
    return {**out, "scenario": _json(sc, version, fp)}
