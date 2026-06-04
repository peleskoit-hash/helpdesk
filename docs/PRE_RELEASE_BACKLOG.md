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
