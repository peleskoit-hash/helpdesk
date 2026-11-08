# ES DESK — база данных и эволюция схемы

> **Статус:** исследовательская документация текущего кода, **v0.14** (полная схема — шаг 40; адресное уточнение о hard delete — шаг 41), 2026-11-08.  
> **Исходная ревизия Windows DEV:** `8439b32`.  
> **Область проверки:** `api/app/models.py` (целиком, 1099 строк), `api/app/services/schema_evolution.py` (целиком, 2232 строки), `api/app/db.py` (целиком), индекс вызовов в `api/app/main.py` и перечень миграционных файлов под `api/`; **дополнение шага 41:** полные `api/app/services/ticket_admin_actions.py` и bulk-delete маршрут `api/app/routers/tickets.py`.  
> **Граница достоверности:** ниже описаны **ORM-модели и код приведения схемы**, а не результат запроса к действующей БД. Проверка системных каталогов PostgreSQL, фактических данных, успешности миграций и DEV/PROD не выполнялась.

## 1. Основной вывод

В приложении определены **50 моделей SQLAlchemy**. Они покрывают рабочие заявки, Telegram и WEB-клиентов, сообщения и вложения, отзывы, меню бота, автоматизации, внутренние задачи, уведомления, статистику и интеграции. Доступ к БД организован через один набор `Base`/`engine`/`SessionLocal`, а значительная часть развития схемы реализована вручную в `schema_evolution.py` через функции `ensure_*`.

**Принципиально важно не путать три уровня:**

1. **Желаемая схема:** классы в `models.py` описывают предполагаемые столбцы, `ForeignKey`, уникальности, `CHECK` и индексы.
2. **Стартовое обеспечение:** `Base.metadata.create_all(...)` создаёт недостающие таблицы, а выбранные `ensure_*` дозаполняют/меняют часть существующих таблиц. `create_all` **не превращает старую таблицу автоматически в точную копию модели**.
3. **Отдельные переходы:** некоторые функции из `schema_evolution.py` **не входят в стартовый путь** и требуют явного применения при вводе соответствующей функции. Они изменяют владельца, nullable-столбцы, ограничения и целостность.

Отсюда правило эксплуатации: **отсутствие ошибки запуска API и совпадение ORM-класса не доказывают, что все FK/unique/CHECK/индексы реально присутствуют в действующей БД**. Перед опасной миграцией требуется независимое read-only сравнение реального DDL и данных с ожидаемыми инвариантами.

## 2. Доступ к БД и границы транзакций

| Механизм | Что установлено из кода | Что из этого не следует |
|---|---|---|
| `api/app/db.py` | `Base(DeclarativeBase)`, `create_engine(settings.database_url, future=True)`, `SessionLocal = sessionmaker(autocommit=False, autoflush=False)` | Нельзя по одному этому файлу утверждать способ управления конкурентной записью в каждом сервисе |
| `get_db()` | Создаёт сессию, отдаёт её вызывающему коду, в `finally` закрывает | Само получение сессии не вызывает `commit` бизнес-операции |
| `Base.metadata.create_all` | При старте создаёт модели, **кроме пяти исключённых таблиц**, перечисленных в §4 | Не выполняет полноценное изменение уже созданных таблиц |
| `ensure_*` | Часто отражает текущие столбцы и делает `ALTER TABLE`/индексы/исправление данных; операции в отдельных `engine.begin()` | Все функции не составляют единую транзакцию с общей точкой отката |
| Миграции с сервисной сессией | `migrate_legacy_bitrix_passwords` и `migrate_legacy_bot_button_pdf_saved_files` вызываются до `db.commit()` на старте | Это не свидетельство, что они уже успешно прошли во всех окружениях |

Преобладают UUID в `String(36)` как прикладные ключи; есть числовые счётчики, текстовые натуральные ключи и составные PK. Применяются `JSON`, обычный `Text`, `DateTime(timezone=True)`, бинарные данные и `Boolean`. На PostgreSQL ручные DDL нередко используют `JSONB`, в ORM — абстрактный `JSON`. В SQLite проверочные/тестовые ветви используют отдельные SQL и перестройку таблиц. Эквивалентность ограничений между диалектами **проверяется отдельно**, не предполагается автоматически.

## 3. Инвентарь таблиц: все 50 моделей

Названия — физические `__tablename__` из `models.py`; группировка тематическая, а не схема отдельных PostgreSQL schemas.

### 3.1. Организации, сотрудники, клиенты и WEB-доступ — 9

| Таблица | Роль |
|---|---|
| `companies` | Организация, верхний субъект привязки |
| `users` | Сотрудники, роли, статусы, предпочтения/учётные данные |
| `clients` | Клиенты организации |
| `client_profiles` | Профиль клиента, в том числе WEB-логин/пароль и верификация |
| `web_client_sessions` | WEB-сессии по хешу токена и срокам действия |
| `web_login_limits` | Ограничение попыток входа по паре «логин + IP» |
| `web_client_drafts` | Единственный синхронизируемый WEB-черновик текста клиента |
| `web_chat_messages` | Отдельный технический поток WEB-чата, не сообщения заявки |
| `ticket_number_counter` | Отдельный singleton-счётчик номеров заявок |

