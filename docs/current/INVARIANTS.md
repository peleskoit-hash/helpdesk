# ES DESK — инварианты, фактические гарантии и границы их доказанности

> **Статус:** исследовательская документация **v0.15 / шаг 42**, 2026-11-08.  
> **Последняя показанная Windows DEV-ревизия:** `8439b32`; последняя показанная Git-ситуация: только неотслеживаемый `docs/current/`.  
> **Назначение:** сохранить для последующей разработки проверяемые правила жизненного цикла и разграничить **то, что код реально обеспечивает**, **то, на что рассчитаны тесты**, и **то, что пока нельзя называть гарантией**.  
> **Граница:** в шаге 42 **прочитаны исходники и тела тестов**, но тесты **не запускались**, DEV/PROD, реальная PostgreSQL, HTTP/Telegram и UI **не проверялись**. Не путать наличие теста с подтверждённым успешным прогоном.

## 1. Как пользоваться документом

Этот документ — не альтернативная спецификация, которая меняет поведение сервиса. Он фиксирует наблюдаемый контракт на указанном `HEAD` и указывает точки, где решение должен принять владелец продукта либо нужна отдельная техническая проверка. Сквозное описание сценариев находится в `WORKFLOWS.md`, схема таблиц — в `DATABASE.md`, детали клиента — в `TELEGRAM.md` и `WEB.md`.

**Обозначения доказательности:**

| Обозначение | Что означает | Чего **не** означает |
|---|---|---|
| **К+Т** | Прочитанный code path реализует правило, а прочитанный тест содержит соответствующую проверку/assert | Что тест действительно прошёл в DEV или PostgreSQL |
| **К** | Правило явно следует из прочитанного исходника | Что оно покрыто тестами или устойчиво ко всем гонкам |
| **КОНТРАКТ?** | Есть различие между возможным бизнес-ожиданием и фактической реализацией; требуется решение | Что обнаружен дефект |
| **НЕ ДОКАЗАНО** | Отсутствует нужная проверка транзакции, БД, конкурентности, окружения или запуска | Что функция обязательно сломана |

**Исследованные источники:** девять полных модулей шага 41 (`routers/tickets.py`, `services/ticket_lifecycle.py`, `assignment.py`, `ticket_admin_actions.py`, `auth.py`, `ticket_list.py`, `ticket_problem_flow.py`, `statistics_lifecycle.py`, `ticket_special.py`); три новых полных модуля шага 42 (`services/ticket_messages.py`, `auto_close.py`, `telegram_client_close.py`); ранее полностью прочитанные `models.py`, WEB-модули и Telegram-модули шагов 35–40. В шаге 42 предоставлены **полные тексты восьми выбранных тестовых файлов**: `test_auto_close.py`, `test_telegram_client_close.py`, `test_ticket_lifecycle_close.py`, `test_admin_delete_regression.py`, `test_statistics_completion_lifecycle.py`, `test_group_parent_visibility.py`, `test_web_task_results.py`, `test_ticket_messages_outbound.py`. Тесты были **проанализированы как текст** и выборочно сопоставлены с исходниками. Это **не полный каталог тестов репозитория**.

### 1.1. Инвариант, ограничение и свойство теста — разные вещи

Например, «обычный сотрудник не может отправить ответ в закрытый тикет» — серверный guard, он отдельно проверяется тестом (**К+Т**). «Клиент Telegram всегда получит ровно одно сообщение, если тикет закрыт» — **не доказано**: внешний API Telegram и транзакция БД не являются одной атомарной операцией. «Групповой parent никогда не закроется до завершения детей» — слишком широкая формулировка: автоматический finalizer проверяет детей, а прямой staff close идёт отдельной ветвью.

## 2. Базовая модель и границы идентичности

| Правило / разграничение | Доказательность | Точка истины и оговорка |
|---|---|---|
| `Ticket` объединяет разные каналы и процессы; `channel`, `workflow_type`, `task_kind`, `intake_state`, `status`, `is_frozen`, `is_special` — независимые оси | **К** | `models.py`, `ticket_lifecycle.py`; нельзя вывести способ доставки из одного `status` |
| `collecting` не равно готовой рабочей заявке; `new`/`pending` не равно непрочитанности; `closed_by_user_id` не равно `completed_by_user_id` | **К** | `ticket_list.py`, `ticket_admin_actions.py`, `statistics_lifecycle.py` |
| `task_kind=group_parent` — контейнер/агрегатор; `group_child` ссылается на него через `parent_ticket_id` | **К** | `models.py`, `ticket_lifecycle.py`; связь `parent_ticket_id` в исследованной модели не является DB-FK |
| WEB `WebChatMessage`, `WebClientDraft`, `TicketMessage` и опубликованный `WebTaskResult` — разные сущности | **К+Т** | `WEB.md`, `test_web_task_results.py`; нельзя считать черновик/чат опубликованным результатом |
| Штатное распределение требует сформированного объекта, но часть карточек/списков может вызывать нумерацию при чтении | **К** | `assignment.py`, `ticket_list.py`, `routers/tickets.py`; **GET не всегда чисто read-only** |
| Точные DB-FK, CHECK, UNIQUE, частичные индексы и миграции на живой базе соответствуют модели | **НЕ ДОКАЗАНО** | Изучены ORM/DDL и миграции, **не** `pg_catalog` DEV/PROD |

