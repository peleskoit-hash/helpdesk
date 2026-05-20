# Functional Contract v1 (current)

## 1) Channel
- Основной канал v1: Telegram Bot API (long polling).

## 2) Roles
- `admin` — полный доступ.
- `agent` — рабочий доступ сотрудника.
- `trainee` — как `agent`, но не участвует в автоназначении.
- `chiki_piki` — read-only наблюдатель только по направлению "Колл Центр".

## 3) Workflow types
- `ticket` — обычная заявка.
- `task` — задача с ответом клиенту при завершении.
- `auto` — автоматический сценарий.
- `group_task` / `group_child` — групповая задача.

## 4) Queues / tabs
- Нераспределённые
- Активные
- Особые (только admin)
- Замороженные
- Закрытые

## 5) Visibility rules
- Агенты/стажёры видят свои активные/замороженные.
- Агенты видят все закрытые заявки.
- Агенты могут вернуть в работу только те заявки, которые закрыли сами.
- `chiki_piki` видит только заявки, где `ClientProfile.destination == "Колл Центр"`, и только read-only.
- `chiki_piki` видит: Нераспределённые, Активные, Замороженные, Закрытые; не видит Особые.

## 6) Special queue
- `is_special=true` — админская очередь.
- Открытые special не входят в обычные active/frozen/unassigned.
- Закрытые special отображаются в закрытых.

## 7) Restore rules
- Обычный `ticket` можно вернуть в работу по текущим ограничениям прав.
- `task` и `auto` возвращать в работу нельзя.

## 8) Auto-close timer
- Работает только для обычных non-special `ticket`.
- Не работает для `task/auto/group_task`, frozen, collecting, closed.

## 9) Telegram menu lifecycle
- После выбора конечной кнопки inline-меню убирается.
- Если конечный пункт не выбран, предсоздание отменяется через 10 минут.
- После закрытия обращений меню автоматически не отправляется; клиент использует `/start`.

## 10) Message/attachment baseline
- Поддерживаются client/agent/internal/system сообщения.
- Attachments и stickers поддерживаются в карточке и доставке по текущим правилам.
