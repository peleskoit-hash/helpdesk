# Роли и права

## Роли

Роли заданы как строки:
- `admin`
- `agent`
- `trainee`
- `chiki_piki`

Основные helpers: `api/app/auth.py`.

## admin

Администратор:
- видит и управляет всеми заявками в рамках интерфейса;
- видит вкладки `unassigned`, `special`;
- может назначать, принудительно закрывать, удалять, переводить в special;
- имеет доступ к `/settings`, `/settings/users`, `/settings/client-card`, `/settings/bot-buttons`, `/statuses`, `/mass-notifications`;
- управляет меню/кнопками бота, users, client card settings.

Риски:
- не смешивать real admin и preview/effective role;
- admin-only элементы не должны мелькать до загрузки session.

## agent

Сотрудник:
- работает со своими активными/замороженными заявками;
- участвует в автоназначении, если `is_active` и availability status `online`;
- видит закрытые по текущим правилам списка;
- может вернуть в работу только закрытые им заявки, если это разрешено flow;
- отвечает клиенту, комментирует, закрывает, freeze/unfreeze по правам.

Важно:
- `assignment.py` автоназначает только role `agent`, не `trainee`.

## trainee

Стажёр:
- почти как `agent` в UI и карточке;
- не участвует в автоназначении;
- completion stats отображаются как у support user.

Не ломать:
- проверки `is_support_user` обычно включают `agent/trainee`;
- автоназначение должно оставаться только для `agent`.

## chiki_piki

Read-only observer.

Ключевое правило:
- должен видеть только заявки/задачи, связанные с `ClientProfile.destination == "Колл Центр"` или эквивалентной привязкой кнопки к меню "Колл Центр".

Поведение:
- без управления заявкой;
- без reply/comment/close/freeze;
- видит ограниченный список и карточку;
- sidebar stats минимальные.

Риски:
- поиск/counts/list/card не должны расширять доступ;
- при изменении `ticket_list.py` проверять chiki_piki отдельно;
- нельзя полагаться только на frontend hiding.

## Role visibility frontend

Файлы:
- `api/app/static/sidebar.js`
- `api/app/static/list.js`
- `api/app/static/ticket.js`
- templates с `.admin-only`, `.status-panel`, `#adminAgentFilter`

Правило:
- backend всегда главный источник доступа;
- frontend скрывает UI, но не является защитой;
- до `HelpdeskSidebar.init()` role-sensitive элементы должны быть скрыты.

## Где смотреть

- Auth/session: `api/app/auth.py`, `api/app/main.py` route `/api/v1/auth/session`.
- Ticket visibility: `api/app/services/ticket_list.py`, `_get_ticket_or_404` в `tickets.py`.
- UI controls: `api/app/static/ticket_controls.js`, `ticket_composer.js`, `list.js`, `sidebar.js`.
- Settings admin-only APIs: `routers/settings*.py`.