**Для будущих патчей:** при изменении одного измерения нельзя автоматически перезаписывать другие. Например, `status=closed` и `intake_state=cancelled` возникают в отдельных контекстах; `is_frozen` не является самостоятельным `status`.

## 3. Права, чтение и неизменность закрытых объектов

### 3.1. Серверные ограничения

1. **К+Т — закрытый объект только для чтения в штатных staff-операциях над сообщениями:** `reply`, `comment`, `edit`, `delete` возвращают ошибку `ticket_closed_readonly` для закрытого `Ticket`. Проверка — `test_text_channel_preserves_access_checks` для WEB/Telegram.
2. **К+Т — роли не взаимозаменяемы:** `chiki_piki` имеет read-only доступ по направлению «Колл Центр» и не получает права на отправку/редактирование; для операций проверяется доступность объекта и assignee. Тесты outbound покрывают роль и назначение на открытом объекте.
3. **К+Т — group parent скрыт от не-admin:** фильтрация в `list_tickets_paginated`, страницах, счётчиках, `task-panel`, карточке по ID/номеру и перечисленных вложенных read-маршрутах. Это проверяется `test_group_parent_visibility.py` на SQLite; PostgreSQL-параметр теста имеет `skip`, если не задан `TICKET_SEARCH_TEST_DATABASE_URL`.
4. **К — широкая видимость закрытых:** исследованные `closed`-списки для `agent/trainee` допускают чтение шире собственного назначения; `_get_ticket_or_404` не повторяет проверку assignee для closed. Это **факт реализации, а не подтверждённое бизнес-требование**. Возможность читать чужой закрытый объект нельзя путать с правом его менять.
5. **К — фильтры `chiki_piki` различаются:** в списке и прямой карточке условия привязки к направлению не полностью идентичны. Отсутствие обходных read-путей для реальных конфигураций **не доказано**.

### 3.2. Безопасность внешнего результата WEB

**К+Т.** `WebTaskResult` по API выдаётся только авторизованной WEB-сессии соответствующего клиента; другой клиент той же компании, клиент иной компании и аноним не получают результат/скачивание. В `test_other_clients_and_anonymous_cannot_read_or_download` ожидаются `404` для чужих и `401` для анонимов. `test_attachment_id_must_belong_to_this_publication` проверяет привязку файла к нужному `task_id`. Параметры `client_id`/`company_id` в списке не должны позволять сменить сессию (`test_read_count_and_idempotent_read_are_scoped_to_session`).

**Граница:** это защита в прочитанных WEB-result маршрутах и выбранных тестах, не независимый security-аудит всех HTTP-маршрутов и файлов хранилища.

## 4. Назначение, очередь и рабочие интервалы

| Правило | Доказательность | Подробности |
|---|---|---|
| Auto-assignment обычного оформленного объекта выбирает подходящего активного `online` агента с минимальной нагрузкой | **К** | `assignment.py`; tie-break по последнему назначению/имени; не распространять на все `auto` и особые объекты |
| Forced assignment и ручное admin assignment имеют другие условия | **К** | Forced может выбрать `agent/trainee` без требования `online`; ручное — активного `admin/agent/trainee` |
| Особый (`is_special`) объект не следует оставлять без административного ответственного; передача агенту снимает special-контур | **К** | `ticket_special.py` |
| Смена ответственного/заморозка/закрытие обновляет `StatsWorkInterval` | **К** | `statistics_lifecycle.py` и вызовы lifecycle; агрегат и выдачу фактических дашбордов в рамках шага не запускали |
| Точный порядок фоновых уведомлений и назначений гарантирован при одновременных workers | **НЕ ДОКАЗАНО** | Отдельные локальные guards/очереди не равны глобальной сериализации |

Переход `on_ticket` может возникать **не только через кнопку**: **К+Т** — текст «иду к вам» при обычном `ticket` переводит сотрудника в этот статус и закрывает предыдущий интервал доступности (`test_plain_ticket_onsite_reply_records_agent_on_ticket_status_history`), а ручная фраза не расходует quick-action `onsite` (`test_manual_onsite_text_triggers_onsite_without_spending_quick_button`). При restore флаг onsite сбрасывается (`test_restore_resets_onsite_and_new_onsite_text_can_trigger_again`).

## 5. Сообщения: что клиенту видно, а что нет

### 5.1. Сохранение и доставка

| Сценарий | Фактическое поведение | Доказательность |
|---|---|---|
| Текстовый ответ сотрудника в обычной Telegram-заявке | Вызов отправки Telegram предшествует локальному добавлению `TicketMessage`/commit; ID Telegram записывается в БД | **К+Т** — `ticket_messages.py`, `test_text_channel_reply_edit_delete`, `test_plain_ticket_text_reply_persists_card_message_and_starts_auto_close_timer` |
| Текстовый ответ в обычном WEB `ticket` через staff-контур | Сохраняется в локальном `TicketMessage`, Telegram клиенту не вызывается | **К+Т** — text-channel тест |
| Ответ сотрудника в `task/normal` | Сохраняется до закрытия; клиенту выдаётся **при завершении**, а не немедленно по reply | **К+Т** — `test_channel_file_archiving_reply_and_task_completion`, `test_web_task_results.py` |
| `comment` | `message_type=internal`, клиенту Telegram не доставляется и не замещает необходимый `agent`-ответ на закрытие задачи | **К+Т** — `test_text_channel_internal_comment_does_not_deliver`, `test_internal_comment_cannot_replace_required_agent_answer` |
| Изменение staff-сообщения | Только `agent/internal`, не deleted, с проверкой автора/админа и незакрытого Ticket; для внешнего Telegram сначала редактирование API, потом обновление DB | **К+Т** — text-channel edit тесты; для Telegram нужен прежний Telegram message ID |
| Удаление staff-сообщения | Это **soft delete** по `message_status=deleted`, не физическое удаление строки; Telegram API delete вызывается отдельно | **К+Т** — `test_text_channel_reply_edit_delete`, `test_delete_agent_message_deduplicates_identical_telegram_ids` |

