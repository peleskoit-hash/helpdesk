# Local run

```bash
docker compose up --build
```

API: `http://localhost:8000`
Docs (Swagger): `http://localhost:8000/docs`

Web UI:
- List page: `http://localhost:8000/tickets`
- Ticket page: `http://localhost:8000/tickets/{ticket_id}`

## Implemented endpoints

- `GET /health`
- `GET /api/v1/tickets/active?page=1&limit=20`
- `GET /api/v1/tickets/frozen?page=1&limit=20`
- `GET /api/v1/tickets/closed?page=1&limit=20`
- `GET /api/v1/tickets/{id}`
- `POST /api/v1/tickets/{id}/reply`
- `POST /api/v1/tickets/{id}/comment`
- `POST /api/v1/tickets/{id}/greeting`
- `POST /api/v1/tickets/{id}/freeze`
- `POST /api/v1/tickets/{id}/unfreeze`
- `POST /api/v1/tickets/{id}/close`
- `POST /api/v1/messenger/webhook/telegram`

## Web behavior

- Separate pages:
  - `/tickets` for queue/list
  - `/tickets/{id}` for ticket card
- Tabs: `Активные`, `Замороженные`, `Закрытые`
- Default sorting: newest first (`created_at desc`)
- Pagination: 20 tickets per page + `Назад/Вперёд`
- Column `Последний ответ` is shown in list
- Role switch in UI: `agent` or `admin`
- Soft auto-refresh:
  - `/tickets` refreshes the list every 5 seconds
  - `/tickets/{id}` refreshes the ticket card every 3 seconds
  - the page is not reloaded, and the reply/comment draft is preserved
- Later upgrade candidate: replace timer refresh with SSE or WebSocket when the infrastructure is ready for server-pushed events.

## Encoding policy (UTF-8)

- All source files and templates must be saved in UTF-8.
- Do not paste Cyrillic directly into PowerShell heredocs for DB seed/update scripts.
- For shell-driven DB updates, use Unicode escape sequences (`\uXXXX`) or UTF-8 files.
- After any Cyrillic data change, verify via API response decoded as UTF-8.
- If broken text like `????` is detected, fix immediately before continuing feature work.

## Notes

- Statuses: `unassigned`, `new`, `pending`, `closed`
- `close` allowed only if ticket has at least one `agent` message
- Greeting time restriction is disabled for local testing.

## Telegram bot

- Endpoint: `POST /api/v1/messenger/webhook/telegram`
- Local real-bot mode uses Telegram long polling (`TELEGRAM_POLLING_ENABLED=true`).
- No public HTTPS webhook URL is required for local testing.
- Idempotency: duplicate `update_id` is ignored (`{"duplicate": true}`).
- Incoming text message:
  - finds/creates Telegram contact by `chat.id`
  - stores Telegram `username` and display name for the client card
  - finds active ticket for client (`status != closed`) or creates new one
  - assigns new ticket to `TELEGRAM_DEFAULT_AGENT_ID` if that agent is active
  - appends message as `message_type=client`
- Agent `reply` and `greeting` messages are sent back to Telegram via `sendMessage`.
- Command `/close`:
  - closes active ticket for this chat
  - adds system message `Заявка закрыта клиентом через /close`
- Command `/start`:
  - if there is no active ticket, shows the problem type menu
  - if there is an active ticket, keeps the client in the current ticket
- Problem type menu:
  - `Проблема со сделкой`
  - `Проблема с техникой`
  - `Проблема с доступами`
  - `Проблема с раздачей`
  - `Иная проблема`
  - shown as inline buttons under the bot message
  - a new ticket is created only after the client presses an inline button
  - inline buttons are removed after selection and appear again after ticket close
