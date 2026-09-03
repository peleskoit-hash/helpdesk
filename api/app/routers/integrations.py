import hmac
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..schemas import IntegrationTaskCreatePayload, IntegrationTaskCreateResponse
from ..services.integration_tasks import create_integration_task, log_invalid_payload

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def require_integration_api_key(authorization: str | None = Header(default=None)) -> None:
    configured_key = (settings.integration_api_key or "").strip()
    expected_prefix = "Bearer "
    supplied_key = ""
    if authorization and authorization.startswith(expected_prefix):
        supplied_key = authorization[len(expected_prefix):].strip()

    if not configured_key or not supplied_key or not hmac.compare_digest(supplied_key, configured_key):
        _error(status.HTTP_401_UNAUTHORIZED, "invalid_integration_api_key", "Неверный Integration API key")


@router.post(
    "/tasks",
    response_model=IntegrationTaskCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _: None = Depends(require_integration_api_key),
):
    try:
        raw_payload = await request.json()
    except json.JSONDecodeError:
        log_invalid_payload(db)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"error": {"code": "invalid_payload", "message": "Некорректный JSON", "details": {}}})

    if not isinstance(raw_payload, dict):
        log_invalid_payload(db)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"error": {"code": "invalid_payload", "message": "JSON body должен быть объектом", "details": {}}})

    try:
        payload = IntegrationTaskCreatePayload.model_validate(raw_payload)
    except ValidationError as exc:
        raw_deal_id = raw_payload.get("deal_id")
        log_invalid_payload(db, deal_id=str(raw_deal_id).strip() if raw_deal_id is not None else None)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors(include_context=False))

    try:
        result = create_integration_task(
            db,
            deal_id=payload.deal_id,
            deal_url=payload.deal_url,
            client_phone=payload.client_phone,
        )
    except Exception:
        logger.exception("Integration task creation failed")
        _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "task_creation_failed", "Не удалось создать интеграционную задачу")

    if result.result == "duplicate":
        response.status_code = status.HTTP_200_OK
        return IntegrationTaskCreateResponse(
            result=result.result,
            code=result.code,
            deal_id=result.deal_id,
            task_id=result.task_id,
        )
    return IntegrationTaskCreateResponse(
        result=result.result,
        code=result.code,
        deal_id=result.deal_id,
        task_id=result.task_id,
    )
