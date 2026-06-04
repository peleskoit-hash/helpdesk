# Tickets flow

## Основные сущности

Models:
- `Ticket`
- `TicketMessage`
- `TicketAttachment`
- `TicketView`
- `Client`
- `ClientProfile`
- `TelegramContact`

Workflow/status поля:
- `workflow_type`: `ticket`, `task`, `auto`, `group_task_parent`, `group_task_child`.
- `status`: `unassigned`, `new`, `pending`, `closed`.
- `intake_state`: например `collecting`, `ready`, `cancelled`.
- `is_frozen`, `freeze_until`, auto-close fields.

## Список `/tickets`

Backend:
- Router: `api/app/routers/tickets.py`.
- Service: `api/app/services/ticket_list.py`.

Frontend:
- Template: `api/app/templates/tickets.html`.
- JS: `api/app/static/list.js`.

Вкладки:
- `active`
- `closed`
- `frozen`
- `unassigned`
- `special`

URL-state:
- `tab`
- `page`
- `q` / active search query

Правило:
- URL является источником истины для tab/page/q;
- смена tab/search сбрасывает page на 1;
- Back/Forward должны восстанавливать состояние.

## Search

Серверный поиск в `ticket_list.py`:
- subject/button path/menu label;
- client display name;
- message bodies через `EXISTS` по `TicketMessage`;
- case-insensitive через `ilike`;
- применяется после role/tab filters, чтобы не расширять доступ.

Не возвращать дубли тикетов из-за join; использовать `EXISTS` или distinct.

## Карточка `/tickets/{id}`

Frontend:
- `ticket.html`
- `ticket.js`
- `ticket_api.js`
- `ticket_message_view.js`
- `ticket_composer.js`
- `ticket_controls.js`
- `ticket_attachments.js`
- `ticket_client_card.js`

Кнопка "Назад":
- использует `return_url` из URL карточки, если он начинается с `/tickets`;
- fallback собирает tab/page/q;
- не использовать `history.back()` как основной механизм.

## Сообщения

Service: `api/app/services/ticket_messages.py`.

Flow:
- reply to client;
- internal comment;
- edit/delete agent message;
- unfreeze on message;
- remove client close button after agent reply;
- refresh auto-close timer.

Не ломать:
- Telegram message id tracking;
- attachments linked to message;
- visibility `agent_visible`.

## Lifecycle controls

Service: `api/app/services/ticket_lifecycle.py`.

Endpoints in `tickets.py` stay thin:
- `POST /api/v1/tickets/{ticket_id}/close`
- `POST /api/v1/tickets/{ticket_id}/freeze`
- `POST /api/v1/tickets/{ticket_id}/unfreeze`
- `PATCH /api/v1/tickets/{ticket_id}/auto-close`

Restore пока в `tickets.py` и рискованный:
- permissions;
- active collecting checks;
- special branches;
- client active ticket conflicts.

## Close / force close / delete

- Normal close sends answer/client text depending on workflow.
- Admin force close/delete should not accidentally trigger group_task aggregated completion answer.
- Group parent finalization must happen only in intended close flow, not in delete/force-without-answer flows unless explicitly needed.

Ручные проверки после правок lifecycle:
- обычная заявка;
- task;
- admin force close;
- group_task parent/children;
- restore;
- freeze/unfreeze;
- auto-close toggle;
- Telegram messages count.

## group_task parent/child

Смысл:
- parent task creates child tasks on assignment;
- child close can contribute answer/files;
- parent finalizes when all existing non-deleted children are closed/deleted as designed;
- final client response may aggregate child answers.

Риски:
- не отправлять parent client_text fallback вместо aggregated completion text;
- не отправлять два сообщения клиенту;
- не показывать wrong-problem controls на child task;
- не включать child layout reorder where it has no meaning.

## Special tickets

Service: `ticket_special.py`.

- Admin can move ordinary ticket to special.
- Special queue visible to admin.
- Support closed/special visibility has special cases in list counts.

## Problem-menu / wrong-problem

Service: `ticket_problem_flow.py`.

Covers:
- problem menu for ticket client;
- wrong-problem selection;
- menu name resolution by `ClientProfile.destination`;
- txt/hybrid/DB button tree compatibility;
- group child restrictions.

## Attachments

Service: `ticket_attachments.py`.

Covers:
- upload/download/local storage;
- sending attachment to Telegram client;
- stickers/unsupported display in UI.

## Не трогать без отдельной задачи

- Restore flow.
- Problem-menu txt/DB-only migration details.
- Group parent/child finalization semantics.
- Admin bulk delete/close client notification semantics.
- Ticket card partial navigation.
