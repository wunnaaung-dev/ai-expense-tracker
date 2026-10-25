from datetime import datetime, timezone
from typing import List, Tuple

from cachetools import TTLCache

from schemas.expense import EntryFilter, ExpenseData
from services.storage.abstract_storage_strategy import StorageStrategy

# Ephemeral only (PRD: no long-term storage of financial data). Keyed by user id; the value
# carries the (provider, target_id) it was read from, so switching storage is a miss.
_records: TTLCache = TTLCache(maxsize=1024, ttl=300)

StorageTag = Tuple[str, str]


async def get_records(
    storage: StorageStrategy, user_id: int, tag: StorageTag, refresh: bool = False
) -> Tuple[List[ExpenseData], datetime]:
    """Every record of the user (all types, currencies and dates) and when it was read."""
    cached = None if refresh else _records.get(user_id)
    if cached is not None and cached[0] == tag:
        return cached[2], cached[1]
    records = await storage.get_all(EntryFilter())
    fetched_at = datetime.now(timezone.utc)
    _records[user_id] = (tag, fetched_at, records)
    return records, fetched_at


def invalidate(user_id: int) -> None:
    _records.pop(user_id, None)


def clear() -> None:
    _records.clear()
