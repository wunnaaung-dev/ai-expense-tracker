"""Single-flight get_records: one get_all per user at a time, and no caching across invalidate()."""
import asyncio
from typing import List

import pytest

from schemas.expense import EntryFilter, ExpenseData
from services.storage import record_cache
from services.storage.abstract_storage_strategy import StorageStrategy

TAG = ("google_sheets", "sheet-1")
OTHER_TAG = ("google_sheets", "sheet-2")
REC = ExpenseData(date="2026-10-19", amount=120, description="lunch")


async def _until(predicate, steps: int = 200) -> None:
    for _ in range(steps):
        if predicate():
            return
        await asyncio.sleep(0)
    pytest.fail("condition never became true")


class FakeStorage(StorageStrategy):
    """Holds get_all open on an event so tests can interleave callers."""

    def __init__(self, records=()):
        self.records: List[ExpenseData] = list(records)
        self.calls = 0
        self.entered = asyncio.Event()  # set when get_all starts
        self.release = None  # event get_all waits on before returning, when set
        self.error = None  # raised instead of returning, when set

    async def get_all(self, filters: EntryFilter) -> List[ExpenseData]:
        self.calls += 1
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return list(self.records)

    async def insert_many(self, items):
        raise NotImplementedError

    async def get_one(self, record_id):
        raise NotImplementedError

    async def update(self, record_id, changes):
        raise NotImplementedError

    async def delete(self, record_id):
        raise NotImplementedError


async def test_concurrent_cold_reads_share_one_get_all():
    storage = FakeStorage([REC])
    storage.release = asyncio.Event()
    first = asyncio.create_task(record_cache.get_records(storage, 1, TAG))
    await storage.entered.wait()  # first holds the user's lock inside get_all
    second = asyncio.create_task(record_cache.get_records(storage, 1, TAG))
    for _ in range(5):
        await asyncio.sleep(0)
    assert not second.done()  # cache is still cold, so it waits on the lock
    storage.release.set()
    r1, r2 = await asyncio.gather(first, second)
    assert storage.calls == 1
    assert r1[0] == r2[0] == [REC]
    assert r1[1] == r2[1]  # second reused first's entry


async def test_different_users_do_not_share_a_lock():
    storage = FakeStorage([REC])
    storage.release = asyncio.Event()
    first = asyncio.create_task(record_cache.get_records(storage, 1, TAG))
    await storage.entered.wait()
    second = asyncio.create_task(record_cache.get_records(storage, 2, TAG))
    await _until(lambda: storage.calls == 2)  # user 2 fetched while user 1 is held open
    assert not first.done()
    storage.release.set()
    await asyncio.gather(first, second)
    assert storage.calls == 2


async def test_concurrent_refresh_reuses_the_in_flight_result():
    storage = FakeStorage([REC])
    storage.release = asyncio.Event()
    first = asyncio.create_task(record_cache.get_records(storage, 1, TAG, refresh=True))
    await storage.entered.wait()
    second = asyncio.create_task(record_cache.get_records(storage, 1, TAG, refresh=True))
    for _ in range(5):
        await asyncio.sleep(0)
    assert not second.done()
    storage.release.set()
    r1, r2 = await asyncio.gather(first, second)
    assert storage.calls == 1
    assert r1[0] == r2[0] == [REC]


async def test_invalidate_during_a_fetch_is_not_cached():
    storage = FakeStorage([REC])
    storage.release = asyncio.Event()
    task = asyncio.create_task(record_cache.get_records(storage, 1, TAG))
    await storage.entered.wait()
    record_cache.invalidate(1)
    storage.release.set()
    records, _fetched_at = await task
    assert records == [REC]  # still returned to the caller
    assert storage.calls == 1

    again, _ = await record_cache.get_records(storage, 1, TAG)
    assert storage.calls == 2  # the invalidated result was not cached
    assert again == [REC]
    await record_cache.get_records(storage, 1, TAG)
    assert storage.calls == 2  # this one was


async def test_get_all_failure_caches_nothing():
    storage = FakeStorage()
    storage.error = RuntimeError("backend down")
    with pytest.raises(RuntimeError, match="backend down"):
        await record_cache.get_records(storage, 1, TAG)
    assert storage.calls == 1

    storage.error = None
    storage.records = [REC]
    records, _ = await record_cache.get_records(storage, 1, TAG)
    assert storage.calls == 2
    assert records == [REC]

    await record_cache.get_records(storage, 1, TAG)
    assert storage.calls == 2  # the retry was cached


async def test_tag_mismatch_inside_the_lock_still_fetches():
    storage = FakeStorage([REC])
    storage.release = asyncio.Event()
    first = asyncio.create_task(record_cache.get_records(storage, 1, TAG))
    await storage.entered.wait()
    second = asyncio.create_task(record_cache.get_records(storage, 1, OTHER_TAG))
    for _ in range(5):
        await asyncio.sleep(0)
    assert not second.done()
    storage.release.set()
    r1, r2 = await asyncio.gather(first, second)
    assert storage.calls == 2  # second's double-check saw TAG, wanted OTHER_TAG
    assert r1[0] == r2[0] == [REC]


async def test_mutating_the_returned_list_does_not_touch_the_cache():
    storage = FakeStorage([REC])
    records, _ = await record_cache.get_records(storage, 1, TAG)
    records.append(ExpenseData(date="2026-10-20", amount=1, description="added"))
    records.sort(key=lambda record: record.amount)
    records.clear()

    again, _ = await record_cache.get_records(storage, 1, TAG)
    assert storage.calls == 1  # still a cache hit
    assert again == [REC]  # the cached list was never touched


async def test_callers_get_distinct_list_objects():
    storage = FakeStorage([REC])
    first, _ = await record_cache.get_records(storage, 1, TAG)
    second, _ = await record_cache.get_records(storage, 1, TAG)
    assert first is not second
    assert first == second == [REC]
    assert first[0] is second[0]  # shallow copy: the records themselves are shared
