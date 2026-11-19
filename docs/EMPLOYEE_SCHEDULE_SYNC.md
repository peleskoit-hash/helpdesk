# Employee schedule: stage 1

This stage only imports Spark API v2 calendars. It does not change user status,
Telegram delivery/backlog, user bindings or UI.

## Configuration

The existing Settings/env mechanism reads:

- `EMPLOYEE_SCHEDULE_ENABLED`: `false` by default. Both manual and automatic
  synchronization are disabled until explicitly enabled.
- `EMPLOYEE_SCHEDULE_API_BASE_URL`: HTTPS origin, without credentials/path/query.
- `EMPLOYEE_SCHEDULE_API_KEY`: secret header `X-API-Key`; never exposed by the API.
- `EMPLOYEE_SCHEDULE_REQUEST_TIMEOUT_SECONDS`: 10 by default, range 1..30.
- `EMPLOYEE_SCHEDULE_MAX_RESPONSE_BYTES`: 8 MiB by default, maximum 32 MiB per month.

Configure the same external key separately in DEV/PROD environments. Do not add
secrets to Git. Environment changes in an existing container require the normal
environment reload procedure; editing a host .env alone does not replace its env.

## Explicit migration (not applied automatically)

Models: `EmployeeScheduleSnapshot` and `EmployeeScheduleSyncState`.
API startup explicitly excludes both tables from `create_all`. The scheduler and
HTTP handlers never create tables. Without migration the status reports
`schema_ready=false`; an enabled manual run returns `migration_required`.

When separately authorized, run this in the API environment whose database has
been verified as the intended target:

```python
from app.services.schema_evolution import ensure_employee_schedule_tables
ensure_employee_schedule_tables()
```

The migration supports SQLite/PostgreSQL, creates only the two new tables and
inserts the singleton with conflict-ignore semantics. Repeating it preserves
stored snapshots and sync state. It must not race another migration runner.

## Operation

The existing internal task scheduler starts a background sync worker. HTTP and
DB work run outside the event loop and do not hold up its other jobs.

Normal refresh is due at 16:00 Europe/Moscow. Missing month coverage triggers
bootstrap; failed attempts retry no earlier than five minutes later; a missed
daily refresh is caught up after restart. An abandoned claim can be reclaimed
after its five-minute lease. Token/generation checks fence late workers.

Each attempt downloads current/next month using GET only and validates both
before opening the publication transaction. Pair replacement, singleton success
and deletion of other months commit together. Failures retain the entire old
pair and last_success_at. A month-boundary crossing invalidates the attempt.

Snapshot age or a later network failure does not delete valid stored data.
Only a successful publication replaces/prunes snapshots; there is no archive.
The v2 external is_active flag does not filter employees or their calendars.
work_day and the explicit shift alone determine whether a day contains a shift.

## Admin API

- `GET /api/v1/settings/employee-schedule`: enabled/configured/schema state,
  last attempt, last success, safe error code and aggregate saved-month metadata.
- `POST /api/v1/settings/employee-schedule/sync`: returns 202 after a durable claim;
  executes HTTP in a background task. A concurrent run returns 409.

Both routes require existing admin authorization. No route returns raw calendar
JSON, employee identities, API key or lease token. Inspect status after a manual
202 to determine the result. Expired running claims display as interrupted.

## Tests

`tests/test_employee_schedule.py` uses temporary SQLite databases and optionally
`EMPLOYEE_SCHEDULE_TEST_DATABASE_URL` pointing to a disposable PostgreSQL database
named `employee_schedule_test`. Never supply a working DEV/PROD database.
