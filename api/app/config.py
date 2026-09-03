from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_SESSION_SECRET = "change-this-session-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    database_url: str = "postgresql+psycopg2://helpdesk:helpdesk@localhost:5432/helpdesk"
    app_public_base_url: str | None = None
    app_timezone: str = "Europe/Moscow"
    telegram_bot_token: str | None = None
    employee_telegram_bot_token: str | None = None
    telegram_notification_bot_owner_id: str | None = None
    telegram_staging_chat_id: str | None = None
    telegram_polling_enabled: bool = False
    telegram_polling_timeout: int = 25
    employee_telegram_polling_enabled: bool = False
    employee_telegram_polling_timeout: int = 25
    telegram_default_agent_id: str = "00000000-0000-0000-0000-000000000001"
    initial_seed_enabled: bool | None = None
    attachment_storage_dir: str = "/app/storage/attachments"
    bitrix_webhook_base: str | None = None
    bitrix_kc_webhook_base: str | None = None
    bitrix_kc_department_ids: str = "60"
    bitrix_kc_refresh_interval_seconds: int = 3600
    bitrix_kc_max_stale_seconds: int = 28800
    bitrix_kc_final_wait_seconds: int = 5
    bitrix_kc_request_timeout_seconds: int = 10
    bitrix_kc_retry_count: int = 1
    bitrix_password_encryption_key: str | None = None
    client_card_source_allowed_hosts: str | None = None
    session_secret_key: str = DEFAULT_SESSION_SECRET
    session_cookie_name: str = "esdesk_session"
    session_cookie_secure: bool = False
    disable_admin_idle_timeout: bool = False
    use_db_buttons: bool = False
    integration_api_key: str | None = None

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def initial_seed_enabled_effective(self) -> bool:
        if self.initial_seed_enabled is not None:
            return bool(self.initial_seed_enabled)
        return not self.is_prod

    def validate_runtime_guards(self) -> None:
        if not self.is_prod:
            return
        secret = (self.session_secret_key or "").strip()
        if not secret or secret == DEFAULT_SESSION_SECRET:
            raise RuntimeError(
                "SESSION_SECRET_KEY must be set to a non-default value when APP_ENV=prod."
            )


settings = Settings()
