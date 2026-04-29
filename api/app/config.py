from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg2://helpdesk:helpdesk@localhost:5432/helpdesk"
    app_timezone: str = "Europe/Moscow"
    telegram_bot_token: str | None = None
    telegram_polling_enabled: bool = False
    telegram_polling_timeout: int = 25
    telegram_default_agent_id: str = "00000000-0000-0000-0000-000000000001"
    attachment_storage_dir: str = "/app/storage/attachments"


settings = Settings()
