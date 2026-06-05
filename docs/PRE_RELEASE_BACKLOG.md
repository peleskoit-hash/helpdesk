# Предрелизный backlog

Короткий список известных направлений, которые лучше делать отдельными задачами.

## App shell / flicker

Done / baseline:
- settings pages partial navigation.

Next safe slices:
- `/statuses` partial navigation;
- `/mass-notifications` partial navigation.

Risky / later:
- `/tickets` partial navigation because of tab/page/q/search/pagination/popstate.
- `/tickets/{id}` partial navigation because of chat/composer/attachments/polling/return_url.

## Telegram files

Future:
- large-file support / Local Bot API / higher file limits.

Risks:
- attachment policy already has user notices and system messages;
- do not mix with normal attachment storage refactor.

## Telegram formatting

Done first stage:
- controlled Telegram HTML in `client_text` / `confirmation_text`;
- sanitizer/fallback in `telegram_text_format.py`.

Future:
- emoji picker in bot button text editor;
- better Telegram preview parity.

## Bot settings UX

Possible future tasks:
- separate "Автоматические сообщения" settings;
- safer editing for group_task parent/children;
- better fallback visibility: DB override vs txt fallback;
- audit all Russian labels after large UI changes.

## Automatic texts

Known sensitive areas:
- group_task aggregated final answer;
- admin delete/force close client notifications;
- wrong-problem corrective message;
- problem-not-selected message;
- intake timeout message;
- client close message.

Rule:
- text-only changes should be separate commits where possible.

## Roles and permissions

Need periodic regression checks:
- chiki_piki list/card/counts only for "Колл Центр";
- trainee not autoassigned;
- admin-only controls not visible to non-admin;
- restore only by admin or closer where intended.

## Deployment / dev-prod split

Future architecture:
- separate dev/prod env files;
- production secret handling;
- backup policy;
- cloud sync rules excluding secrets/storage/logs;
- migrations instead of ad-hoc schema evolution eventually.

## Testing debt

Potential tests:
- unit tests for `ticket_lifecycle.py` group_task finalization;
- tests for `ticket_list.py` role/search filters;
- tests for `telegram_text_format.py` sanitizer;
- smoke script for app shell routes;
- smoke script for bot button settings save/load.

## Do not bundle before release

Avoid combining in one commit:
- Telegram runtime + frontend layout;
- schema changes + text fixes;
- role visibility + search/pagination;
- group_task lifecycle + admin delete;
- app shell + ticket card conversion.

## Roadmap: planned features

Это не техническое ТЗ, а список будущих направлений. Каждый пункт перед реализацией требует отдельного обсуждения требований, UX, edge cases, модели данных, прав доступа, рисков и миграций, если они нужны.

### До релиза или сразу после стабилизации

1. Telegram emoji picker в редакторе текстов кнопок бота

- Добавить отдельную кнопку в toolbar большого Telegram-редактора.
- По клику открывать выпадающее или плавающее меню примерно как в Telegram.
- Использовать стандартный набор emoji, без custom/premium Telegram emoji на первом этапе.
- Меню должно быть скроллируемым, выбранный emoji вставляется в позицию курсора.
- Перед реализацией отдельно обсудить UX, категории, поиск, позиционирование popup и поведение при выделенном тексте.

2. `Настройки -> Автоматические сообщения`

- Новый раздел настроек со списком автоматических Telegram-текстов helpdesk/bot сценариев.
- Постепенно перенести туда тексты создания заявки/задачи, group_task flows, закрытия клиентом/агентом/админом, авто-закрытия, заморозки/разморозки, wrong problem, таймаутов, очереди/распределения, ошибок, onboarding/menu timeout и предупреждений по файлам.
- Редактирование должно использовать большой Telegram text editor и formatting toolbar, как `Текст клиенту` в настройках кнопок бота.
- Перед реализацией нужен отдельный аудит всех hardcoded automatic messages и ключей шаблонов.

3. Частичное цитирование сообщений