### 3.2. Заявки, сообщения, файлы, публикации — 9

| Таблица | Роль |
|---|---|
| `tickets` | Заявка/задача/автоматизация, статус, канал, исполнитель, intake и служебный JSON |
| `ticket_messages` | История общения по заявке: client/agent/internal/system |
| `ticket_attachments` | Файлы сообщений и квитанции Telegram/архива |
| `ticket_attachment_preloads` | Временные загрузки до привязки к сообщению/задаче |
| `web_task_results` | Публикуемый клиенту снимок завершённой WEB-задачи |
| `web_task_result_files` | Бинарные файлы опубликованного снимка |
| `ticket_views` | Сведения о просмотре заявок сотрудниками |
| `saved_files` | Загруженные файлы, используемые конфигурацией кнопок |
| `background_contours` | Сохранённые графические контуры/фоновые активы |

### 3.3. Оценки, замечания и быстрые ответы — 6

| Таблица | Роль |
|---|---|
| `ticket_review_cycles` | Цикл запроса отзыва об исполнителе |
| `ticket_reviews` | Версии отзывов, актуальность, оценка и снимки имён |
| `ticket_feedback` | Опубликованное клиентское замечание/предложение по заявке |
| `ticket_feedback_inputs` | Временное состояние сбора клиентского замечания |
| `quick_replies` | Шаблоны ответов сотрудника |
| `quick_reply_usage` | Использование шаблона конкретным сотрудником |

### 3.4. Telegram и уведомления — 10

| Таблица | Роль |
|---|---|
| `telegram_contacts` | Соответствие клиента и Telegram-идентичности внутри компании |
| `telegram_notification_accounts` | Учётная запись сотруднического уведомительного бота |
| `telegram_avatar_cleanup_state` | Контроль состояния фоновой очистки аватаров |
| `telegram_updates` | Учёт обработанных Telegram update |
| `pending_task_intents` | Ожидаемое создание задачи/намерение клиента |
| `employee_notification_periods` | Периоды накопления уведомлений сотрудника |
| `employee_notification_backlog` | Накопленные события уведомлений |
| `employee_notification_preferences` | Настройки уведомлений сотрудника |
| `telegram_automation_groups` | Зарегистрированные Telegram-группы для автоматизаций |
| `telegram_group_registration_codes` | Одноразовые/временные коды регистрации групп |

### 3.5. Внутренние задачи, статистика и интеграции — 6

| Таблица | Роль |
|---|---|
| `internal_task_definitions` | Описание немедленного, назначенного или повторяющегося задания |
| `internal_task_occurrences` | Экземпляр/плановый выпуск задания и связанная задача |
| `stats_completions` | Снимок учёта завершённого объекта для статистики |
| `stats_work_intervals` | Интервалы работы сотрудника |
| `stats_user_status_intervals` | Интервалы статусов сотрудника |
| `integration_logs` | Результаты интеграционных вызовов и событий |

### 3.6. Меню и конфигурация — 10

| Таблица | Роль |
|---|---|
| `bot_button_settings` | DB-only настройки и идентичность кнопок, автоматизации, инструкции, файлы |
| `bot_menu_level_layouts` | Раскладка кнопок уровня меню |
| `bot_menu_profiles` | Типы/профили меню |
| `bot_menu_destination_bindings` | Привязка назначения клиента к меню |
| `automatic_message_templates` | Системные шаблоны сообщений |
| `client_card_block_configs` | Настройки блоков карточки клиента |
| `client_card_field_configs` | Настройки полей карточки клиента |
| `client_card_rule_configs` | Правила карточки клиента |
| `client_card_source_configs` | Источники данных карточки клиента |
| `call_center_employee_resolver_state` | Персистентное состояние разрешения сотрудников КЦ |

**Контроль количества:** 9 + 9 + 6 + 10 + 6 + 10 = **50** моделей. `Base` — декларативная база, не отдельная доменная таблица.

## 4. Создание и обновление схемы при старте

В проверенном индексе `api/app/main.py` входной блок сначала вызывает:

```python
Base.metadata.create_all(
    bind=engine,
    tables=[
        table for table in Base.metadata.sorted_tables
        if table.name not in {
            "web_chat_messages",
            "ticket_number_counter",
            "web_client_drafts",
            "employee_notification_periods",
            "employee_notification_backlog",
        }
    ],
)
```

Затем в исследованном порядке идут вызовы:

