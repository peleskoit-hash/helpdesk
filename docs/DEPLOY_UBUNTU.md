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

## Обычное обновление кода prod

Эта процедура обновляет только код и контейнеры. Prod БД не перезаписывается.

1. Перейдите в проект:

```bash
cd /opt/es-desk
```

2. Снимите backup PostgreSQL:

```bash
mkdir -p backups
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > backups/esdesk_$(date +%F_%H-%M).sql
```

3. Если за время работы появились новые важные вложения, дополнительно снимите backup `storage/`.

4. Обновите код:

```bash
git pull --ff-only origin main
```

5. Пересоберите и поднимите контейнеры:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

6. Выполните быстрые проверки:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
curl http://127.0.0.1:8000/health
docker compose -f docker-compose.prod.yml --env-file .env.prod logs --tail=100 api
```

7. Убедитесь, что web UI открывается, а Telegram polling работает только на prod token.

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

## Полный refresh test-prod из dev

Внимание: эта процедура уничтожает текущие данные test-prod и допустима только до начала реальной эксплуатации.

1. Перейдите в проект на Ubuntu:

```bash
cd /opt/es-desk
```

2. Остановите prod API, чтобы во время refresh не писать новые данные:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod stop api
```

3. Снимите backup текущего test-prod:
- backup PostgreSQL;
- backup `storage/`, если нужны текущие вложения или черновики.

4. На dev делайте dump только штатным `pg_dump`:

```bash
pg_dump --no-owner --no-privileges ...
```

Не собирайте SQL вручную через PowerShell `>` из вывода `psql`. Если dump делается из контейнера, сначала создайте файл внутри контейнера или на Linux-хосте, затем перенесите его как файл через `docker cp` / `scp`.

5. Передайте dump на Ubuntu как файл:

```bash
scp <dump-file> deploy@<server>:/opt/es-desk/backups/
```

6. Очистите `public` schema в prod БД:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db sh -lc 'psql -U "$POSTGRES_USER" "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"'
```

7. Выполните restore под prod-пользователем:

```bash
cat backups/<dev-dump>.sql | docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db sh -lc 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
```

8. Если нужны dev-вложения, синхронно перенесите `storage/attachments`. Restore только БД без соответствующего `storage/` оставит битые ссылки на файлы.

9. Поднимите API обратно:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build api
```

10. Выполните smoke-check:
- `docker compose ... ps`
- `curl http://127.0.0.1:8000/health`
- вход в web UI;
- открытие списка заявок;
- открытие карточки с вложением;
- проверка, что Telegram polling использует только prod token.

### Telegram cleanup policy после dev -> prod

После restore dev БД в test-prod нужно привести Telegram-runtime данные в безопасное состояние:

- очищать `telegram_updates`;
- очищать `pending_task_intents`;
- обнулять Telegram message ID поля, относящиеся к сообщениям другого бота:
  - `tickets.client_close_message_id`
  - `tickets.menu_message_id`
  - `ticket_messages.telegram_message_id`
  - `ticket_attachments.telegram_message_id`
- для `ticket_attachments` сохранять `local_path` и реальные файлы в `storage/`, но не рассчитывать на dev `telegram_file_id` / `telegram_file_path` как на рабочий источник для prod-бота;
- сохранять `clients`, `client_profiles`, `tickets`, историю сообщений, `bot_button_settings`, `automatic_message_templates`;
- `telegram_contacts` можно сохранять, но сотрудники и клиенты должны заново открыть именно prod-бота и выполнить `/start`, чтобы bindings и меню прогрелись на правильном token;
- никогда не заменять `.env.prod`, prod token и prod secrets dev-файлами.

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
