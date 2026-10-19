from datetime import date

import pytest
from fastapi.testclient import TestClient

from constants.storage_status import (
    STATUS_MISSING,
    STATUS_READY,
    STATUS_SCHEMA_BROKEN,
)
from core.dependencies import get_sheets_provisioning_service, get_sheets_storage
from core.security import get_current_user
from main import app
from schemas.expense import ExpenseData
from services.google.errors import GoogleApiError
from services.google_sheets import google_sheets_storage
from services.google_sheets.errors import (
    RecordNotFoundError,
    SheetSchemaBrokenError,
    SpreadsheetMissingError,
)
from services.google_sheets.google_sheets_storage import GoogleSheetsStorage
from services.google_sheets.schema import build_setup_requests, build_spreadsheet_body
from services.storage.errors import StorageWriteError
from tests.fake_google import FakeGoogle
from tests.test_provisioning import FakeUser, StubProvisioning


@pytest.fixture(autouse=True)
def clear_column_cache():
    google_sheets_storage._column_cache.clear()
    yield
    google_sheets_storage._column_cache.clear()


class StatusLog:
    def __init__(self):
        self.statuses = []

    async def __call__(self, status):
        if not self.statuses or self.statuses[-1] != status:
            self.statuses.append(status)

    @property
    def last(self):
        return self.statuses[-1] if self.statuses else None


@pytest.fixture
async def fake():
    fake = FakeGoogle()
    created = await fake.create_spreadsheet(build_spreadsheet_body())
    sheet_id = created["sheets"][0]["properties"]["sheetId"]
    await fake.batch_update(created["spreadsheetId"], build_setup_requests(sheet_id))
    return fake


@pytest.fixture
def status_log():
    return StatusLog()


@pytest.fixture
def storage(fake, status_log):
    return GoogleSheetsStorage(fake, fake.only().spreadsheet_id, user_id=1, on_status=status_log)


def expense(description, amount=10.0, day=1, **extra):
    return ExpenseData(date=date(2026, 10, day), amount=amount, description=description, **extra)


async def insert(storage, *items):
    results = await storage.insert_many(list(items))
    return [r.data.id for r in results]


async def all_records(storage):
    # Simulates the cache TTL expiring after a user edit.
    storage.invalidate_columns()
    records, _ = await storage.get_all(page_size=100)
    return {r.description: r for r in records}


# --- basic CRUD ------------------------------------------------------------------

async def test_insert_assigns_uuid_and_writes_typed_cells(storage, fake):
    (record_id,) = await insert(storage, expense("lunch", 120.5, payment_channel="KBank", source="text"))

    row = fake.values_by_header()[0]
    assert row["ID"] == record_id and len(record_id) == 36
    assert row["Date"] == (date(2026, 10, 1) - date(1899, 12, 30)).days  # a real date serial
    assert row["Amount"] == 120.5
    assert row["Type"] == "expense"
    assert row["Payment Channel"] == "KBank"
    assert row["Source"] == "text"


async def test_crud_roundtrip(storage):
    lunch_id, taxi_id = await insert(storage, expense("lunch", day=2), expense("taxi", day=3))

    records, cursor = await storage.get_all(page_size=10)
    assert [r.description for r in records] == ["taxi", "lunch"]  # newest first
    assert cursor is None

    await storage.update(lunch_id, {"amount": 99, "category": "Food", "type": "income"})
    await storage.delete(taxi_id)

    records = await all_records(storage)
    assert list(records) == ["lunch"]
    assert records["lunch"].amount == 99
    assert records["lunch"].category == "Food"
    assert records["lunch"].type == "income"


async def test_get_all_paginates(storage):
    await insert(storage, *[expense(f"e{day}", day=day) for day in range(1, 6)])
    page1, cursor = await storage.get_all(page_size=2)
    page2, cursor2 = await storage.get_all(page_size=2, start_cursor=cursor)
    _, cursor3 = await storage.get_all(page_size=2, start_cursor=cursor2)
    assert [r.description for r in page1] == ["e5", "e4"]
    assert [r.description for r in page2] == ["e3", "e2"]
    assert cursor3 is None