1. `ensure_client_profile_columns` → `ensure_telegram_contact_columns` → `ensure_telegram_avatar_cleanup_state_table` → `ensure_telegram_contact_company_user_unique` → `ensure_telegram_notification_accounts_table` → `ensure_user_columns`.
2. `ensure_ticket_columns` → `ensure_ticket_message_columns` → `ensure_ticket_attachment_table` → `ensure_web_task_results_tables` → `ensure_ticket_attachment_preload_table`.
3. `ensure_ticket_reviews_tables` → `ensure_ticket_feedback_tables` → `ensure_bot_button_settings_table` → `ensure_telegram_group_registry_tables` → `ensure_automatic_message_templates_table`.
4. `ensure_bot_menu_level_layouts_table` → `ensure_bot_menu_profiles_tables` → `ensure_client_card_field_config_columns` → `ensure_call_center_employee_resolver_state_table`.
5. `ensure_employee_notification_preferences_table` → `ensure_internal_task_tables` → `ensure_integration_logs_table` → `ensure_saved_files_table` → `ensure_background_contours_table` → `ensure_stats_foundation_tables` → `ensure_quick_reply_tables`.
6. В сессии: `migrate_legacy_bitrix_passwords` и `migrate_legacy_bot_button_pdf_saved_files`, затем `db.commit()`; далее системные seed и очистка старых интеграционных логов.

Этот порядок подтверждён **индексом вызовов**, не полным повторным чтением `main.py` на шаге 40. Системные seed/удаление старых логов — уже **операции с данными**, их не следует считать чистой проверкой схемы. Наличие отдельной условной демо-инициализации в `main.py` также не делает её безопасной для любого окружения без анализа настроек.

### 4.1. Что действительно улучшает существующие таблицы

Важные примеры из полного `schema_evolution.py`:

- `ensure_ticket_columns`: добавляет исторически отсутствовавшие поля, корректирует часть `NULL`; при **первом добавлении `ticket_number`** присваивает номера старым заявкам по `(created_at,id)` и делает уникальный индекс. Если столбец **уже присутствовал**, тот же путь создания индекса не гарантируется — требуется инспекция каталога.
- `ensure_client_profile_columns`: WEB-логин, нормализованный логин, пароль и версия учётных данных; выполняет нормализацию/переход legacy WEB credentials с проверками конфликтов и уникальности.
- `ensure_telegram_contact_company_user_unique`: ищет дубликаты пары `(company_id,external_user_id)` и **прерывает старт ошибкой**, если такие пары есть, вместо молчаливого слияния; иначе обеспечивает уникальность с диалектной реализацией.
- `ensure_telegram_notification_accounts_table`: создаёт/дополняет учётные записи уведомительного бота, backfill и уникальные идентификаторы.
- `ensure_ticket_reviews_tables`: циклы и версии отзывов, проверка дубликатов `cycle_id` до уникального индекса, актуальная версия через частичную уникальность; конфликтные данные также не «чинятся» произвольно.
- `ensure_bot_button_settings_table`: обновляет DB-only настройки; в частности, **удаляет устаревший столбец `is_db_created`**, если он существует, и заполняет `stable_button_id` для старых записей. Наличие `DROP COLUMN` означает, что startup способен физически изменить структуру, хоть это и штатная функция эволюции.
- `ensure_ticket_attachment_table` и `ensure_ticket_attachment_preload_table`: создают/дополняют хранилища файлов, включая состояние Telegram-upload; старые SQLite DDL-ветви могут отличаться по FK от ORM.
- `ensure_internal_task_tables`, `ensure_stats_foundation_tables`, `ensure_quick_reply_tables`, `ensure_*menu*`, `ensure_*feedback*` и другие: создают отсутствующие объекты/индексы, иногда дополняют существующие; нет общего механизма «полного синхронизатора» всех объявлений ORM.

## 5. Неавтоматические миграции — особенно важно

Функции **существуют в `schema_evolution.py`, но не найдены среди вызовов стартового блока `main.py`** на `8439b32`. Это не доказательство, что миграции нигде и никогда не применялись: их могли вызвать отдельным регламентом ранее. Текущий факт — **они не включены в исследованный startup**.

| Функция | Зачем нужна | Критичная особенность |
|---|---|---|
| `ensure_ticket_number_counter()` | Создаёт `ticket_number_counter` и инициализирует `last_number` по максимуму существующих номеров | Использует PostgreSQL advisory lock и singleton `id=1`; при уже созданном счётчике не заменяет его максимумом заново |
| `ensure_web_chat_messages_table()` | Создаёт техническую WEB-историю с event-key идемпотентностью | Таблица исключена из `create_all`; обычный startup `ensure` её не создаёт |
| `ensure_web_client_drafts_table()` | Создаёт серверный черновик с версией | Также исключена из `create_all`; необходима до привязки composer-draft вложений |
| `ensure_employee_notification_backlog_tables()` | Создаёт периоды и очередь накопленных уведомлений | **Обе таблицы исключены** из `create_all`, отдельное развёртывание перед использованием backlog |
| `ensure_ticket_attachment_preload_owners()` | Переход с сотруднического владельца на **либо `user_id`, либо `client_id`** | Добавляет `client_id`/FK, снимает `NOT NULL` с `user_id`, вводит XOR `CHECK`; в SQLite перестройка таблицы |
| `ensure_composer_draft_attachment_preloads()` | Разрешает WEB-композитор до создания `Ticket` | Добавляет nullable `composer_draft_id` и FK к `web_client_drafts`, снимает `NOT NULL` с `ticket_id`; в SQLite — перестройка с защитными проверками |