Здесь «ответ существует» не равно «ответ дошёл до клиента»: `TicketMessage` и `TicketAttachment` сохраняют локальные статусы, а Telegram — внешний необратимый эффект. Если API уже выполнил действие, но DB commit не прошёл, общий rollback не откатывает Telegram. См. раздел 11.

### 5.2. Быстрые действия и черновики

- **К+Т:** `quick_action=clarification` и `onsite` относятся к обычному внешнему ответу в `ticket`; для повторного действия действует одноразовое состояние `quick_action_state_json`.
- **К+Т:** перед `clarification` требуется использованный `greeting`; отказ происходит **до отправки** и не меняет состояние (`test_quick_clarification_requires_greeting_and_does_not_send_on_failure`).
- **К+Т:** одно и то же быстрое действие нельзя использовать дважды (`test_quick_clarification_marks_action_only_after_success_and_repeat_is_rejected`). Перед отправкой сервис повторно считывает состояние после блокировки Ticket: устаревшее значение из памяти не обходит запрет (`test_quick_clarification_rechecks_used_state_from_database_before_sending`). **Но** SQLite-тест не доказывает поведение двух реальных PostgreSQL-транзакций при гонке.
- **К+Т:** quick-action не должен стирать набираемый вручную черновик (`test_quick_clarification_preserves_saved_draft`). Обычные успешные reply/comment очищают черновик и увеличивают `draft_revision`; неуспешные — сохраняют, что проверяется отдельными outbound тестами.
- **К:** ручной текст «иду к вам» активирует бизнес-флаг `onsite_triggered` даже без `quick_action=onsite`; отличие намеренно проверяется тестом.

## 6. Файлы: предварительная загрузка, архив и финальная отправка

Файловый контур требует особенно осторожных формулировок: **сохранён локальный файл**, **загружен в Telegram staging**, **есть `telegram_file_id`**, **отправлен клиенту** — четыре разных факта.

### 6.1. Подтверждённые локальные правила

1. **К+Т:** обычный `ticket` с raw-вложением создаёт `TicketMessage`, `TicketAttachment` в статусе `uploading` и планирует фоновую отправку **после сохранения**. Не обещать немедленную доставку при HTTP 200 (`test_reply_plain_ticket_attachment_saves_locally_marks_uploading_and_schedules_background`).
2. **К+Т:** при успешном staging/delivery статус файла становится `sent`; повторный вызов worker при зафиксированном результате не должен повторно отправлять файл (`test_channel_file_archiving_reply_and_task_completion`, `test_preload_worker_skips_duplicate_final_send_when_attachment_already_sent`). Это **локальная защита повторов**, не доказательство глобального exactly-once во всех авариях.
3. **К+Т:** если сохранение staging-метаданных в БД завершилось ошибкой, локальный файл сохраняют и не удаляют (`test_background_delivery_commit_failure_keeps_local_file_and_skips_unlink`). После ошибки финальной доставки статус переходит в `failed` и добавляется `system`-заметка (`test_background_delivery_failure_marks_failed_and_records_note`).
4. **К+Т:** удалённое до завершения worker сообщение не должно доставляться; вложение переходит в `failed` (`test_deleted_message_while_background_pending_is_not_delivered`).
5. **К+Т:** `task` может связать сразу несколько готовых preload-файлов с **одним** агентским ответом; если preload ещё `uploading`, claim сохраняет привязку к ответу, worker завершает подготовку отдельно (`test_task_reply_claims_multiple_ready_preloads_into_one_agent_message`, `test_task_async_preloads_keep_one_reply_across_staging_race`).
6. **К+Т:** удаление unclaimed preload возможно, а удаление уже claimed — запрещено; cancelled-in-flight требует очистки после позднего завершения (`test_remove_claimed_preload_is_rejected_and_preserves_sent_attachment`, `test_cancelled_uploading_preload_cleans_late_staged_telegram_message`). Фоновая stale-cleanup оставляет claimed, удаляя просроченные unclaimed (`test_stale_preload_cleanup_removes_old_unclaimed_and_keeps_claimed`).
7. **К+Т:** для `comment` raw-файл непосредственно в ответе отклоняется; допустим заранее готовый preload, который остаётся внутренним и не отправляется клиенту (`test_internal_comment_raw_file_is_rejected_without_final_attachment`, `test_internal_comment_ready_preload_is_claimed_without_client_delivery`).
8. **К+Т:** для WEB `task` raw-файл при staff reply проходит Telegram **архив**. Если архив недоступен, `reply` отклоняется `502`, локальный `TicketMessage`/attachment не фиксируются в БД после rollback; локальный файл может остаться (`test_web_task_raw_archive_failure_does_not_persist_an_unarchived_answer`). Это **архивирование**, не отправка результата WEB-клиенту.