async def test_unknown_record_raises(storage):
    await insert(storage, expense("lunch"))
    with pytest.raises(RecordNotFoundError):
        await storage.update("no-such-id", {"amount": 1})
    with pytest.raises(RecordNotFoundError):
        await storage.delete("no-such-id")


async def test_update_rejects_id_and_unknown_fields(storage):
    (record_id,) = await insert(storage, expense("lunch"))
    with pytest.raises(ValueError):
        await storage.update(record_id, {"id": "other"})
    with pytest.raises(ValueError):
        await storage.update(record_id, {"colour": "red"})


# --- user edits ------------------------------------------------------------------

async def test_header_renamed(storage, fake):
    (record_id,) = await insert(storage, expense("lunch"))
    fake.rename_header("Amount", "Price (THB)")
    fake.rename_header("Description", "What")

    await insert(storage, expense("dinner", 50))
    await storage.update(record_id, {"amount": 75})

    records = await all_records(storage)
    assert records["lunch"].amount == 75
    assert records["dinner"].amount == 50
    assert [row["Price (THB)"] for row in fake.values_by_header()] == [75, 50]


async def test_columns_reordered(storage, fake):
    (record_id,) = await insert(storage, expense("lunch", 10))
    fake.move_column("Amount", 0)
    fake.move_column("ID", 7)

    await insert(storage, expense("dinner", 20))
    await storage.update(record_id, {"description": "brunch"})

    rows = fake.values_by_header()
    assert fake.main_tab().grid()[0][0] == "Amount"
    assert [(r["Description"], r["Amount"]) for r in rows] == [("brunch", 10), ("dinner", 20)]
    records = await all_records(storage)
    assert records["dinner"].amount == 20


async def test_tab_renamed(storage, fake):
    (record_id,) = await insert(storage, expense("lunch"))
    fake.rename_tab("My money")

    await insert(storage, expense("dinner"))
    await storage.delete(record_id)

    assert list(await all_records(storage)) == ["dinner"]


async def test_custom_column_is_preserved(storage, fake):
    (record_id,) = await insert(storage, expense("lunch"))
    fake.insert_column(2, "Notes", fill="keep me")

    await insert(storage, expense("dinner"))
    await storage.update(record_id, {"amount": 33, "description": "late lunch"})

    rows = fake.values_by_header()
    assert rows[0]["Notes"] == "keep me"
    assert rows[0]["Description"] == "late lunch"
    assert rows[1].get("Notes") is None  # new rows leave the user's column blank


async def test_rows_sorted_update_and_delete_hit_right_row(storage, fake):
    a, b, c = await insert(storage, expense("a", 1), expense("b", 2), expense("c", 3))
    fake.sort_rows("Description", reverse=True)  # c, b, a

    await storage.update(a, {"amount": 100})
    await storage.delete(c)

    rows = fake.values_by_header()
    assert [(r["Description"], r["Amount"]) for r in rows] == [("b", 2), ("a", 100)]


async def test_delete_rechecks_row_when_rows_move(storage, fake):
    a, b = await insert(storage, expense("a"), expense("b"))
    original = fake.get_grid_values
    calls = {"n": 0}

    async def sort_after_locate(spreadsheet_id, grid_range):
        values = await original(spreadsheet_id, grid_range)
        calls["n"] += 1
        if calls["n"] == 1:  # the user sorts between our locate and delete
            fake.sort_rows("Description", reverse=True)
        return values

    fake.get_grid_values = sort_after_locate
    await storage.delete(a)

    assert [r["Description"] for r in fake.values_by_header()] == ["b"]


