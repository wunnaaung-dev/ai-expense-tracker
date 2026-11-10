import asyncio
import weakref
from datetime import datetime, timezone
from typing import Dict, List, NamedTuple, Tuple

from cachetools import TTLCache

from schemas.expense import EntryFilter, ExpenseData
from services.storage.abstract_storage_strategy import StorageStrategy

# Ephemeral only (PRD: no long-term storage of financial data). Keyed by user id; the value
# carries the (provider, target_id) it was read from, so switching storage is a miss.
_records: TTLCache = TTLCache(maxsize=1024, ttl=300)
# One fetch at a time per user; weakly held so idle users' locks are collected.
_locks: "weakref.WeakValueDictionary[int, asyncio.Lock]" = weakref.WeakValueDictionary()
# Bumped by invalidate(): a fetch that started before it must not write its result back.
_generations: Dict[int, int] = {}

StorageTag = Tuple[str, str]


class _Entry(NamedTuple):
    tag: StorageTag
    fetched_at: datetime
    records: List[ExpenseData]


async def get_records(
    storage: StorageStrategy, user_id: int, tag: StorageTag, refresh: bool = False
) -> Tuple[List[ExpenseData], datetime]:
    """Every record of the user (all types, currencies and dates) and when it was read."""
    # Callers always get a shallow copy: the cached list is shared across requests.
    # The ExpenseData records inside it are shared too and must never be mutated.
    requested_at = datetime.now(timezone.utc)
    if not refresh:
        cached = _records.get(user_id)
        if cached is not None and cached.tag == tag:
            return list(cached.records), cached.fetched_at
    # Local strong ref: the weakly-held lock must outlive being awaited on.
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    # A held lock means a get_all is in flight; only its result post-dates requested_at.
    # (The clock is coarse, so fetched_at >= requested_at alone cannot tell "fetched just
    # before we asked" from "just after" — waiting for the lock is the reliable signal.)
    waited = lock.locked()
    async with lock:
        # Double-check: someone may have fetched while we waited for the lock.
        cached = _records.get(user_id)
        if cached is not None and cached.tag == tag and (
            not refresh or (waited and cached.fetched_at >= requested_at)
        ):
            return list(cached.records), cached.fetched_at
        generation = _generations.get(user_id, 0)
        records = await storage.get_all(EntryFilter())
        fetched_at = datetime.now(timezone.utc)
        # An invalidate() during the read wins: never cache data read before it.
        if _generations.get(user_id, 0) == generation:
            _records[user_id] = _Entry(tag=tag, fetched_at=fetched_at, records=records)
        return list(records), fetched_at


def invalidate(user_id: int) -> None:
    _records.pop(user_id, None)
    _generations[user_id] = _generations.get(user_id, 0) + 1


def clear() -> None:
    _records.clear()
    _generations.clear()
    _locks.clear()