### 6.2. Что нельзя обещать

- **НЕ ДОКАЗАНО:** абсолютное отсутствие двойного внешнего Telegram-сообщения после crash/retry между успешным API send и DB commit. Идемпотентность worker проверяется для выбранных сохранённых состояний, не для всего множества аварий.
- **НЕ ДОКАЗАНО:** отсутствие временно осиротевших staging-сообщений/локальных файлов при сбоях. Есть очистки и компенсации, но DB + файл + Telegram нельзя объединить одним транзакционным commit.
- **НЕ ДОКАЗАНО:** гарантированное восстановление `uncertain`/`failed` без оператора для каждого вида вложения. Повтор без проверки состояния может создать дубли; политика ручного retry — отдельный контракт.
- **НЕ ДОКАЗАНО:** все сочетания Telegram альбомов, группового parent, скачивания после рестарта и race между delete/close/deliver. Выбранные тесты детальны, но не равны интеграционному полному покрытию.

## 7. Закрытие сотрудником: обязательные условия и варианты

### 7.1. Обычное `POST /{ticket_id}/close`

| Вид объекта | Предусловие / результат | Доказательность |
|---|---|---|
| Обычный `ticket` | Для штатного close нужен существующий активный `message_type=agent` (не soft-deleted); без него `409`. При закрытии фиксируются `closed_at`, `closed_by_user_id`, интервал работы, очищается auto-timer | **К+Т** — `test_ticket_lifecycle_close.py` |
| `task/normal` | Кроме активного агентского ответа нужен исходный текст клиента; активные загрузки блокируют закрытие. Итог Telegram передаётся при закрытии; WEB публикуется отдельным result | **К+Т** — lifecycle и `test_web_task_results.py` |
| `task/group_child` | Закрытие может передать текст/вложения последнего активного ответа в parent. Флаг `suppress_client_answer` исключает из результата группы; parent может закрыться автоматически, если детей с незакрытым status не осталось | **К+Т** — group-child тесты в `test_ticket_lifecycle_close.py` |
| `task/group_parent`, *автоматический finalizer* | `finalize_group_parent_if_ready` сначала `flush`, потом проверяет отсутствие children со `status != closed`; закрывает parent после окончания всех детей | **К** — `ticket_lifecycle.py`; sibling-кейсы проверяются lifecycle-тестами |
| `task/group_parent`, *прямой staff close* | Отдельная ветвь `close_ticket_by_actor` собирает child payload, но **не вызывает аналогичный guard «все children закрыты»** перед переходом parent в closed | **КОНТРАКТ?** — реальный код; нет найденного теста «ручной parent close при открытом child» |
| Внутренняя task | Агентский результат не публикуется WEB/Telegram-клиенту, планируется внутреннее уведомление | **К+Т** — `test_internal_task_keeps_existing_completion_without_client_publication` |
| `auto` | Не наследует автоматически правила ordinary `ticket`/`task`; для admin force есть отдельная ветвь | **К** — `ticket_lifecycle.py` |

**К+Т:** повторное закрытие уже закрытого объекта возвращает результат `already_closed` на уровне lifecycle; вызывающий HTTP-роут может отобразить отдельный код. `test_completion_first_close_and_repeat_close_keep_single_completed_row` проверяет отсутствие второй записи `StatsCompletion` при повторе на уровне сервиса.

### 7.2. Административное принудительное закрытие

**К+Т.** Admin с явным force может завершить оформленный объект **без активного агентского ответа**. `skip_client_notification` учитывается только как часть такого force-action, а не как универсальный флаг для агента. Наличие ответа меняет выбор ветви; если в `task` нет исходного клиентского сообщения, применяются отдельные правила без ответа. Неоформленный Telegram `ticket`, обычный `task` или `group_parent` могут попасть под узкие предикаты **hard delete** вместо обычного `status=closed`.

**КОНТРАКТ?:** статус `closed` и факт зачтённой работы — не одно и то же. Force без клиента видимого ответа может создать завершение без атрибуции сотруднику (`completed_by_user_id=null`), что подтверждено тестами. Это необходимо сохранить при изменении статистики.

### 7.3. Нельзя объединять все закрытия в одну операцию

Обычное staff close, admin force, auto-close по таймеру, Telegram `/close`/callback и hard delete имеют **разные** предусловия, поля, side effects и синхронизацию статистики. Таблица в разделе 12 фиксирует существенные отличия. В будущих патчах нельзя «упростить» их общим вызовом без явной матрицы обратной совместимости.

## 8. Закрытие клиентом Telegram

**К+Т — доступность закрытия зависит от класса обращения:**