async def test_optional_column_deleted(storage, fake):
    (record_id,) = await insert(storage, expense("lunch", payment_channel="cash"))
    fake.delete_column("Payment Channel")

    await insert(storage, expense("dinner", payment_channel="card"))
    await storage.update(record_id, {"payment_channel": "card", "amount": 5})

    records = await all_records(storage)
    assert records["lunch"].payment_channel is None
    assert records["lunch"].amount == 5
    assert records["dinner"].payment_channel is None


async def test_required_column_deleted_then_repaired(storage, fake, status_log):
    await insert(storage, expense("lunch"))
    fake.delete_column("Amount")

    with pytest.raises(SheetSchemaBrokenError) as error:
        await insert(storage, expense("dinner"))
    assert error.value.missing == ["amount"]
    assert status_log.last == STATUS_SCHEMA_BROKEN

    await storage.repair()
    assert status_log.last == STATUS_READY
    assert fake.main_tab().grid()[0][-1] == "Amount"

    await insert(storage, expense("dinner", 42))
    assert (await all_records(storage))["dinner"].amount == 42


async def test_transactions_tab_deleted_then_repaired(storage, fake, status_log):
    spreadsheet = fake.only()
    old_tab = fake.main_tab()
    spreadsheet.tabs.remove(old_tab)
    spreadsheet.metadata = [m for m in spreadsheet.metadata if m.get("sheet_id") != old_tab.sheet_id]

    with pytest.raises(SheetSchemaBrokenError):
        await storage.get_all(page_size=10)
    assert status_log.last == STATUS_SCHEMA_BROKEN

    await storage.repair()
    await insert(storage, expense("lunch"))
    assert fake.main_tab().title == "Transactions"
    assert list(await all_records(storage)) == ["lunch"]


async def test_duplicated_tab_writes_go_to_original(storage, fake):
    await insert(storage, expense("lunch"))
    copy_tab = fake.duplicate_tab()

    await insert(storage, expense("dinner"))

    assert [r["Description"] for r in fake.values_by_header()] == ["lunch", "dinner"]
    assert [r["Description"] for r in fake.values_by_header(copy_tab)] == ["lunch"]


async def test_writes_ignore_a_stale_cached_mapping(storage, fake):
    await storage.resolve_columns()  # cached
    fake.move_column("Amount", 0)

    await insert(storage, expense("lunch", 12))

    assert fake.values_by_header()[0]["Amount"] == 12


# --- missing spreadsheet / API failures ------------------------------------------

@pytest.mark.parametrize("how", ["deleted", "trashed"])
async def test_spreadsheet_gone_sets_missing(storage, fake, status_log, how):
    setattr(fake.only(), how, True)

    with pytest.raises(SpreadsheetMissingError):
        await storage.get_all(page_size=10)
    assert status_log.last == STATUS_MISSING


async def test_api_error_on_append_is_storage_write_error(storage, fake):
    await storage.resolve_columns()
    fake.fail["batch_update"] = GoogleApiError(500, "backend error")
    with pytest.raises(StorageWriteError):
        await insert(storage, expense("lunch"))


# --- repair endpoint ---------------------------------------------------------------

class StubStorage:
    def __init__(self, error=None):
        self.error = error

    async def repair(self):
        if self.error:
            raise self.error


@pytest.fixture
def client_with_storage():
    def make(storage_stub):
        app.dependency_overrides[get_current_user] = FakeUser
        app.dependency_overrides[get_sheets_storage] = lambda: storage_stub
        app.dependency_overrides[get_sheets_provisioning_service] = lambda: StubProvisioning(STATUS_READY)
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def test_repair_endpoint_returns_status(client_with_storage):
    response = client_with_storage(StubStorage()).post("/api/v1/storage/repair")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_repair_endpoint_maps_missing_spreadsheet(client_with_storage):
    response = client_with_storage(StubStorage(SpreadsheetMissingError("x"))).post("/api/v1/storage/repair")
    assert response.status_code == 409
    assert response.json() == {"code": "storage_missing"}
