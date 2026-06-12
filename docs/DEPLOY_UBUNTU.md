# Deploy ES DESK on Ubuntu Server

## Назначение

Этот runbook описывает развёртывание prod-окружения ES DESK на отдельном Ubuntu Server.

## Предусловия

На сервере должны быть установлены:
- Docker
- Docker Compose plugin
- git
- SSH-доступ к серверу

## Что используется

В проде используются:
- `docker-compose.prod.yml`
- `.env.prod`
- `.env.prod.example`
- `storage/`
- `TG buttons/`

## Подготовка проекта

1. Клонируйте репозиторий или перейдите в проект:

```bash
git clone <repo-url>
cd helpdesk
```

Если код уже присутствует на сервере:

```bash
git pull
```

2. Подготовьте prod env:

```bash
cp .env.prod.example .env.prod
```

3. Заполните и проверьте `.env.prod`:
- `APP_ENV=prod`
- `INITIAL_SEED_ENABLED=false`
- `TELEGRAM_POLLING_ENABLED=true` только на prod
- `TELEGRAM_BOT_TOKEN` = отдельный prod token
- `DATABASE_URL` согласован с `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`
- `SESSION_SECRET_KEY` заменён на не-default placeholder
- `SESSION_COOKIE_SECURE=false` для первого LAN HTTP smoke; `true` только после HTTPS / reverse proxy
- `DISABLE_ADMIN_IDLE_TIMEOUT=false`

4. Обязательно заполните реальные секреты в `.env.prod`:
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `TELEGRAM_BOT_TOKEN`
- `SESSION_SECRET_KEY`
- при необходимости `BITRIX_WEBHOOK_BASE`

5. Сгенерируйте `SESSION_SECRET_KEY`:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(64))
PY
```

6. Проверьте compose-конфиг на основе примера без запуска контейнеров:

```bash
ESDESK_ENV_FILE=.env.prod.example docker compose -f docker-compose.prod.yml --env-file .env.prod.example config
```

`ESDESK_ENV_FILE` нужен только для этой ручной проверки. В реальный `.env.prod` эту переменную добавлять не нужно.

## Правило dev / prod для Telegram polling

- Windows/dev использует локальный `.env` и отдельный dev bot token.
- Ubuntu/prod использует только `.env.prod` и отдельный prod bot token.
- Один и тот же `TELEGRAM_BOT_TOKEN` нельзя одновременно использовать в dev и prod при `TELEGRAM_POLLING_ENABLED=true`.
- Если на Windows используется dev, новый dev token нужно положить именно в локальный `.env`. Файл `.env.dev.example` служит только как безопасный шаблон.

## Запуск

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

## Проверка после запуска

Проверка контейнеров:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
```

Логи API:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f api
```

Логи DB:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f db
```

## Остановка и restart

Остановка:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod down
```

Restart API:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod restart api
```

## Обновление кода

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

## Backup DB

Перед любыми изменениями схемы или ручными операциями рекомендуется снять дамп:

```bash
mkdir -p backups
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > backups/esdesk_$(date +%F_%H-%M).sql
```

## Restore DB

Внимание: restore перезаписывает данные. Перед restore обязательно сделайте backup.

Пример:

```bash
cat backups/esdesk_YYYY-MM-DD_HH-MM.sql | docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db sh -lc 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
```

После restore проверьте, что приложение корректно стартует и все критичные страницы открываются.

## Backup storage

Папку `storage/` тоже нужно периодически архивировать, потому что там могут быть вложения.

## Первый LAN smoke

1. Откройте:

```text
http://SERVER_IP:8000
```

2. Проверьте:
- вход в web UI;
- открытие списка заявок;
- что polling работает только на prod;
- что dev не использует тот же bot token одновременно.

## Что ещё остаётся

Вне этого runbook остаются:
- reverse proxy / TLS
- автоматические backup jobs
- operational tasks для внешних API