**Порядок зависимостей, а не инструкция к немедленному запуску:** WEB drafts должны существовать до FK из composer preload; для клиентских preload должна быть применена модель владельца `user_id XOR client_id`; после — проверка уникальностей и ограничений. Перед реальным выполнением обязательны резервная копия, сверка версий, описание фактического DDL и отдельное разрешение. Эти функции **ничего не менялись** в ходе документирования.

Для новых пустых БД общее `create_all` может создать современный `ticket_attachment_preloads` сразу, поскольку эта таблица **не входит в пятёрку исключений**. А вот стартовый `ensure_ticket_attachment_preload_table()` имеет совместимую с историей ветку создания **старой employee-only структуры** (`ticket_id`/`user_id NOT NULL`, без WEB-владельца и composer-draft) на некоторых существующих путях. Поэтому реальная форма зависит от истории создания/миграции; документировать её как единую «гарантированную» нельзя.

### 5.1. Отдельные миграционные Python-модули

В инвентаре **внутри `api/`** обнаружены:

- `api/app/services/web_credentials_migration.py` — вызов legacy-перехода из `ensure_client_profile_columns`.
- `api/app/services/bitrix_password_migration.py` — стартовый переход legacy Bitrix-паролей через сессию.
- `api/app/services/bot_button_saved_file_migration.py` — стартовая миграция исторических PDF кнопок в `SavedFile` и обновление ссылок.

Инвентарь был ограничен `api/`. Из него **нельзя** делать вывод об отсутствии Alembic, SQL-файлов или внешних эксплуатационных сценариев в других каталогах/на сервере. Полные тела перечисленных трёх модулей на шаге 40 не читались; их специфический бизнес-контракт освещён ранее в `TELEGRAM.md`/`WEB.md` либо только через места вызова.

## 6. Главные связи и осознанное отсутствие FK

Это логическая карта, а **не гарантия**, что в уже работающей базе каждая стрелка закреплена каталогом PostgreSQL.

| Связь/ключ | ORM-декларация | Практический смысл |
|---|---|---|
| `users.company_id`, `clients.company_id` → `companies.id` | FK | Сотрудники и клиенты относятся к организации |
| `client_profiles.client_id` → `clients.id` | FK + уникальность клиента | Один профиль на клиента |
| `telegram_contacts.client_id/company_id` → клиент/компания | FK; отдельная уникальность компании + external user | Сопоставление Telegram-пользователя внутри компании |
| `tickets.company_id/client_id` → компания/клиент | FK | Владелец заявки; фильтрация по организации и клиенту в сервисах |
| `tickets.assignee_id`, `closed_by_user_id`, `completed_by_user_id`, `special_assignee_id` → `users.id` | Nullable FK | Текущий исполнитель и отдельные факты lifecycle |
| `ticket_messages.ticket_id` → `tickets.id` | FK, ORM cascade delete-orphan через relationship | История конкретной заявки |
| `ticket_attachments.message_id/ticket_id` → сообщение/заявка | Два FK; ORM relationship от сообщения с delete-orphan | Вложения привязаны и к сообщению, и к заявке |
| `ticket_attachment_preloads.ticket_id` → `tickets.id`, `composer_draft_id` → `web_client_drafts.client_id` | Nullable FK в модели | Файл может существовать до создания полноценной WEB-задачи |
| `ticket_attachment_preloads.user_id/client_id` | Nullable FK + XOR CHECK | Владелец: сотрудник **или** клиент, не оба и не ноль |
| `web_chat_messages.client_id` → `clients.id` | FK | Технический WEB-чат клиента, отдельно от `TicketMessage` |
| `web_client_sessions.client_id` → `clients.id` | FK `ON DELETE CASCADE` | Сессии клиента и их удаление на уровне БД по родителю |
| `web_task_result_files.task_id` → `web_task_results.task_id` | FK | Снимок результата и его бинарные файлы |
| `web_task_results.task_id`, `company_id`, `client_id`, `message_id` | **Нет FK к `tickets`/`ticket_messages`** | Снимок не удаляется *автоматически из-за FK-каскада* при очистке отдельного рабочего сообщения. **Но явный admin hard delete заявки удаляет снимок прикладным кодом** — см. §8.2 |
| `tickets.parent_ticket_id` | **Обычный `String(36)` без FK** | Иерархию group parent/child проверяет прикладной код |
| `internal_task_occurrences.ticket_id` | **Обычный String, уникальный индекс, не FK** | Связь с выпущенной задачей без жёсткого каскада |
| `stats_*` company/user/object identifiers | **Часто String без FK** | Снимки/интервалы статистики переживают изменения жизненного цикла |
| `bot_button_settings.fixed_assignee_user_id`, `deleted_by_user_id` | **String без FK** | Идентификаторы конфигурации/атрибуции; не трактовать как референциально защищённые |
| `quick_replies.author_user_id` → `users.id`; `quick_reply_usage` | FK; в usage составной PK | Собственность шаблона и история использования |
| `ticket_reviews.previous_version_id` → `ticket_reviews.id` | Self-FK | Цепочка версий отзыва |
| `ticket_review_cycles.review_id` → `ticket_reviews.id` | FK с `use_alter` | Циклическая связь цикла и опубликованного отзыва |
| `telegram_automation_groups.company_id`, `bot_button_settings.telegram_group_id` | FK | Назначение автоматизации на зарегистрированную группу |

