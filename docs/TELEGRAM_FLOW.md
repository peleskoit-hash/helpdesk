# Telegram flow

## Entry points

Router:
- `api/app/routers/telegram.py`

Polling:
- `api/app/services/telegram_polling.py`
- started from `api/app/main.py` when env enables it.

Transport:
- `api/app/services/telegram_bot.py`

## /start and onboarding

Service:
- `api/app/services/telegram_onboarding.py`

Responsibilities:
- process `/start`;
- find/create `TelegramContact`;
- create/update `Client` and `ClientProfile`;
- first connection flow;
- keep behavior for existing contacts.

Menu keyboard tracking:
- `TelegramContact.active_menu_message_id`;
- `TelegramContact.menu_message_ids_json`;
- on `/start`, old tracked menu keyboards should be removed before sending a new one.

Risk:
- deleting initial ticket or not verifying client must not accidentally grant full menu if onboarding state says otherwise.

## TelegramContact

Model fields include:
- external chat/user ids;
- username/display name;
- `client_id`;
- active ticket/menu fields;
- client close button message id;
- menu keyboard tracking.

## Menu callbacks and DB-only runtime

Service:
- `api/app/services/telegram_menu_flow.py`

Related:
- `api/app/services/button_config.py`
- `TG buttons/` legacy/reference configs
- `BotButtonSetting`
- `BotMenuProfile`
- `BotMenuDestinationBinding`

Rules:
- `ClientProfile.destination` chooses menu profile;
- fallback menu exists;
- DB-only/txt compatibility must not be broken casually;
- menu/callback/group_task are high-risk areas.

## Ticket/task/auto/group_task creation

Telegram flow can create:
- `ticket` - normal support request;
- `task` - internal task based on client text;
- `auto` - automation branch;
- `group_task_parent` + `group_task_child` - parent/children task workflow.

Important:
- if client selects task and waits for text, intake timeout closes/cancels according to current timer settings;
- task/group_task should not send unnecessary queue notification to client;
- group parent assignment creates child tasks.

## Client close flow

Service:
- `api/app/services/telegram_client_close.py`

Responsibilities:
- store/remove client close button;
- process `client_close` callback;
- close active ticket for contact;
- gracefully handle stale callback/no active ticket.

Router must call service; no duplicate old local functions in `telegram.py`.

## Automation flow

Service:
- `api/app/services/telegram_automation.py`

Examples:
- Bitrix phone/deal lookup automation.

Rules:
- valid result may be sent automatically;
- if API error or result validation fails, create task for agent;
- phone lookup result must be only digits and `+` to auto-send.

## Attachments and policy

Services:
- `telegram_attachments.py` - file storage / attachment creation.
- `telegram_attachment_policy.py` - limits, forbidden types, client/system notices.

Rules:
- voice/video messages are not supported;
- too-large/forbidden files warn client and create system message as designed;
- do not create broken attachment rows.

## Telegram HTML formatting

Service:
- `api/app/services/telegram_text_format.py`

Responsibilities:
- sanitize allowed Telegram HTML;
- strip HTML fallback;
- prepare `(safe_text, parse_mode)`;
- guard `<`, `>`, `&` outside allowed tags;
- validate links.

Used for:
- `client_text`;
- `confirmation_text`;
- group_task parent final text where applicable.

Allowed controlled HTML tags include:
- `b`, `i`, `u`, `s`, `tg-spoiler`, `code`, `pre`, `blockquote`, `a href`.

## Services already extracted

- `telegram_onboarding.py`
- `telegram_client_close.py`
- `telegram_automation.py`
- `telegram_attachment_policy.py`
- `telegram_attachments.py`
- `telegram_text_format.py`
- `telegram_menu_flow.py`
- `telegram_bot.py`
- `telegram_polling.py`

## Не трогать без отдельной задачи

- General callback/menu runtime.
- DB-only/txt migration semantics.
- Group task parent/child creation.
- Telegram polling lifecycle.
- Bot API large-file/local Bot API support.