- Обычный `ticket/normal` можно клиентски закрыть, пока он не `closed`, **даже после ответа сотрудника**, независимо от наличия assignee. Это подтверждается параметризованным `test_telegram_client_close.py`.
- `task/normal` можно клиентски отменить только пока `intake_state=collecting`. После перехода `ready` кнопка должна сниматься, даже если назначение не удалось; тест проверяет это отдельно.
- Group-task (не normal) и `auto` допускают клиентское закрытие по особым условиям отсутствия assignee. Fallback для иных типов использует наличие агентского сообщения; **сам `has_agent_message` не отсекает soft-deleted**, поэтому нельзя распространять это условие на все типы как «активный ответ».
- Предварительная Telegram-заявка/незавершённый normal/group-parent task удаляется **физически** по узким предикатам. Повторный callback не должен удалять чужой новый draft.
- Для inline-кнопки проверяется, что `callback.message_id == ticket.client_close_message_id`; устаревшая кнопка отклоняется. Создаётся запись `TelegramUpdate`.

**К:** оформленная заявка при client close получает `status=closed`, `closed_at`, сброс freeze и закрытие work interval; если ещё `collecting`, intake становится `cancelled`. В прочитанных `close_active_ticket_for_contact` и `process_client_close_callback` **нет прямого вызова `sync_completion_after_close`/`_mark_ticket_completed`**, а `closed_by_user_id` не проставляется идентификатором сотрудника. Не трактовать такое закрытие как сотрудническое выполнение без проверки аналитики.

**К:** callback фиксирует изменение/удаление в БД **до** post-close Telegram send и review UI. После успешного commit сетевая ошибка не может вернуть исходную заявку сама по себе. Telegram `send_client_close_notification` выбирает отдельный текст для обычного неназначенного ticket; для остальных пытается подготовить post-close действия и review.

## 9. Автозакрытие обычной заявки через 20 минут

**К+Т.** `auto_close.py` применяет таймер **20 минут** только к ordinary `workflow_type=ticket` со статусом `new/pending`, оформленному (не `collecting`), без freeze, special и `auto_close_disabled`. Таймер выставляется, когда последнее `client`/`agent` сообщение принадлежит **агенту**; если последним является клиент — таймер очищается. Тест `test_plain_ticket_text_reply_persists_card_message_and_starts_auto_close_timer` проверяет запуск на обычный ответ, `test_auto_close.py` — закрытие due, отсутствие повтора, отказ Telegram и отсутствие контакта.

`cleanup_auto_close_tickets` ставит `status=closed`, `closed_at`, `auto_closed_at`, `closed_by_user_id=null`, очищает таймер и work interval, записывает системное сообщение; для Telegram пытается отправить уведомление **до commit**, но сетевая ошибка приводит к дополнительному system-сообщению и всё равно допускает закрытие. Для WEB этот Telegram send пропускается. **Нет прямого вызова** `_mark_ticket_completed` / `sync_completion_after_close` в этом сервисе (**К**); это отдельная ветвь от обычного сотруднического закрытия.

**Открытые расхождения/ограничения:**

1. **КОНТРАКТ?:** `_is_ticket_auto_close_scope` трактует `intake_state=null` как `ready`, а SQL-фильтр cleanup содержит `Ticket.intake_state != 'collecting'`; в SQL `NULL != значение` не истинно, так что объект с NULL может получить timer, но **не попасть в выборку cleanup**. Уточнить, допустимы ли такие реальные строки и требуется ли единый фильтр.
2. **КОНТРАКТ?:** `_last_client_visible_message` отбирает `message_type in (client, agent)` без фильтра `message_status != 'deleted'`. После soft-delete последнего agent-сообщения timer может опираться на удалённое сообщение. Нет найденного теста именно этой комбинации.
3. **НЕ ДОКАЗАНО:** нет прочитанной гарантии уникального конкурентного захвата due-объекта (например, `FOR UPDATE SKIP LOCKED`); обычный `select` не доказывает отсутствие повторной обработки при двух scheduler workers. Обозначить как гипотезу для проверки, а не установленный дубль.
4. **НЕ ДОКАЗАНО:** поведение статистики дашборда при auto/client close без прямой синхронизации `StatsCompletion`, включая последующие restore/delete. Нужны точечные тесты/SQL с известной ожидаемой метрикой.

## 10. Публикация результата WEB-задачи

### 10.1. Что защищено кодом и выбранными тестами

- **К+Т:** публикуется результат только для штатного `channel=web`, `workflow_type=task`, `task_kind=normal`, закрываемого в нужной ветви с несупрессированным клиентским ответом. Другие каналы/виды/состояния не должны создавать `WebTaskResult` (`test_publisher_excludes_other_scenarios`).
- **К+Т:** берётся **последний активный `agent`-ответ**, а не internal/system/deleted и не более ранний ответ. Если в последнем ответе только файл, допустим `text=''`. Отдельные тесты: `test_last_agent_only_and_immutable_snapshot_survives_connection_restart`, `test_files_only_answer`.
- **К+Т:** результат — самостоятельный snapshot текста, файлов и байтов. Изменение или удаление исходного `TicketMessage`/`TicketAttachment` и удаление локального файла **после публикации** не меняет опубликованный результат; тест повторно читает его после dispose соединения.
- **К+Т:** `publish_result` и bytes файлов входят в транзакцию закрытия: rollback убирает и publication, и blob, и возвращает статус задачи; отсутствие исходного файла при закрытии даёт `409` без утечки пути/внутренних ошибок (`test_rollback_removes_publication_and_blob_together`, `test_missing_file_rolls_back_close_without_exposing_storage`).
- **К+Т:** до закрытия API не показывает result/непрочитанное. После публикации чтение/отметка прочтения ограничены клиентской сессией; повторный `read` идемпотентен, `read_at` не перезаписывается.
- **К+Т:** force/suppress без клиентской публикации не должен создавать фиктивный result (`test_suppressed_and_forced_without_answer_never_invent_results`). Внутренняя задача не становится внешним WEB-result.
- **К+Т:** создание таблиц `web_task_results` и `web_task_result_files` при schema-evolution аддитивно и идемпотентно на изолированном тестовом engine; **не заменяет** проверку реальной DEV-схемы.

