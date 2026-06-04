# Frontend app shell / partial navigation

## Зачем нужен

Проблема: при обычных переходах HTML-документ перезагружается целиком, sidebar уничтожается и создаётся заново. Это вызывает flicker avatar/initials/counts/sidebar.

Решение: `api/app/static/app_shell.js` перехватывает безопасные внутренние ссылки, fetch-ит HTML страницы, извлекает `#appContent` и заменяет только правую часть. Sidebar остаётся живым.

## Основные элементы

В template должен быть:

```html
<main id="appContent" class="main" data-shell-page="settings-home">
  ... page content ...
</main>
```

Page script должен регистрироваться:

```javascript
if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("settings-home", initSettingsHomePage);
} else {
  initSettingsHomePage();
}
```

Init может вернуть cleanup:

```javascript
return () => {
  // remove listeners / stop timers
};
```

## Текущий scope

Закоммиченный стабильный baseline:
- `/settings`
- `/settings/users`
- `/settings/client-card`
- `/settings/bot-buttons`

Простые кандидаты для расширения:
- `/statuses`
- `/mass-notifications`

Намеренно не переводить без отдельной задачи:
- `/tickets`
- `/tickets/{id}`

Почему:
- `/tickets` имеет tab/page/q/search/pagination/popstate;
- `/tickets/{id}` имеет chat/composer/attachments/polling/return_url/cleanup.

## Правила ссылок

Для shell routes использовать:
- обычные `<a href="/settings/users">...</a>`, которые shell может перехватить;
- или `HelpdeskAppShell.navigate("/settings/users")`.

Не использовать для shell routes:
- прямой `window.location.href = "/settings/users"`, кроме fallback если shell недоступен.

Shell не должен перехватывать:
- external origin;
- `target="_blank"`;
- download links;
- Ctrl/Cmd-click;
- middle click;
- routes outside whitelist.

## Error/fallback behavior

Fallback to full navigation допустим только если:
- route not whitelisted;
- fetch/extract failed before mount;
- shell unavailable.

Если ошибка после partial mount/init:
- не делать silent full reload;
- показать ошибку в status/error area;
- оставить sidebar живым.

## Page scripts

Script tags from fetched HTML do not execute automatically. Поэтому:
- app shell must ensure required scripts are already loaded or load them explicitly;
- page modules must expose/register init;
- init must be safe on hard refresh and partial load;
- cleanup must prevent duplicate timers/listeners.

## Cache busting

When changing shell/page JS, bump query version in templates and shell route scripts, e.g.:

```html
<script src="/static/app_shell.js?v=20260604a"></script>
```

## Sidebar

Files:
- `api/app/static/sidebar.js`
- `api/app/static/theme.js`
- `api/app/static/styles.css`

Important:
- `HelpdeskSidebar.init()` should be idempotent;
- do not render admin-only UI from stale localStorage before session;
- logout must clear stale role/user/count keys;
- role-sensitive elements hidden until sidebar is ready.

## Manual checks

For each shell route:
1. Hard refresh works.
2. Click from another shell page does not trigger Document navigation.
3. Network shows fetch HTML, not full page Document reload.
4. Sidebar DOM/avatar/counts do not flicker.
5. Page content initializes correctly.
6. Back/Forward works.
7. Repeated transitions do not duplicate handlers/polling.
8. Browser Ctrl/Cmd-click and middle click remain normal links.
## UI/app shell text safety

Любая app shell, UI, HTML, template или frontend JS-задача считается текстоопасной, если меняет русские подписи или строки интерфейса.

Перед ответом и перед commit обязательно:
```powershell
node --check <changed js files>
python tools/check_mojibake.py <changed js/html/css/md files>
python tools/check_staged_encoding.py
git diff --check
```

Ручной diff review обязателен даже при passed checks. Проверить добавленные/изменённые русские строки:
- подписи кнопок;
- modal titles;
- labels;
- empty states;
- error/status messages;
- placeholder/aria-label/title;
- cache-busting версии, если менялся JS.

В diff не должно быть mojibake-паттернов: кириллическая `Р`/`С` в начале бессмысленных фрагментов, латинские `D-with-stroke`/`N-with-tilde`/`A-with-circumflex` последовательности, двойные UTF-8 артефакты или `????`.

Для записи файлов:
- использовать `apply_patch` или UTF-8 write через Python;
- не писать кириллицу через PowerShell pipe/`Set-Content`/`Out-File` без явного UTF-8;
- не добавлять BOM и не делать массовую смену line endings.

Если после UI/app shell правки появились кракозябры, не делать commit: сначала исправить текст и повторить проверки.
