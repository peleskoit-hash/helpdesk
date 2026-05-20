# Functional Contract v1 (current)

## 1) Channel
- Основной канал v1: Telegram Bot API (long polling).

## 2) Authentication / Access
- Web UI работает через login/logout/session auth.
- Защищённые страницы без сессии редиректят на `/login`.
- Защищённые API без сессии возвращают `401/403`.

## 3) Roles
- `admin` — полный доступ.
- `agent` — рабочий доступ сотрудника.
- `trainee` — как `agent`, но не участвует в автоназначении.
- `chiki_piki` — read-only наблюдатель только по направлению "Колл Центр".

## 4) Workflow types
- `ticket` — обычная заявка.
- `task` — задача с ответом клиенту при завершении.
- `auto` — автоматический сценарий.
- `group_task` / `group_child` — групповая задача.

## 5) Queues / tabs
- Нераспределённые
- Активные
- Особые (только admin)
- Замороженные
- Закрытые

## 6) Visibility rules
- Агенты/стажёры видят свои активные/замороженные.
- Агенты видят все закрытые заявки.
- Агенты могут вернуть в работу только те заявки, которые закрыли сами.
- `chiki_piki` видит только заявки, где `ClientProfile.destination == "Колл Центр"`, и только read-only.

## 7) Special queue
- `is_special=true` — админская очередь.
- Открытые special не входят в обычные active/frozen/unassigned.
- Закрытые special отображаются в закрытых.

## 8) Restore rules
- Обычный `ticket` можно вернуть в работу по текущим ограничениям прав.
- `task` и `auto` возвращать в работу нельзя.

## 9) Auto-close timer
- Работает только для обычных non-special `ticket`.
- Не работает для `task/auto/group_task`, frozen, collecting, closed.

## 10) Telegram menu routing
- DB-only runtime поддерживается через `USE_DB_BUTTONS`.
- Меню выбирается по `ClientProfile.destination` (поле "Куда?") через menu profiles.
- Один профиль меню может быть привязан к нескольким destination.
- При отсутствии явной привязки используется fallback `Базовое`.

## 11) Button settings (current)
- Название/label override;
- активность;
- инструкция;
- текст системный;
- текст клиенту;
- подтверждение;
- фиксированный ответственный;
- freeze_on_create;
- файлы;
- условные файлы;
- automation_key;
- layout rows.

## 12) Completion stats
- Для `agent/trainee` ведётся и показывается статистика выполненных задач/заявок (включая календарные периоды).
- `Legacy` хранится и отображается отдельно.

## 13) Onsite trigger
- В `ticket` фраза агента "Иду к вам" (без учёта регистра) включает onsite trigger:
  - статус агента `on_ticket`;
  - кнопка закрытия в этой заявке = "Решил на месте";
  - при закрытии добавляется служебное напоминание агенту про возврат статуса в "В сети".

## 14) Message/attachment baseline
- Поддерживаются client/agent/internal/system сообщения.
- Attachments и stickers поддерживаются в карточке и доставке по текущим правилам.