### 10.2. Граница «неизменяемости»

**К.** Snapshot защищён от последующего редактирования/удаления **исходного сообщения**, но не является вечным архивом: `POST /admin/delete` **явно удаляет** `WebTaskResultFile` и `WebTaskResult` вместе с Ticket (см. `ticket_admin_actions.py`, `routers/tickets.py`). Следовательно, утверждение «результат переживает любое удаление Ticket» **неверно**. В `DATABASE.md` v0.14 эта разница уже отражена.

**НЕ ДОКАЗАНО:** политика нормативного хранения/retention, восстановление snapshot из backup и строгий предел размера опубликованных bytes под реальной нагрузкой.

## 11. Hard delete, restore и внешние эффекты

### 11.1. Hard delete

**К+Т.** Admin `POST /admin/delete` — отдельная необратимая для рабочего UI операция. Код удаляет связанные сообщения, файлы/предзагрузки, вложенные связи, review/feedback, view и WEB snapshots, отвязывает относящиеся к Ticket integration log поля; физические локальные файлы тоже очищаются в штатной ветви. `test_admin_delete_regression.py` проверяет множество FK-связей, физические файлы, parent/child bulk, уведомления, commit rollback и ошибку Telegram **после** DB deletion. Тест **не создаёт отдельно опубликованный `WebTaskResult`** — его удаление подтверждено кодом, но не специализированным assert данного тестового файла.

Смысл транзакционной границы: Telegram уведомления о hard delete собираются до выполнения, а доставляются **best-effort после успешного commit**; при rollback до commit их не отправляют. Ошибка Telegram после commit **не восстанавливает** удалённую запись. При bulk parent+child защищают от очевидного повторного уведомления на один объект. Тесты используют SQLite с FK ON; optional PostgreSQL-параметры **скипаются**, если окружение не предоставлено.

**НЕ ДОКАЗАНО:** hard delete без следов во всех внешних системах (staging Telegram, внешние интеграции, резервные копии). DB deletion не отменяет ранее отправленные уведомления и сторонние копии.

### 11.2. Восстановление `POST /restore`

**К+Т.** Штатный staff restore ограничен закрытым ordinary `workflow_type=ticket`, выполняется администратором или допустимым исходным закрывшим сотрудником и проверяет конкурирующее активное обращение клиента. Он сбрасывает close/freeze/completion, обновляет рабочие интервалы и состояния отзывов; `task` и `auto` не становятся восстанавливаемыми через этот endpoint. `test_completion_reopen_clears_same_row_and_reclose_updates_to_new_agent` проверяет, что сохранённая строка `StatsCompletion` переводится в `is_completed=false`, а повторное закрытие вновь использует **ту же строку**.

**Граница:** WEB `WebTaskResult` имеет собственное хранилище, но штатный restore относится к ordinary ticket, а не к task. Нельзя обещать «откат результата WEB task» через staff restore.

## 12. Завершение и статистика: сравнение путей

### 12.1. Как заполняется `StatsCompletion`

**К+Т.** Для стандартной ветви `close_ticket_by_actor` и `sync_completion_after_close`:

- `ticket` и `task/normal`, `task/group_child` допускают отдельную запись `StatsCompletion`; `task/group_parent`, `auto` и неизвестный `task_kind` **исключены whitelist** (`test_completion_object_identity_whitelist`). При `task_kind=null` нормализуется `normal` для `task`.
- `is_completed=true` и `completed_at=closed_at` возможны **без** `completed_by_user_id`, если однозначная атрибуция не найдена (`test_completion_with_unresolved_attribution_still_marks_object_completed`). Поэтому подсчёт «выполнено» и метрика «кто выполнил» должны различаться.
- При admin close привязка к ответившему/назначенному сотруднику разрешается существующей функцией attribution, а не автоматически к admin; force без реального клиентски видимого ответа может оставить attribution null (`test_admin_close_uses_existing_resolver_attribution` и lifecycle-тесты).
- Повторный close не плодит новую completed-строку; reopen сбрасывает **ту же** строку, reclose может установить другого исполнителя (`test_completion_first_close_and_repeat_close_keep_single_completed_row`, `test_completion_reopen_clears_same_row_and_reclose_updates_to_new_agent`).
- Историческая запись `StatsCompletion` хранит строковые `object_id` и `completed_by_user_id` и в проверенном изолированном тесте переживает физическое удаление Ticket и User (`test_completion_survives_source_ticket_and_user_delete`). Это исторический результат, **не** гарантия сохранения любых других таблиц после hard delete.

