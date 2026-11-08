# ES DESK — карта подсистем

> **Версия 0.3 (2026-11-08), обзорная.** Исходная инвентаризация выполнена на `5d95903`; точечные дополнения по принятым коммитам `8aec22d` и `8439b32`. Навигационная карта, **не** исчерпывающее описание логики.

| Подсистема | Подтверждённые исходники | Установленный объём ответственности | Что ещё проверить |
|---|---|---|---|
| Запуск и инфраструктура API | `api/app/main.py`, `api/app/config.py`, `api/app/db.py`, Compose-файлы | Регистрация FastAPI, роутеров, БД, фоновых циклов | Полный порядок инициализации и конфигурации окружений |
| Сущности и схема | `api/app/models.py`, `api/app/services/schema_evolution.py` | ORM-модели; две отдельные таблицы отложенных Telegram-уведомлений, требующие явного `ensure_employee_notification_backlog_tables()` | Остальные FK/миграции, проверка PROD |
| Заявки и задачи | `api/app/routers/tickets.py`, `api/app/services/ticket_*`, `api/app/services/ticket_reading.py`, `api/app/services/ticket_list.py` | API карточки/списка, сообщения и вложения; подтверждение прочтения и подсчёт `unread_count` для ответственного агента | Детальные переходы и инварианты |
| Telegram (клиенты) | `api/app/routers/telegram.py`, `telegram_groups.py`, `api/app/services/telegram_*`, `api/app/services/assignment.py`, `api/app/services/ticket_numbering.py` | Подтверждены polling, dispatch update, базовое создание/распределение, `GROUP_PARENT`, часы приёма и безопасная очистка | Все ветки меню, файлов, автоматизаций, гарантий доставки и DB-only/TXT-совместимости; см. `TELEGRAM.md` |
| Пользователи и статусы | `api/app/routers/users.py`, `api/app/services/statistics_lifecycle.py`, `employee_auto_offline.py` | Пользователи, статусы, интервалы истории, плановый офлайн | Границы прав и влияния на назначения |
| Внутренние задачи / scheduler | `api/app/routers/internal_tasks.py`, `api/app/services/internal_task_scheduler.py` | Внутренние задачи и периодические задания | Полный перечень проходов и гарантии исполнения |
| Агентский UI | `api/app/templates/`, `api/app/static/app_shell.js`, `list.js`, `ticket.js`, `ticket_message_view.js`, `ticket_composer.js`, `settings_client_card.js`, `styles.css` | App Shell; пузырёк `+N`, положение чата по прочитанности, подтверждение фактически видимых сообщений; двухколоночные настройки списка карточки клиента | Карта всех page-init/cleanup и зависимостей JS |
| Отдельный WEB-клиент | `web-client/*`, `api/app/routers/web_*.py` | WEB-вход, меню, задачи, история, чат, результаты, файлы, черновики | Полные пользовательские сценарии и ограничения |
| Настройки и бот-кнопки | `api/app/routers/settings*.py`, `api/app/services/button_config.py` | Настройки, DB-модели меню и шаблонов | Фактическая логика и старые совместимости |
| Управление / отчёты | `api/app/routers/management_*.py`, `management_statistics.py` | Разделы управления, статистика, обратная связь | Формулы расчёта и видимость по ролям |
| Интеграции и уведомления | `api/app/routers/integrations.py`, `management_integrations.py`, `mass_notifications.py`, `employee_notification_preferences.py` | Внешние взаимодействия и уведомления | Контракты интеграций и логирование ошибок |
| Сотруднический Telegram-бот, накопитель | `api/app/services/employee_notification_backlog.py`, `employee_telegram_notifications.py`, `employee_telegram_bot.py`, `internal_task_scheduler.py` | Накопление после рабочего времени в PostgreSQL; предложение утром, выдача порциями по 5, callback и очистка | Живой утренний сценарий и гарантии доставки |
| Отзывы, ответы и файлы | `api/app/routers/reviews.py`, `quick_replies.py`, `management_saved_files.py` | Соответствующие API-разделы | Семантика и связи с основными процессами |
| Тестирование | `tests/` | Наборы проверок API и UI (233 отслеживаемых файла в инвентаре) | Сопоставить тесты с подсистемами |

## Проверенные ссылки между модулями

- `api/app/main.py` регистрирует роутеры и запускает фоновые циклы.
- FastAPI отдаёт агентские шаблоны и статику; `app_shell.js` управляет частичной навигацией между зарегистрированными страницами.
- `web-client/dev-server.mjs` содержит разрешённые пути WEB API, включая историю, чат, результаты и черновики.
- `button_config.py` и `telegram.py` обращаются к `BotButtonSetting`.
- `telegram_polling_loop()` вызывает `process_telegram_update()`, который разветвляет обработку `callback_query`, `message`, `edited_message` и некоторых групповых сценариев.
- `routers/telegram.py:_ready_ticket_for_distribution()` вызывает `ensure_ticket_number()` и использует `assignment.py`; в `assignment.py` вызывается hook для `GROUP_PARENT`, который регистрирует `routers/telegram.py`.
- `telegram_intake_hours.py` используется в клиентских сценариях, after-hours cleanup и при передаче автоматизации сотруднику.
- Клиентское меню удаляет устаревшие интерактивные сообщения и валидирует stale callback (`8aec22d`); механизм отложенной выдачи сотрудникам описан в `TELEGRAM.md` (`8439b32`).
- `ticket_list.py` → `ticket_reading.py` рассчитывает `unread_count`; `routers/tickets.py` отдаёт `unread_message_ids` и принимает `POST /api/v1/tickets/{id}/read`; `ticket_message_view.js` подтверждает фактически видимые сообщения через браузер.
- Подробности и границы проверок: [`TELEGRAM.md`](TELEGRAM.md).
- `internal_task_scheduler.py` подключает `employee_auto_offline.py` (просмотренный diff из коммита `e9e8852`).

## Принцип дальнейшего заполнения

Добавлять к каждой подсистеме не списки всех функций, а **точки входа → основные сервисы → таблицы/внешние зависимости → события/ограничения → тесты**. Если связь ещё не проверена кодом, помечать её «не проверено».