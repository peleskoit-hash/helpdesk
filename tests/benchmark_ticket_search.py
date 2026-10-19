"""Reproducible isolated search measurements; prints results, never touches DEV tables.

Run from the repository root with runpy (as in the UI fixture). Set
TICKET_SEARCH_TEST_DATABASE_URL for a temporary PostgreSQL schema.
"""
import argparse
import json
import os
import statistics
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, event, insert, text
from sqlalchemy.orm import Session

from api.app.db import Base
from api.app.models import (Client, ClientProfile, Company, InternalTaskDefinition, InternalTaskOccurrence,
                            TelegramContact, Ticket, TicketMessage, User)
from api.app.services import ticket_list


@contextmanager
def database(backend):
    if backend == "sqlite":
        with tempfile.TemporaryDirectory(prefix="search_bench_", dir="work") as folder:
            engine = create_engine("sqlite:///" + (Path(folder) / "bench.sqlite").as_posix())
            try:
                yield engine
            finally:
                engine.dispose()
        return
    url = os.environ["TICKET_SEARCH_TEST_DATABASE_URL"]
    admin = create_engine(url)
    schema = "search_bench_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def seed(engine, count):
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(insert(Company), [{"id": "company", "name": "Synthetic"}])
        connection.execute(insert(User), [{"id": "admin", "company_id": "company", "full_name": "Анна Администратор", "role": "admin"}])
        connection.execute(insert(Client), [{"id": f"client-{i}", "company_id": "company", "display_name": "Анна Клиент"} for i in range(300)])
        connection.execute(insert(ClientProfile), [{"id": f"profile-{i}", "client_id": f"client-{i}",
            "custom_fields": {"custom_fio": "Анна Петрова", "department": "Support"}} for i in range(300)])
        connection.execute(insert(TelegramContact), [{"id": f"contact-{i}", "company_id": "company", "client_id": f"client-{i}",
            "external_user_id": str(i), "external_chat_id": str(i), "external_display_name": "Анна Telegram", "external_username": f"synthetic_{i}"} for i in range(300)])
        connection.execute(insert(InternalTaskDefinition), [{"id": "definition", "company_id": "company", "title": "Synthetic",
            "description": "Synthetic", "creator_admin_id": "admin", "responsible_admin_id": "admin", "display_author": True,
            "assignment_mode": "distribution", "notify_on_completion": False, "schedule_kind": "immediate", "timezone_name": "Europe/Moscow"}])
        for start in range(0, count, 500):
            tickets, messages, occurrences = [], [], []
            for i in range(start, min(start + 500, count)):
                ticket_id = f"00000000-0000-0000-0000-{i:012d}"
                tickets.append({"id": ticket_id, "ticket_number": i + 1, "company_id": "company", "client_id": f"client-{i % 300}",
                    "assignee_id": "admin", "subject": "topic-unique" if i == count - 1 else f"Request {i}",
                    "status": "closed", "created_at": now + timedelta(seconds=i), "updated_at": now + timedelta(seconds=i),
                    "call_center_profile_snapshot_json": {"profile_delta": {"custom_fields": {"custom_fio": "Анна Иванова"},
                        "contact_name": "Анна", "last_name": "Иванова"}} if i % 5 == 0 else None})
                for message in range(2):
                    messages.append({"id": f"message-{i}-{message}", "ticket_id": ticket_id, "message_type": "client",
                                     "body": f"Synthetic message {i} {message}", "created_at": now})
                if i % 10 == 0:
                    occurrences.append({"id": f"occurrence-{i}", "definition_id": "definition", "occurrence_at_utc": now + timedelta(seconds=i),
                        "ticket_id": ticket_id, "title_snapshot": "Synthetic", "description_snapshot": "Synthetic",
                        "creator_admin_id": "admin", "responsible_admin_id": "admin", "display_author": i % 20 == 0,
                        "effective_assignment_mode": "distribution", "notify_on_completion": False, "materialized_at_utc": now})
            connection.execute(insert(Ticket), tickets)
            connection.execute(insert(TicketMessage), messages)
            if occurrences:
                connection.execute(insert(InternalTaskOccurrence), occurrences)


def measure(engine, query):
    metrics = {"sql_queries": 0, "max_sql_parameters": 0, "max_parameter_bytes": 0,
               "ticket_in_ids": 0, "orm_tickets_loaded": 0, "peak_identity_map": 0}
    original = getattr(ticket_list, "_requester_search_ticket_ids", None)
    if original:
        def matching_ids(*args):
            result = original(*args)
            metrics["ticket_in_ids"] = len(result)
            return result
        ticket_list._requester_search_ticket_ids = matching_ids

    def sql(_conn, _cursor, _statement, parameters, _context, _many):
        values = list(parameters.values()) if isinstance(parameters, dict) else parameters
        metrics["sql_queries"] += 1
        metrics["max_sql_parameters"] = max(metrics["max_sql_parameters"], len(values))
        metrics["max_parameter_bytes"] = max(metrics["max_parameter_bytes"], sum(len(str(value).encode("utf-8")) for value in values))

    def loaded(session, instance):
        if isinstance(instance, Ticket):
            metrics["orm_tickets_loaded"] += 1
        metrics["peak_identity_map"] = max(metrics["peak_identity_map"], len(session.identity_map))

    event.listen(engine, "before_cursor_execute", sql)
    start = time.perf_counter()
    try:
        with Session(engine, autoflush=False) as db:
            event.listen(db, "loaded_as_persistent", loaded)
            result = ticket_list.list_tickets_paginated(db=db, user=SimpleNamespace(id="admin", role="admin"),
                statuses=["closed"], is_frozen=None, sort="created_at", order="asc", page=1, limit=20,
                filter_admin_assignees=False, query=query)
            metrics["total"] = result.total
            metrics["page_items"] = len(result.items)
    finally:
        metrics["seconds"] = round(time.perf_counter() - start, 4)
        event.remove(engine, "before_cursor_execute", sql)
        if original:
            ticket_list._requester_search_ticket_ids = original
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["sqlite", "postgresql", "both"], default="both")
    parser.add_argument("--sizes", type=int, nargs="+", default=[5000, 20000])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    backends = ["sqlite", "postgresql"] if args.backend == "both" else [args.backend]
    for backend in backends:
        for count in args.sizes:
            with database(backend) as engine:
                seed(engine, count)
                print(json.dumps({"seeded": backend, "tickets": count, "clients": 300, "messages": count * 2,
                                  "internal_tasks": count // 10}), flush=True)
                for query in ["topic-unique", "аНнА"]:
                    runs = [measure(engine, query) for _ in range(args.repeats)]
                    result = dict(runs[-1], backend=backend, tickets=count, query=query, repeats=args.repeats)
                    result["seconds"] = round(statistics.median(run["seconds"] for run in runs), 4)
                    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