**К+Т:** прямое закрытие `task/group_parent` **не** создаёт отдельный `StatsCompletion` и не устанавливает legacy-поля `completed_by_user_id`/`completed_counted_at` (`test_real_group_parent_close_does_not_create_completion_or_legacy_completion`). Автоматический parent finalizer также не содержит `_mark_ticket_completed`. Это наблюдаемая особенность, отдельно от вопроса раннего закрытия parent.

### 12.2. Матрица важных переходов

| Путь | Предусловие | DB-эффект | Клиентский результат и граница |
|---|---|---|---|
| Staff ordinary `ticket` close | Активный агентский ответ или admin force | `closed`, interval, timer clear, `StatsCompletion` по whitelist | Telegram уведомление/review в вызываемой ветви; внешний side effect не атомарен |
| Staff `task/normal` close | Клиентский текст + agent reply, нет незаконченной загрузки; есть force-ветви | `closed`, interval, `StatsCompletion` | Telegram итог при close **или** WEB snapshot; internal task — внутренний канал |
| Staff `group_child` close | Соответствующий ответ/force | `closed`, group aggregation, `StatsCompletion` | Содержимое через parent, не обязательно прямое уведомление child |
| Auto finalizer parent | Все children `closed` | `parent=closed`, interval, **без StatsCompletion parent** | Агрегированный ответ parent |
| Прямой staff close parent | Нужный агентский ответ/force и текст клиента в обычной ветви | `parent=closed`, **без StatsCompletion parent** | Собирает уже доступные child answers; guard всех children в этой ветви не показан |
| Telegram client `/close`/button | По типу обращения/стадии и current button | Hard delete draft **или** `closed`, interval | Для callback отправка подтверждения **после DB commit**; в прочитанных функциях нет stats completion |
| Timer auto-close | Due ordinary unfrozen/unspecial/eligible ticket | `closed`, `auto_closed_at`, interval; stats completion не синхронизируется здесь | Telegram send до commit, ошибка фиксируется note, закрытие продолжается |
| Admin hard delete | Admin, явно выбранные объекты | Физическое удаление Ticket/части связей/WEB snapshot, локальных файлов | Telegram notice best-effort **после commit** |
| Restore ordinary ticket | Closed `ticket`, authorized, нет конфликтного active | Open + сброс завершения / восстановление интервала | Уведомление best-effort; не restore task/auto |

### 12.3. Не приравнивать статусы и показатели

`status=closed`, `closed_at`, `completed_counted_at`, `StatsCompletion.is_completed`, `StatsWorkInterval.ended_at`, `auto_closed_at` — не синонимы. Текущий код заполняет их **разными путями**. При создании аналитики с формулой «все closed = выполнены конкретным агентом» неизбежна логическая потеря различий: client close и timer auto-close не проходят тот же синхронизатор, а force может не иметь автора.

## 13. Внешняя атомарность и риски конкурентности

### 13.1. Порядок операций различен

| Операция | Зафиксированный порядок в коде | Что происходит при отказе |
|---|---|---|
| Обычный Telegram text reply | Telegram API send → добавление локального message/commit | Сеть может успеть раньше DB; rollback не удаляет Telegram автоматически |
| Файловый reply обычного ticket | Локальный message/attachment и commit → background staging/final send | Есть `uploading/failed`, note, компенсации; некоторые crash-окна остаются |
| Staff close с Telegram notify | Вызовы клиентской отправки внутри ветви close до вызывающего commit | Клиент может увидеть сообщение раньше успешного commit |
| Telegram client close callback | DB commit → callback answer/keyboard remove/send close | Запись может закрыться, даже если Telegram подтверждение не пришло |
| Auto-close cleanup | Смена DB-полей и Telegram send → DB commit | Ошибка сети записывается в system note; commit всё равно возможен |
| Admin hard delete | DB delete/commit → отложенные Telegram notices | При ошибке сети DB delete уже не откатывается |
| WEB task result close | Байт-снимок и `closed` в одной DB-транзакции | Rollback удаляет публикацию и возвращает состояние задачи |

Таблица описывает **порядок прочитанного кода**, а не гарантированное поведение внешних API под нагрузкой.

### 13.2. Реестр открытых технических и продуктовых вопросов

