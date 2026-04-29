from dataclasses import dataclass
from fastapi import Header, HTTPException


@dataclass
class CurrentUser:
    id: str
    role: str


def get_current_user(
    x_user_id: str | None = Header(default=None),
    x_user_role: str | None = Header(default=None),
) -> CurrentUser:
    role = (x_user_role or "agent").lower()
    if role not in {"admin", "agent"}:
        raise HTTPException(status_code=401, detail={"error": {"code": "invalid_role", "message": "Unknown role", "details": {}}})
    if role == "agent" and not x_user_id:
        raise HTTPException(status_code=401, detail={"error": {"code": "missing_user_id", "message": "x-user-id header is required for agent", "details": {}}})
    return CurrentUser(id=x_user_id or "admin", role=role)
