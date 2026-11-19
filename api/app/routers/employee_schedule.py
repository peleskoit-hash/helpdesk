from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..services.employee_schedule_sync import claim_sync, run_sync, sync_status


router = APIRouter(prefix="/api/v1/settings/employee-schedule", tags=["settings"])


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


@router.get("", response_model=ScheduleStatusOut, dependencies=[Depends(require_admin)])
def get_status(db: Session = Depends(get_db)):
    return sync_status(db)


@router.post("/sync", status_code=202, dependencies=[Depends(require_admin)])
def start_sync(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    result, claim = claim_sync(db, manual=True)
    if claim is None:
        status = 409 if result in ("running", "disabled") else 503
        raise HTTPException(status, detail={"error": {"code": result, "message": "Schedule sync not started"}})
    background_tasks.add_task(run_sync, claim)
    return {"status": "accepted"}
