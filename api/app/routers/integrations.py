import hmac
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..schemas import IntegrationTaskCreatePayload, IntegrationTaskCreateResponse, IntegrationTelegramPayload, IntegrationTelegramResponse
from ..services.integration_tasks import create_integration_task, log_invalid_payload
from ..services.integration_telegram import (
    IntegrationTelegramError,
    log_invalid_payload as log_invalid_telegram_payload,
    send_integration_telegram_message,
)

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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _call_time_out(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _invalid_payload_detail(message: str) -> dict:
    return {"error": {"code": "invalid_payload", "message": message, "details": {}}}


def _extract_invalid_telegram_log_values(raw_payload) -> tuple[str | None, datetime | None, str | None]:
    if not isinstance(raw_payload, dict):
        return None, None, None
    raw_user_id = raw_payload.get("user_id")
    bitrix_id = None
    if isinstance(raw_user_id, str):
        bitrix_id = raw_user_id.strip() or None
    elif isinstance(raw_user_id, int) and not isinstance(raw_user_id, bool):
        bitrix_id = str(raw_user_id)

    call_time = None
    raw_call_time = raw_payload.get("call_time")
    if isinstance(raw_call_time, str):
        try:
            call_time = _as_utc(datetime.fromisoformat(raw_call_time.replace("Z", "+00:00")))
        except ValueError:
            call_time = None

    raw_message = raw_payload.get("message")
    message_text = raw_message if isinstance(raw_message, str) else None
    return bitrix_id, call_time, message_text


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


@router.post(
    "/telegram",
    response_model=IntegrationTelegramResponse,
    status_code=status.HTTP_200_OK,
)
async def send_telegram(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_integration_api_key),
):
    try:
        raw_payload = await request.json()
    except json.JSONDecodeError:
        log_invalid_telegram_payload(db)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_invalid_payload_detail("Некорректный JSON"),
        )

    if not isinstance(raw_payload, dict):
        log_invalid_telegram_payload(db)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_invalid_payload_detail("JSON body должен быть объектом"),
        )

    try:
        payload = IntegrationTelegramPayload.model_validate(raw_payload)
    except ValidationError as exc:
        bitrix_id, call_time, message_text = _extract_invalid_telegram_log_values(raw_payload)
        log_invalid_telegram_payload(db, bitrix_id=bitrix_id, call_time=call_time, message_text=message_text)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.errors(include_context=False)},
        )

    try:
        result = send_integration_telegram_message(
            db,
            bitrix_id=payload.user_id,
            call_time=payload.call_time,
            message_text=payload.message,
        )
    except IntegrationTelegramError as exc:
        error_content = {
            "result": "error",
            "code": exc.code,
            "user_id": exc.user_id,
        }
        if exc.code == "telegram_send_failed":
            error_content["description"] = exc.description
        return JSONResponse(
            status_code=exc.status_code,
            content=error_content,
        )
    except Exception:
        logger.exception("Integration Telegram send failed")
        raise

    return IntegrationTelegramResponse(
        result=result.result,
        code=result.code,
        user_id=result.user_id,
        call_time=_call_time_out(result.call_time),
        telegram_message_id=result.telegram_message_id,
    )