**Важно:** `relationship(cascade="all, delete-orphan")` — поведение **SQLAlchemy при операции через ORM**, это не то же самое, что `ON DELETE CASCADE` в DDL. В частности, для `Ticket → TicketMessage → TicketAttachment` модель задаёт ORM-cascade; нельзя утверждать, что прямой SQL `DELETE FROM tickets` физически каскадирует все файлы. И тем более удаление строк не означает удаления файлов с диска — файловый cleanup отдельно реализуется сервисами.

## 7. Уникальность, проверки и ключевые индексы

Список сосредоточен на **бизнес-инвариантах**, а не каждой автоматически создаваемой PK/индексации. Для фактических имён DDL и действительности ограничений нужен read-only запрос к конкретной БД.

### 7.1. Уникальности

| Объект | Ожидаемое ограничение / индекс | Причина |
|---|---|---|
| `tickets.ticket_number` | `UNIQUE`, допускается `NULL` | Внешний последовательный номер заявки |
| `ticket_number_counter.id` | PK + `id=1` CHECK | Один счётчик на эту БД |
| `client_profiles.client_id` | `UNIQUE` | Один профиль на клиента |
| `client_profiles.web_login_normalized` | `UNIQUE` при наличии WEB-логина | Запрет коллизии нормализованных логинов |
| `web_client_drafts.client_id` | `UNIQUE` | Один WEB-черновик на клиента |
| `web_chat_messages(client_id,event_key)` | `UNIQUE` | Идемпотентность добавления технического события |
| `web_login_limits(login_normalized,source_ip)` | Составной PK | Ключ ограничения входа |
| `telegram_contacts(company_id,external_user_id)` | Составной `UNIQUE` | Один Telegram-пользователь внутри компании |
| `telegram_contacts.external_chat_id` | Глобальный `UNIQUE` в модели | Не допускает второй контакт с тем же chat ID |
| `telegram_notification_accounts.telegram_user_id` / `.bound_user_id` | Раздельные `UNIQUE` | Не смешивать Telegram-аккаунты сотрудников |
| `telegram_updates.update_id` | `UNIQUE` | Маркер уже принятого update |
| `pending_task_intents.external_chat_id` | `UNIQUE` | Один pending intent на чат |
| `ticket_reviews(ticket_id,agent_id,version_number)` | Составной `UNIQUE` | Не дублировать версию отзыва |
| `ticket_reviews.cycle_id` | Уникальный индекс | Не привязывать два отзыва к одному циклу |
| `ticket_reviews(ticket_id,agent_id) WHERE is_current` | **Частичный** уникальный индекс | Не более одной актуальной версии на пару |
| `ticket_feedback.ticket_id` | `UNIQUE` | Один feedback по заявке |
| `employee_notification_periods(user_id,work_date)` | `UNIQUE` | Один период сотрудника на рабочую дату |
| `internal_task_occurrences(definition_id,occurrence_at_utc)` | `UNIQUE` | Идемпотентность выпуска по времени |
| `internal_task_occurrences.ticket_id` | Уникальный индекс | Один occurrence на выпущенную задачу |
| `stats_completions(company_id,object_type,object_id)` | `UNIQUE` | Один статистический snapshot объекта |
| `telegram_automation_groups(environment,bot_id,chat_id)` | `UNIQUE` | Одна регистрация группы для окружения/бота |
| `telegram_group_registration_codes.code_hash` | `UNIQUE` | Уникальный регистрационный код |
| `bot_button_settings.button_key`, `.stable_button_id` | Отдельные `UNIQUE` | Сохранение логического и стабильного ID кнопки |
| `bot_menu_level_layouts.level_key` | `UNIQUE` | Одна раскладка на уровень |
| `bot_menu_profiles.menu_type` | `UNIQUE` | Один профиль на тип меню |
| `bot_menu_destination_bindings.destination_value` | `UNIQUE` | Одно разрешение назначения |
| `automatic_message_templates.key` | `UNIQUE` | Один шаблон на ключ |
| `saved_files.storage_key`, `background_contours.storage_key` | Раздельные `UNIQUE` | Не допускать повторного физического storage key |