- Дать агенту возможность цитировать выделенный фрагмент сообщения, а не всё сообщение целиком.
- В Telegram reply должен отображаться выбранный фрагмент, если это поддерживается.
- Полное цитирование сообщения оставить как fallback.
- Перед реализацией отдельно обсудить UX выделения, хранение `quote_text`/позиции, UTF-16 offsets, кириллицу, emoji, переносы строк, captions и fallback для удалённых сообщений.

4. Отдельный бот уведомлений для сотрудников

- Сделать отдельного Telegram-бота для уведомлений сотрудникам о новых заявках и новых сообщениях.
- Добавить admin-раздел `Настройка уведомлений` с default-настройками для всех сотрудников.
- Default-настройки должны быть базой для личных настроек сотрудников.
- Перед реализацией отдельно обсудить bot token, привязку Telegram-аккаунта сотрудника, события, throttling/anti-spam, quiet hours, права, шаблоны и отключение типов уведомлений.

5. Профильное меню из нижнего левого кружка с аватаром/инициалами

- Сделать нижний sidebar avatar/initials крупнее и кликабельным.
- По клику открывать меню/гармошку вверх.
- Пункты сверху вниз: `Личные данные`, `Мои отзывы`, `Личные настройки уведомлений`, `Выход`.
- `Личные данные`: смена аватарки, изменение пароля, просмотр данных учётки.
- `Мои отзывы`: будущая страница отзывов агента с фильтром периода.
- `Личные настройки уведомлений`: персональные настройки после появления отдельного notification bot, включая формат уведомлений, настройки новых сообщений и сброс к default.
- Текущую кнопку выхода перенести в это меню.
- Перед реализацией отдельно обсудить UX, права, узкие экраны, хранение аватаров, смену пароля и модель персональных уведомлений.

6. Новый admin-фильтр `Проблемные КЦ`

- Добавить список/фильтр для админов по аналогии с `Особые`.
- В отличие от `Особые`, агенты смогут отправлять туда заявки/задачи.
- Кнопка отправки в `Проблемные КЦ` должна появляться только для заявок/задач типа `Колл Центр`.
- Перед реализацией отдельно обсудить видимость списка, права отправки/возврата, assignee semantics, влияние на auto-close/freeze, уведомления и причины/комментарии отправки.

7. Большая переработка `Настройки -> Карточка клиент`

- Отдельная крупная UX-задача.
- Переработать структуру настройки карточки клиента.
- Перед реализацией отдельно обсудить поля, блоки, порядок, типы полей, визуальные группы, цветовые схемы, права, миграцию существующих данных и совместимость с `ClientProfile.destination` / `Куда?`.

### После деплоя

8. Большая переработка автоматизаций по кнопкам

- Переработать создание, настройки и управление автоматизациями, привязанными к кнопкам бота.
- Связано с будущим развитием `Настройки -> Кнопки бота`.
- Перед реализацией отдельно обсудить типы автоматизаций, UI конструктора, хранение, fallback, ошибки, ручную передачу агенту и логи выполнения.

9. Кнопки перехода в карточке заявки

- Добавить настраиваемые кнопки в карточке заявки.
- Кнопки могут вести на внешние или внутренние страницы.
- В URL должны подставляться данные из карточки клиента: имя, телефон, destination / `Куда?` и другие поля `ClientProfile`.
- Перед реализацией отдельно обсудить placeholders, права, валидацию URL, безопасность, место настройки и место отображения в карточке.

10. Отзывы и оценки после заявок

- Реализовать отзывы и оценки именно после заявок, не задач.
- Отзывы/оценки должны храниться.
- Админы должны видеть отзывы, агенты должны видеть свои отзывы.
- В будущем связано с пунктом `Мои отзывы` в профильном меню.
- Перед реализацией отдельно обсудить момент отправки запроса оценки, шкалу, текст отзыва, анонимность/видимость, фильтры по периодам, права доступа, хранение и влияние на статистику.