| ID | Вопрос | Основание | Как отдельно подтвердить, не меняя код |
|---|---|---|---|
| **I-01** | Можно ли закрыть group parent вручную при открытом child и должно ли это запрещаться? | Direct close parent не вызывает sibling guard; auto-finalizer вызывает | Точный интеграционный тест с parent + open child: staff `/close`, статус child/parent, payload клиенту; затем продуктовое решение |
| **I-02** | Должны ли agent/trainee видеть **чужие closed** объекты? | `closed` list и card обходят фильтр назначения | Согласовать матрицу прав; тестировать list/card/attachments/search одним актором на чужих closed |
| **I-03** | Эквивалентны ли list/card правила `chiki_piki` для разных конфигураций кнопок? | Разные предикаты в `ticket_list.py` и `tickets.py` | Синтетический набор ключей и direct URL, проверка leak/no leak |
| **I-04** | Может ли `intake_state=null` зависнуть с выставленным auto timer? | Python scope coalesce NULL, SQL cleanup `!= collecting` | Чистый SQLite/PostgreSQL тест NULL в timer/cleanup; оценить реальные данные read-only |
| **I-05** | Может ли soft-deleted agent reply быть последним для auto timer? | `_last_client_visible_message` не фильтрует deleted | Тест reply → timer → soft delete → refresh → cleanup; решить желаемую семантику |
| **I-06** | Как должны учитываться в статистике закрытия клиентом и таймером? | Нет прямой синхронизации `StatsCompletion` в этих двух сервисах | Утвердить KPI; точные DB-тесты до/после client close / auto close / reopen |
| **I-07** | Нет ли двойной внешней отправки при crash между Telegram send и commit? | Network и DB не атомарны; разные порядки | Fault injection на staging/final-send/commit и уникальные идентификаторы, без PROD |
| **I-08** | Сериализуются ли concurrent close/restore/hard delete и due scheduler workers? | Нет end-to-end concurrency проверки, в auto cleanup нет явного row lock | PostgreSQL transaction tests двух сессий, фиксированные барьеры, повторные операции |
| **I-09** | Все ли WEB result bytes и файлы очищаются по retention/backup-политике? | Snapshot независим от исходных файлов, но admin delete его удаляет | Проверка согласованной retention-policy, read-only инвентаризация БД/хранилища |
| **I-10** | Полон ли lifecycle internal task scheduler / notification backlog? | В шаге 42 целиком прочитаны только три новых сервиса, `internal_tasks.py` и scheduler ещё не включены | Отдельный адресный исследовательский проход, без смешивания с уже доказанными staff guards |

**Приоритет:** I-01, I-02, I-04–I-08 требуют определения ожидаемого контракта **до** разработки исправлений. Само присутствие в таблице — **не указание Codex чинить**. Пограничные сценарии изоляции и двойной доставки должны проверяться на изолированном DEV/test окружении; реальный PROD не является испытательным стендом.

## 14. Карта тестов: что охвачено и что не проверено

| Тестовый файл | Прочитанная область доказательств | Важная граница |
|---|---|---|
| `tests/test_auto_close.py` | Due, повторный запуск, нет Telegram-контакта, ошибка Telegram | Нет отдельного NULL/deleted/concurrent/stats-сценария |
| `tests/test_telegram_client_close.py` | `/close`, кнопка, stale callback, normal task ready, обычный ticket после агентского ответа | Не доказывает статистику и все гонки новых/старых callback |
| `tests/test_ticket_lifecycle_close.py` | Normal/forced task/ticket, group-child aggregation, suppressed results, parent finalization, completed attribution | Нет обнаруженного прямого кейса staff parent close **при ещё открытом** child |
| `tests/test_admin_delete_regression.py` | FK и cleanup связанных записей, local files, rollback, post-commit notification, bulk parent+child | SQLite FK ON; PostgreSQL optional; WEB snapshot deletion явен в коде, отдельный seed/assert не найден |
| `tests/test_statistics_completion_lifecycle.py` | Exactly one stats row per identity в проверенных последовательных вызовах, reopen, attribution, parent/auto whitelist, survival delete | Нет concurrency гарантии и проверки client/auto close на статистику |
| `tests/test_group_parent_visibility.py` | Все вкладки, поиск/счётчики, карточка по ID/номеру, task-panel и названные read nested paths | PostgreSQL случай пропускается без отдельного test DB URL; не аудит всех путей |
| `tests/test_web_task_results.py` | Snapshot, bytes и rollback, security чтения, read idempotence, missing file, schema evolution | Изолированный engine и mocked Telegram; не live-доставка и не реальная миграция |
| `tests/test_ticket_messages_outbound.py` | Текст WEB/Telegram, права/edit/soft-delete, draft, quick actions, upload/preload/staging/final delivery/race-кейсы | Много статических/UI-контрактов и mock workers; не полная внешняя атомарность |

Часть тестов параметризована по SQLite/PostgreSQL, но PostgreSQL запускается только при наличии отдельной переменной окружения. **Ни один из этих тестов в шаге 42 не был запущен**; это обзор реализации их ожиданий. Не писать в статусах «все тесты зелёные».

## 15. Правила изменения этих инвариантов в будущем

1. Сначала сформулировать изменяемый продуктовый контракт **по одному сценарию**, определить затронутые `workflow_type`, `task_kind`, `channel`, `status`, `intake_state`, роли.
2. Сверить контракты сразу в `WORKFLOWS.md`, `INVARIANTS.md`, `DATABASE.md`, `TELEGRAM.md`/`WEB.md` **только если изменение реально относится к ним**. Документацию редактировать точечно, сохранять несвязанные разделы и исторические выводы.
3. Подготовить минимальный change request для Codex **только после решения пользователя**; данный документ сам по себе не даёт разрешение менять приложение.
4. После целевого diff проверить код, релевантные unit/DB tests и безопасный DEV smoke, отдельно от PROD. Не использовать summary Codex как доказательство.
5. Зафиксировать новую Git-ревизию, какие тесты **действительно выполнялись** и какой контракт стал подтверждённым, обновить `DOCUMENTATION_PROGRESS.md`; не переносить старое «не доказано» в «подтверждено» без новых данных.

**Стоп-точка шага 42:** `INVARIANTS.md` собран из фактического кода и восьми выбранных тестовых файлов. Для продолжения не нужны повторные выгрузки уже изученных файлов при том же `HEAD`. Дальше — согласование приоритетов открытых контрактов и, по решению пользователя, адресные проверки, а не автоматическое исправление перечисленных рисков.
