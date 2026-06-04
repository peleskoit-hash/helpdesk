# Настройки кнопок бота

## Где находится UI

Page:
- `/settings/bot-buttons`

Template:
- `api/app/templates/settings_bot_buttons.html`

Frontend modules:
- `api/app/static/settings_bot_buttons.js`
- `api/app/static/bot_buttons_api.js`
- `api/app/static/bot_buttons_level_view.js`
- `api/app/static/bot_buttons_editor.js`
- `api/app/static/bot_buttons_editor_view.js`
- `api/app/static/bot_buttons_menu_view.js`
- `api/app/static/bot_buttons_files.js`

Backend routers:
- `settings_bot_menu_profiles.py`
- `settings_bot_button_structure.py`
- `settings_bot_button_items.py`
- legacy parts in `settings.py`

## Menu profiles and destination

`ClientProfile.destination` is the field "Куда?" in client card.

Menu profile binding:
- one menu profile can be bound to many destination values;
- one destination value can be bound to only one menu profile;
- fallback/basic menu used if no explicit binding.

Tables/models:
- `BotMenuProfile`
- `BotMenuDestinationBinding`
- `BotMenuLevelLayout`
- `BotButtonSetting`

## BotButtonSetting fields

Important fields:
- `button_key`
- `button_label`
- `button_path`
- `menu_type`
- `workflow_type`
- `is_enabled`
- `instruction_text`
- `client_text`
- `client_text_overridden`
- `system_text`
- `system_text_overridden`
- `confirmation_enabled`
- `confirmation_text`
- `confirmation_overridden`
- `fixed_assignee_id`
- `freeze_on_create`
- `automation_key`
- files/conditional files metadata

## Text fields

Fields in editor:
- "Инструкция" -> internal UI instruction.
- "Текст клиенту" -> text sent to Telegram client.
- "Текст системный" -> internal/system text in ticket.
- "Подтверждение" -> Telegram confirmation before creation.

Large modal editors:
- client/confirmation have Telegram preview;
- internal fields do not need Telegram toolbar.

Telegram HTML toolbar:
- only for `client_text` and `confirmation_text`;
- stores controlled Telegram HTML in existing text fields;
- runtime sanitizes through `telegram_text_format.py`.

## group_task parent editor

Group task parent has child tasks.

UI includes parent edit action so admin can edit parent button settings, including client text.

Important:
- parent final Telegram text should use saved DB override where intended;
- finalization should not accidentally send original fallback if DB text was changed;
- for actual accumulated child answers, lifecycle aggregation has priority in completion flow.

## Layout editor

- Level layout supports rows / order.
- Child task ordering may be intentionally hidden where order has no runtime meaning.
- Avoid adding delete/reorder controls outside the level where selection exists and behavior is clear.

## Legacy txt fallback

`TG buttons/` remains reference/fallback.

Current rule:
- do not remove txt compatibility casually;
- when adding DB settings, preserve fallback behavior unless task explicitly migrates it;
- do not change button_config runtime in UI-only tasks.

## Frequent risks

- Losing line breaks in `client_text` / `confirmation_text`.
- `.strip()` removing meaningful leading/trailing newlines.
- Mojibake in Russian labels.
- Not saving override flags.
- UI shows changed text but Telegram runtime still uses fallback.
- Parent group_task final text differs from saved UI text.
- Empty submenu/group parent not opening in level view.

## Checks

Frontend:
```powershell
node --check api/app/static/settings_bot_buttons.js
node --check api/app/static/bot_buttons_editor_view.js
node --check api/app/static/bot_buttons_level_view.js
```

Backend:
```powershell
python -m py_compile api/app/routers/settings_bot_button_items.py api/app/routers/settings_bot_button_structure.py api/app/routers/settings_bot_menu_profiles.py
```

Encoding:
```powershell
python tools/check_mojibake.py api/app/static/settings_bot_buttons.js api/app/static/bot_buttons_editor_view.js api/app/static/bot_buttons_level_view.js api/app/routers/settings_bot_button_items.py
```