В других полях также есть уникальные свойства (например, поля конфигурации карточек и составной usage быстрых ответов). Таблица выше — **перечень эксплуатационно критичных случаев**, не полный автоматически сгенерированный DDL dump.

### 7.2. CHECK и строгие поля

| Таблица | Правило ORM / schema evolution | Значение |
|---|---|---|
| `tickets` | `channel IN ('telegram','web')` | Ограничение допустимого канала |
| `web_chat_messages` | `message_type IN ('client','bot','system')` | Не смешивать технический WEB-чат с agent/internal TicketMessage |
| `web_client_drafts` | `version >= 0` | Невозможна отрицательная версия |
| `ticket_number_counter` | `id = 1`, `last_number >= 0` | Singleton и неотрицательный счётчик |
| `ticket_attachment_preloads` | `(user_id IS NOT NULL AND client_id IS NULL) OR (user_id IS NULL AND client_id IS NOT NULL)` | Строго один владелец |
| `ticket_reviews` | `rating >= 1 AND rating <= 5` | Валидная оценка |
| `internal_task_definitions` | Assignment/schedule/recurrence/state/end — из перечисленных значений; при count-limit `max_occurrences > 0` | Недопустимые режимы не проходят при присутствии CHECK |
| `background_contours` | Диапазоны rotation 0–360, rings 0–2, distance 0–200, scale 50–200; размер 0–2 097 152 байт; PNG/WEBP | Формат и пределы графики |

**Тонкость:** значения `TicketStatus`, `TicketWorkflowType`, `TicketTaskKind`, `TicketIntakeState`, `MessageType` объявлены как Python `Enum`, но соответствующие поля `tickets.status/workflow_type/task_kind/intake_state` в модели — **строковые**, и для всех них не обнаружен отдельный `CHECK` аналогичный `channel`. Корректность состояний определяется прикладными переходами, не следует объявлять её полностью защищённой БД.

### 7.3. Показательные индексы чтения

- `web_chat_messages`: `(client_id,created_at,id)` — листание технической истории.
- `web_task_results`: `(company_id,client_id,read_at)` — выборка снимков результатов по владельцу/прочтению.
- `web_client_sessions`: клиент и обычный срок истечения — поиск/истечение WEB-сессий.
- `ticket_attachment_preloads`: `(ticket_id,user_id)` и `(ticket_id,client_id)` — файлы конкретного владельца/задачи.
- `ticket_review_cycles`: ticket и agent; `ticket_reviews`: ticket, agent, rated_at, составной `(agent_id,is_current,rated_at)`.
- `integration_logs`: created_at и составной `(source,event_type,result,deal_id,created_at)`.
- `quick_reply_usage`: `(user_id,last_used_at)`.
- `internal_task_definitions` / `internal_task_occurrences`: индексы выборки по времени/состоянию планировщика.
- `stats_work_intervals` / `stats_user_status_intervals`: выборки по сотруднику, компании и временному интервалу.

## 8. Прикладные контуры поверх схемы

### 8.1. Ticket — общий объект, а не одна «таблица заявок Telegram»

В `tickets` хранятся одновременно `channel` (`telegram`/`web`), `workflow_type` (`ticket`/`task`/`auto`), `task_kind` (`normal`/`group_parent`/`group_child`), `status` и `intake_state`. Это **разные оси**, поэтому `workflow_type=task` не доказывает `channel=web`, а `intake_state=collecting` не означает статус `new`. Для истории работы обращаться также к `ticket_messages`/`ticket_attachments`.

Поля `quick_action_state_json`, `call_center_profile_snapshot_json`, флаги/ID доставки и `button_config_key`/`instruction_button_key` хранят не строгую реляционную модель, а прикладное состояние/снимки. Структуру конкретных JSON-ветвей определяют сервисы Telegram, WEB и автоматизаций, а не FK. Подробный lifecycle — `TELEGRAM.md`, `WEB.md` и `WORKFLOWS.md`.

`parent_ticket_id` у дочерней групповой задачи **не FK**. При проверке согласованности группы недостаточно `information_schema.referential_constraints`: надо отдельно искать потерянных родителей, совпадение company и допустимую связь kinds. Это предложение для последующей проверки, не объявление найденной порчи данных. Сквозные действия назначения, закрытия и удаления — также в `WORKFLOWS.md`.

### 8.2. Почему есть два типа сообщений

- `TicketMessage` — часть рабочей заявки, имеет `ticket_id`, `message_type`, автора и список `TicketAttachment`.
- `WebChatMessage` — технический клиентский WEB-чат с `client_id` и `event_key`, отдельным ограничением типов `client/bot/system`. Он отображает сервисные события/состояние UI и имеет другой retention (по `WEB.md`).
- `WebTaskResult` — **третий объект**, сохранённый опубликованный итог, не зависимый внешним ключом от удаляемого TicketMessage. `WebTaskResultFile` хранит снимок файла (бинарные данные `LargeBinary`, загрузка поля в ORM `deferred=True`).

