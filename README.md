# Helpdesk (local MVP)

Локальный helpdesk с Telegram-ботом и web-интерфейсом для обработки заявок.

## Запуск

```bash
docker compose up --build
```

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`

## Страницы

- `/tickets` — список заявок
- `/tickets/{id}` — карточка заявки
- `/settings` — главная настроек (admin)
- `/settings/users` — настройка пользователей (admin)
- `/settings/client-card` — настройка карточки клиента (admin)
- `/statuses` — быстрое управление статусами сотрудников (admin)

## Роли

- `admin` — полный доступ
- `agent` (`Сотрудник`) — рабочий доступ
- `trainee` (`Стажёр`) — как сотрудник, но без автоназначения
- `chiki_piki` — read-only наблюдатель по "Колл Центру"

## Очереди (вкладки)

- Нераспределённые
- Активные
- Особые (только admin)
- Замороженные
- Закрытые

## Ключевые функции

- Работа с обращениями `ticket`, задачами `task`, автообращениями `auto`, group-task.
- Attachments (входящие из Telegram и исходящие из helpdesk), предпросмотр изображений.
- Поддержка стикеров (static/video/animated с fallback).
- Bitrix automation (`bitrix_deal_phone_lookup`).
- Forced assignee через TG buttons (`Ответственный: login:/user_id:`).
- Special queue (`Особые`) для admin.
- Админские принудительные действия: завершение/удаление.
- Автотаймер закрытия для обычных non-special ticket.
- Настраиваемая карточка клиента (системные + custom поля).

## Telegram (кратко)

- `/start` открывает меню проблем.
- После выбора конечной кнопки меню-клавиатура убирается.
- Если конечная проблема не выбрана за 10 минут — предсоздание отменяется.
- `/close` закрывает активное обращение без автопоказа меню.

## Важно

- Все файлы проекта в UTF-8.
- Секреты и токены в документацию не добавлять.

## Для Codex / локальной разработки

- Перед любой задачей перейти в корень проекта:
  `Set-Location "C:\Users\ES\Downloads\Source\helpdesk"`
- Проверить корень:
  `Test-Path .\api\app\main.py`
- Все относительные пути в задачах считать от `PROJECT_ROOT`.
- Перед завершением изменений запускать проверку битой кириллицы:
  `python tools/check_mojibake.py api/app "TG buttons" README.md PROJECT_CONTEXT.md functional-contract-v1.md`
