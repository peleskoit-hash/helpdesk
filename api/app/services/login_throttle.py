"""Persistent STAFF throttling; locks are held until the caller commits/rolls back."""
import hashlib
import math
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..models import StaffLoginThrottleBucket


def as_utc(value):
    # SQLite returns naive timestamps even for timezone-aware columns.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def delay_seconds(failure_count):
    return 30 * 2 ** (failure_count - 4) if failure_count >= 4 else 0


def _clear(limit):
    limit.failure_count = 0
    limit.last_failure_at = limit.blocked_until = None
    limit.identity_hash = None


def _lock(db, scope, key_hash):
    key = {"realm": "staff", "scope": scope, "key_hash": key_hash}
    table = StaffLoginThrottleBucket.__table__
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    statement = insert(table).values(**key, failure_count=0)
    db.execute(statement.on_conflict_do_update(
        index_elements=[table.c[name] for name in key],
        set_={"failure_count": table.c.failure_count},
    ))
    return db.scalar(select(StaffLoginThrottleBucket).filter_by(**key).execution_options(populate_existing=True))


class StaffLoginThrottle:
    def __init__(self, db, identity, source_ip, clock):
        self.identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        # Always lock account -> IP, also for previously unseen keys.
        self.account = _lock(db, "account", self.identity_hash)
        self.ip = _lock(db, "ip", hashlib.sha256(source_ip.encode("utf-8")).hexdigest())
        self.limits = [self.account, self.ip]
        self.now = clock()  # Read after acquiring all locks, never before waiting.
        self.retry_after = max((math.ceil((as_utc(limit.blocked_until) - self.now).total_seconds())
                                for limit in self.limits if limit.blocked_until), default=0)
        self.retry_after = max(self.retry_after, 0)
        if not self.retry_after:
            for limit in self.limits:
                if limit.last_failure_at:
                    period = max(delay_seconds(limit.failure_count), 30)
                    if self.now >= as_utc(limit.last_failure_at) + timedelta(seconds=3 * period):
                        _clear(limit)

    def failure(self):
        for limit in self.limits:
            limit.failure_count = min(limit.failure_count + 1, 12)
            delay = delay_seconds(limit.failure_count)
            limit.last_failure_at = self.now
            limit.blocked_until = self.now + timedelta(seconds=delay) if delay else None
        if self.ip.identity_hash is None:
            self.ip.identity_hash = self.identity_hash
        elif self.ip.identity_hash != self.identity_hash:
            self.ip.identity_hash = "mixed"
        return max(delay_seconds(limit.failure_count) for limit in self.limits)

    def success(self):
        for limit in self.limits:
            if limit is not self.ip or limit.identity_hash == self.identity_hash:
                _clear(limit)