**Уточнение по исходникам шага 41:** отсутствие FK **не гарантирует сохранение** `WebTaskResult` после полного удаления задачи. В `api/app/services/ticket_admin_actions.py` функция `_delete_ticket_hard` **явно удаляет** строки `WebTaskResultFile` и `WebTaskResult` по `task_id` при административном `POST /api/v1/tickets/admin/delete`; эта операция отличается от очистки отдельного рабочего сообщения и от обычного закрытия с публикацией результата. Bulk-delete, обработка дочерних задач и post-commit уведомления разобраны в `WORKFLOWS.md` §10. Считать опубликованный результат вечным/неудаляемым только на основании отсутствия FK **неправильно**.

При анализе истории нельзя объединять строки этих трёх таблиц по простому совпадению текстов/дат: жизненный цикл, владельцы и назначение отличаются.

### 8.3. Нумерация

В `tickets.ticket_number` объявлена уникальность. Контрольное значение хранится в отдельном `ticket_number_counter` с одним `id=1`. Приведение `ensure_ticket_number_counter()` использует `MAX(ticket_number)` для начальной записи и PostgreSQL advisory locking; ранние исследования `telegram`-пути установили также использование `pg_advisory_xact_lock` в `ticket_numbering.py` при выдаче номеров. **Полный алгоритм выделения номера в отдельном сервисе не был повторно перечитан на шаге 40**; избегать утверждения, что все варианты одинаковы на PostgreSQL и SQLite.

### 8.4. WEB и Telegram-контакт

`ClientProfile` хранит WEB credentials, `WebClientSession` — **хеш**, а не открытый токен, `WebLoginLimit` — составной ключ ограничения попыток. Контракт WEB-авторизации из `WEB.md`: помимо профиля требуется верификация и TelegramContact. Наличие строк в `client_profiles` и `telegram_contacts` необходимо, но **само по себе недостаточно** для доказательства успешного входа, если credentials/версия сессии устарели.

### 8.5. Меню и файловая конфигурация

`BotButtonSetting` — структурный DB-only источник кнопок. В нём `button_key`, `stable_button_id`, `legacy_button_key`, путь/тип, настройки инструкции/назначения, `automation_key`, JSON прямых/условных файлов и признак архива. Layout и профили/назначения находятся в самостоятельных таблицах. `SavedFile` содержит метаданные и `storage_key`, а не гарантирует присутствие самих байтов на диске. Состояние физического файла и его ссылок надо сверять отдельно; файловый storage не транзакционный с SQL.

### 8.6. Внутренние задачи и статистика

`InternalTaskDefinition` задаёт расписание/условия, `InternalTaskOccurrence` — конкретный выпуск. Уникальные `(definition_id,occurrence_at_utc)` и `ticket_id` защищают от некоторых дублей на уровне БД, но `ticket_id` не является FK. Служебная статистика также осознанно сохраняет строковые идентификаторы и снимки вне строгих FK. Поэтому «висячая строка» в статистике не всегда ошибка — сначала нужен бизнес-контракт удержания истории.

### 8.7. Отзывы

Связь цикла и отзыва циклическая (`cycle.review_id` и `review.cycle_id`), специально учтена при создании FK. Сохраняются версии и отдельная `is_current`; частичный уникальный индекс обеспечивает **не более одного** текущего отзыва на пару ticket/agent (не обязательное существование такого отзыва). ФИО и тема — снимки для истории, их не следует автоматически переписывать из текущего профиля.

## 9. Риски расхождения модели и фактической БД

| Приоритет | Что сверить на живой БД read-only | Почему не достаточно чтения модели |
|---|---|---|
| **Критично** | Существование пяти исключённых из `create_all` таблиц и их индексов | Обычный API startup специально их не создаёт |
| **Критично** | `ticket_attachment_preloads`: nullable `ticket_id/user_id`, `client_id`, `composer_draft_id`, реальные FK и XOR CHECK | Историческая employee-only схема требует двух отдельных переходов; SQLite может быть физически другой |
| **Критично** | `ticket_number_counter`: singleton и согласованность с `MAX(ticket_number)`, наличие уникальности у `tickets.ticket_number` | Счётчик и unique могут появляться разными путями; корректность реальных значений не доказана |
| **Высоко** | `telegram_contacts`: дубли компании/external user, наличие compose unique и global chat uniqueness | Стартовый ensure отказывается исправлять дубли автоматически |
| **Высоко** | `ticket_reviews`: `cycle_id`, версия, partial unique текущего отзыва, `rating` | Две связанные таблицы и конфликтные исторические данные |
| **Высоко** | Список всех FK и `ON DELETE` на существующих tables, отдельно SQLite | `CREATE TABLE IF NOT EXISTS` не добавит отсутствовавший FK; ручные SQLite DDL местами не повторяют ORM |
| **Высоко** | `web_task_results`/`web_task_result_files`: тип бинарного поля, уникальность result, индекс по owner | Результат специально отделён от изменяемой заявки, неправильный случайный FK разрушит смысл снимка |
| **Средне** | `bot_button_settings`: отсутствие старого `is_db_created`, stable IDs, уникальные ключи и сохранённые JSON | Устаревшая схема могла пройти не все стадии backfill; startup содержит DROP |
| **Средне** | Часовые пояса SQL-столбцов и фактическая конвенция UTC, JSON/JSONB | ORM `DateTime(timezone=True)` соседствует с `datetime.utcnow`, а ручные DDL различаются по типам |
| **Средне** | Индексы обслуживания очередей, чистки, статистики, WEB-read | Декларация ORM не достраивает все индексы каждой legacy-таблицы |

