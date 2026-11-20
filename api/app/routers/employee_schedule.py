from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..services.employee_schedule_sync import claim_sync, run_sync, sync_status
from ..services.employee_schedule import BindingError, binding_status, list_employees, remove_binding, set_binding


router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class SavedMonthOut(BaseModel):
    month: str
    revision: int
    fetched_at: datetime
    departments: int
    employees: int
    calendar_days: int


class ScheduleStatusOut(BaseModel):
    enabled: bool
    configured: bool
    schema_ready: bool
    status: str
    source: str | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_code: str | None = None
    next_attempt_at: datetime | None = None
    months: list[SavedMonthOut]


def require_admin(user: CurrentUser = Depends(get_current_user)):
    if not is_admin(user):
        raise HTTPException(403, detail={"error": {"code": "admin_only", "message": "Administrator required"}})


@router.get("/employee-schedule", response_model=ScheduleStatusOut, dependencies=[Depends(require_admin)])
def get_status(db: Session = Depends(get_db)):
    return sync_status(db)


@router.post("/employee-schedule/sync", status_code=202, dependencies=[Depends(require_admin)])
def start_sync(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    result, claim = claim_sync(db, manual=True)
    if claim is None:
        status = 409 if result in ("running", "disabled") else 503
        raise HTTPException(status, detail={"error": {"code": result, "message": "Schedule sync not started"}})
    background_tasks.add_task(run_sync, claim)
    return {"status": "accepted"}


class BindingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    external_employee_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)


class EmployeeOut(BaseModel):
    external_employee_id: str
    full_name: str
    department_id: str
    department_name: str
    available: bool


class ShiftOut(BaseModel):
    status: str
    date: date
    reason: str | None = None
    timezone: str
    shift: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    snapshot_updated_at: datetime | None = None


class BindingOut(BaseModel):
    user_id: str
    local_active: bool
    enabled: bool
    external_employee_id: str | None = None
    external_full_name: str | None = None
    external_department_id: str | None = None
    external_department_name: str | None = None
    connected_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_offline_work_date: date | None = None
    schedule: ShiftOut


def _binding_error(exc: BindingError):
    raise HTTPException(exc.status, detail={"error": {"code": exc.code, "message": exc.message}}) from None


@router.get("/employee-schedule/employees", response_model=list[EmployeeOut], dependencies=[Depends(require_admin)])
def get_employees(user_id: str | None = None, db: Session = Depends(get_db)):
    try:
        return list_employees(db, user_id=user_id)
    except BindingError as exc:
        _binding_error(exc)


@router.get("/users/{user_id}/schedule", response_model=BindingOut, dependencies=[Depends(require_admin)])
def get_binding(user_id: str, db: Session = Depends(get_db)):
    try:
        return binding_status(db, user_id)
    except BindingError as exc:
        _binding_error(exc)


@router.patch("/users/{user_id}/schedule", response_model=BindingOut, dependencies=[Depends(require_admin)])
def update_binding(user_id: str, payload: BindingUpdate, db: Session = Depends(get_db)):
    try:
        set_binding(db, user_id, payload.enabled, external_employee_id=payload.external_employee_id)
        return binding_status(db, user_id)
    except BindingError as exc:
        _binding_error(exc)


@router.delete("/users/{user_id}/schedule", response_model=BindingOut, dependencies=[Depends(require_admin)])
def delete_binding(user_id: str, db: Session = Depends(get_db)):
    try:
        remove_binding(db, user_id)
        return binding_status(db, user_id)
    except BindingError as exc:
        _binding_error(exc)
