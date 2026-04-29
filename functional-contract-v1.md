# Functional Contract v1 (updated)

## 1) Channel
- One active channel for v1: Telegram.

## 2) Roles
- `agent` sees only tickets assigned to them.
- `admin` sees all tickets and manages assignment.

## 3) Ticket statuses
- `unassigned` (admin-only visibility in work queues; incoming ticket not distributed yet)
- `new` (no agent message yet)
- `pending` (ticket in active work; at least one agent action/message exists)
- `closed` (final)

Notes:
- `open` is removed.
- Reopen is not used in v1.

## 4) Lists
- Active: `new`, `pending` with `is_frozen=false`
- Frozen: `new`, `pending` with `is_frozen=true`
- Closed: `closed`
- `unassigned` is visible to admin only.

## 5) Default sorting
- Default in lists: newest first (`created_at desc`).
- Column click sorting is supported.

## 6) Close rules
- Ticket can be closed only if there is at least one `agent` message.
- Client command `/close` closes active ticket.

## 7) Message types
- `client`
- `agent`
- `internal` (green, not sent to Telegram)
- `system` (blue, not sent to Telegram)

## 8) Greeting button
- Time windows (Europe/Moscow):
  - 05:00-11:59 -> "Доброе утро"
  - 12:00-16:59 -> "Добрый день"
  - 17:00-19:30 -> "Добрый вечер"
- After 19:30 button is disabled.

## 9) Freeze block
- `is_frozen` boolean
- optional `freeze_until`
- if datetime invalid/empty -> no auto-unfreeze

## 10) Final stage (before production)
- Rotate Telegram bot token in BotFather (old test token must be revoked).
- Update `.env` with new production token.
- Verify webhook/long-poll settings with the new token.