**Неизученные здесь факторы:** настроенные PostgreSQL extension/schema/search_path/roles/permissions, triggers, grants, практические параметры isolation/locking, bloat/размеры таблиц, номера миграций других директорий, реальный размер БД, внешние резервные копии. Никаких утверждений об их наличии/отсутствии нет.

## 10. Безопасная методика проверки фактической схемы — будущий отдельный этап

Это **план read-only сверки**, а не выполненная проверка и не поручение запускать SQL сейчас.

1. Зафиксировать окружение (`DEV`/`PROD`), точный Git HEAD, PostgreSQL версию, имя database/schema и факт существования актуальной проверяемой резервной копии; секреты/пароли в отчёт не копировать.
2. Считать системные каталоги: таблицы/колонки/типы/nullability/defaults; все PK/FK/unique/check с точным `ON DELETE`; все индексы и partial predicates. Для PostgreSQL источники — `pg_catalog`, `pg_get_constraintdef`, `pg_get_indexdef` и `information_schema`.
3. Сравнить набор с ORM + условной эволюцией **по таблицам**, отдельно пометив колонку/ограничение «модель заявляет» → «реальная БД имеет/не имеет».
4. Снять безопасные агрегаты дублей/инвариантов (`COUNT`, группировки), особенно TelegramContact, review cycles/current, numbering, WEB event-key, preload owners; без выгрузки персональных данных.
5. Составить краткий **список различий**, разделить на ожидаемый legacy, потенциально опасное расхождение и неизвестное. Ничего автоматически не чинить.
6. Только по отдельному решению — подготовить порядок миграций, план rollback, проверку restore и адресные tests/smoke; исполнение **не является частью документирования**.

## 11. Что установлено, а что остаётся открытым

**Установлено чтением исходников:** 50 ORM-таблиц, основные связи и ограничения, исходная последовательность startup ensure, наличие пяти исключений из create_all, шесть отдельных явных функций миграции из §5, ключевые SQLite/PostgreSQL различия, отсутствие FK в ряде намеренно отвязанных snapshot-связей.

**Не проверено:** действующие DDL/FK/индексы DEV и PROD; фактические данные и последствия legacy миграций; выполнение `ensure_*` на старой конкретной БД; полное содержимое других миграционных сценариев; end-to-end тесты и время выполнения. Поэтому в документе слова «уникально», «FK», «CHECK» означают **декларацию ORM или код создания/модификации**, если явно не сказано обратное.

**Изменения в системе:** никаких — только исследование. Не запускались Codex, миграции, команды БД, rebuild, tests, deploy или push.

## 12. Источники и связь с соседними документами

| Источник | Покрытие шага 40 |
|---|---|
| `api/app/models.py` | Полный исходник, 50 моделей, декларативные связи/индексы/ограничения |
| `api/app/services/schema_evolution.py` | Полный исходник, startup/отдельные ensure, PostgreSQL и SQLite ветви |
| `api/app/db.py` | Полный исходник, engine/Base/SessionLocal/get_db |
| `api/app/main.py` | **Индекс**, но не полный исходник в новом экспорте: `create_all`, исключения, последовательность startup ensure/миграций |
| Перечень `api/**` migration/SQL | Имена трёх сервисных миграций, не гарантированно полный инвентарь всего репозитория |
| `TELEGRAM.md` v0.11 | Меню, callbacks, файлы/автоматизации, Telegram/номерные/уведомительные flows |
| `WEB.md` v0.12 | Авторизация, черновики, рабочие задачи/файлы, технический чат, результаты/история |
| `DOCUMENTATION_PROGRESS.md` v0.13 | Журнал, степень проверки, границы, следующий шаг |

**Исходная выгрузка шага 40:** `Вставленный текст(20261108-190005).txt` (~209 КБ), `HEAD=8439b32`, Windows `git status`: только `?? docs/current/`.

**Следующий проход:** `WORKFLOWS.md` / `INVARIANTS.md` — задокументировать ключевые сквозные переходы по общей цепи Telegram/WEB → Ticket → назначение → результат/закрытие → фоновые действия, а также защитные правила и конфликтные состояния. Не переизучать целиком `models.py`, `schema_evolution.py` и WEB frontend без изменения ревизии; при необходимости адресно снять крупный экспорт API сотруднической стороны и сервисов переходов.
