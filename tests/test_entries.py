import hashlib
from datetime import date
from types import SimpleNamespace
from typing import Optional

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from core import clock
from core.dependencies import get_entry_service, get_storage_service, get_storage_tag
from core.security import get_current_user
from main import app
from schemas.expense import EntryCreate, EntryDraft, EntrySaveResponse, EntryUpdate, ExpenseData
from services.entries.entry_defaults import EntryDefaults
from services.entries.entry_service import EntryService
from services.entries.errors import DuplicateEntryError
from services.extraction.abstract_audio_extraction_strategy import AudioExtractionStrategy
from services.extraction.abstract_image_extraction_strategy import ImageExtractionStrategy
from services.extraction.abstract_text_extraction_strategy import TextExtractionStrategy
from services.storage.errors import RecordNotFoundError, StorageWriteError
from tests.test_expense_schema import FakeDb, RecordingStorage


class FakeExtractor(ImageExtractionStrategy, TextExtractionStrategy, AudioExtractionStrategy):
    def __init__(self, item: Optional[ExpenseData], transcript: str = ""):
        self.item = item
        self.transcript = transcript
        self.calls: list[tuple] = []

    def _result(self):
        return self.item.model_copy() if self.item else None

    async def extract(self, image_bytes, mime_type, entry_type):
        self.calls.append(("slip", entry_type))
        return self._result()

    async def extract_from_text(self, text, entry_type):
        self.calls.append(("text", entry_type))
        return self._result()

    async def extract_from_audio(self, audio_bytes, mime_type, entry_type):
        self.calls.append(("voice", entry_type))
        return self._result(), self.transcript


class FakeDedup:
    def __init__(self, saved=()):
        self.hashes = set(saved)

    async def compute_hash(self, file_bytes: bytes) -> str:
        return hashlib.sha256(file_bytes).hexdigest()

    async def exists(self, file_hash: str) -> bool:
        return file_hash in self.hashes

    async def claim(self, file_hash: str) -> bool:
        if file_hash in self.hashes:
            return False
        self.hashes.add(file_hash)
        return True

    async def release(self, file_hash: str) -> None:
        self.hashes.discard(file_hash)


def entry_service(extractor: FakeExtractor, currency="THB", channel="Cash", dedup=None, on_saved=None):
    db = FakeDb()
    user = SimpleNamespace(id=1, default_currency=currency, default_payment_channel=channel)
    service = EntryService(
        image_extraction=extractor,
        text_extraction=extractor,
        audio_extraction=extractor,
        dedup=dedup or FakeDedup(),
        defaults=EntryDefaults(user),
        on_saved=on_saved,
    )
    return service, db, user


def entry_from(draft: EntryDraft, **changes) -> EntryCreate:
    """What the client sends back after the review screen."""
    fields = draft.model_dump(exclude={"currency_source", "payment_channel_source", "source_line"})
    return EntryCreate(**{**fields, **changes})


def expense(description="Lunch", amount=120.0, **extra):
    return ExpenseData(date=date(2026, 10, 22), amount=amount, description=description, **extra)


class FailingStorage(RecordingStorage):
    async def insert_many(self, items):
        raise StorageWriteError("backend down")


class CountingStorage(RecordingStorage):
    """Counts writes so a test can prove exactly one insert happened."""

    def __init__(self):
        super().__init__()
        self.insert_calls = 0

    async def insert_many(self, items):
        self.insert_calls += 1
        return await super().insert_many(items)


class CountingDedup(FakeDedup):
    """FakeDedup that records how often release() was called."""

    def __init__(self, saved=()):
        super().__init__(saved)
        self.releases = 0

    async def release(self, file_hash: str) -> None:
        self.releases += 1
        await super().release(file_hash)


class BrokenReleaseDedup(CountingDedup):
    """release() fails the way a DB error would, after the attempt is counted."""

    async def release(self, file_hash: str) -> None:
        self.releases += 1
        raise RuntimeError("dedup release failed")


def raising_on_saved():
    """The cache-invalidation hook blowing up after the row is already written."""
    raise RuntimeError("cache invalidate failed")


# --- extract ------------------------------------------------------------------------

async def test_extract_text_returns_draft_and_never_writes():
    extractor = FakeExtractor(expense("  Lunch ", category="Food", currency="THB", payment_channel="Bank"))
    service, db, _ = entry_service(extractor)

    response = await service.extract_text("expense", "Lunch 120 baht by KBank")

    assert response.status == "draft"
    draft = response.draft
    assert (draft.amount, draft.currency, draft.currency_source) == (120, "THB", "detected")
    assert (draft.category, draft.payment_channel, draft.description) == ("Food", "Bank", "Lunch")
    assert (draft.type, draft.source, draft.slip_hash) == ("expense", "text", None)
    assert draft.source_line == "“Lunch 120 baht by KBank”"
    assert extractor.calls == [("text", "expense")]
    assert draft.payment_channel_source == "detected"
    assert db.commits == 0  # nothing stored


async def test_extract_fills_defaults():
    # No currency, an expense category for an income, and a channel outside Cash/Bank.
    extractor = FakeExtractor(expense(category="Food", payment_channel="KBZPay app"))
    service, _, _ = entry_service(extractor, currency="MMK", channel="Bank")
    draft = (await service.extract_text("income", "got 5000")).draft
    assert (draft.currency, draft.currency_source) == ("MMK", "default")
    assert draft.category == "General"
    assert (draft.payment_channel, draft.payment_channel_source) == ("Bank", "default")
    assert draft.type == "income"


@pytest.mark.parametrize("mode", ["text", "slip", "voice"])
async def test_no_amount_is_unreadable(mode):
    service, _, _ = entry_service(FakeExtractor(None))
    if mode == "text":
        response = await service.extract_text("expense", "hello")
    elif mode == "slip":
        response = await service.extract_slip("expense", b"img", "image/png", "a.png")
    else:
        response = await service.extract_voice("expense", b"audio", "audio/webm", 4)
    assert (response.status, response.mode, response.draft) == ("unreadable", mode, None)


async def test_slip_draft_carries_hash_and_filename():
    service, _, _ = entry_service(FakeExtractor(expense()))
    draft = (await service.extract_slip("expense", b"img", "image/png", "slip.png")).draft
    assert draft.slip_hash == hashlib.sha256(b"img").hexdigest()
    assert (draft.source, draft.source_line) == ("slip", "slip.png")


async def test_saved_slip_is_duplicate_without_calling_gemini():
    extractor = FakeExtractor(expense())
    service, _, _ = entry_service(extractor, dedup=FakeDedup({hashlib.sha256(b"img").hexdigest()}))
    response = await service.extract_slip("expense", b"img", "image/png", "slip.png")
    assert response.status == "duplicate"
    assert extractor.calls == []


async def test_voice_source_line():
    service, _, _ = entry_service(FakeExtractor(expense(), transcript="lunch 120 baht"))
    draft = (await service.extract_voice("expense", b"a", "audio/webm", 4)).draft
    assert draft.source_line == "Voice note · 0:04 · “lunch 120 baht”"
    draft = (await service.extract_voice("expense", b"a", "audio/webm", None)).draft
    assert draft.source_line == "Voice note · “lunch 120 baht”"


def test_today_is_bangkok_time():
    assert clock.BANGKOK.utcoffset(None).total_seconds() == 7 * 3600


# --- save ---------------------------------------------------------------------------

async def test_save_writes_the_reviewed_entry():
    service, _, user = entry_service(FakeExtractor(expense(currency="THB")))
    draft = (await service.extract_text("expense", "Lunch 120")).draft
    storage = RecordingStorage()

    response = await service.save(entry_from(draft, amount=150, currency="USD", description=" Dinner "), storage)

    (saved,) = storage.saved
    assert (saved.amount, saved.currency, saved.description, saved.source) == (150, "USD", "Dinner", "text")
    assert response.entry.amount == 150
    assert user.default_currency == "THB"  # a setting, not changed by saving


async def test_manual_entry_saves_with_source_manual():
    service, _, _ = entry_service(FakeExtractor(None))
    storage = RecordingStorage()
    entry = EntryCreate(
        type="income", amount=5000, currency="MMK", date=date(2026, 10, 22),
        category="Salary", payment_channel="Bank", source="manual",
    )
    await service.save(entry, storage)
    assert (storage.saved[0].source, storage.saved[0].type) == ("manual", "income")


async def test_same_slip_saved_twice_is_duplicate():
    service, _, _ = entry_service(FakeExtractor(expense()))
    draft = (await service.extract_slip("expense", b"img", "image/png", "s.png")).draft
    storage = RecordingStorage()

    await service.save(entry_from(draft), storage)  # tab 1
    with pytest.raises(DuplicateEntryError):
        await service.save(entry_from(draft), storage)  # tab 2
    assert len(storage.saved) == 1


async def test_failed_write_releases_the_slip():
    dedup = FakeDedup()
    service, _, user = entry_service(FakeExtractor(expense(currency="USD")), dedup=dedup)
    draft = (await service.extract_slip("expense", b"img", "image/png", "s.png")).draft

    with pytest.raises(StorageWriteError):
        await service.save(entry_from(draft), FailingStorage())
    assert dedup.hashes == set()  # can be saved again


async def test_on_saved_failure_still_returns_the_saved_entry(caplog):
    dedup = FakeDedup()
    service, _, _ = entry_service(FakeExtractor(expense()), dedup=dedup, on_saved=raising_on_saved)
    draft = (await service.extract_slip("expense", b"img", "image/png", "s.png")).draft
    storage = CountingStorage()

    response = await service.save(entry_from(draft), storage)

    assert isinstance(response, EntrySaveResponse)  # the row is written, so the call succeeds
    assert response.entry.id == storage.saved[0].id
    assert storage.insert_calls == 1
    assert "on_saved failed for user 1" in caplog.text  # logged, not raised
    assert "cache invalidate failed" in caplog.text


async def test_on_saved_failure_does_not_double_write_text_entries(caplog):
    service, _, _ = entry_service(FakeExtractor(expense()), on_saved=raising_on_saved)
    draft = (await service.extract_text("expense", "Lunch 120")).draft
    storage = CountingStorage()

    response = await service.save(entry_from(draft), storage)

    assert draft.slip_hash is None  # nothing dedups this flow, so a retry must not re-insert
    assert isinstance(response, EntrySaveResponse)
    assert storage.insert_calls == 1
    assert "on_saved failed for user 1" in caplog.text


async def test_release_failure_does_not_mask_the_write_error(caplog):
    dedup = BrokenReleaseDedup()
    service, _, _ = entry_service(FakeExtractor(expense()), dedup=dedup)
    draft = (await service.extract_slip("expense", b"img", "image/png", "s.png")).draft

    with pytest.raises(StorageWriteError, match="backend down"):  # not the release error
        await service.save(entry_from(draft), FailingStorage())

    assert dedup.releases == 1  # attempted, but it cannot mask the write error
    assert "hash may be stuck" in caplog.text
    assert "dedup release failed" in caplog.text


async def test_failed_write_with_a_working_release_still_frees_the_hash(caplog):
    dedup = CountingDedup()
    service, _, _ = entry_service(FakeExtractor(expense()), dedup=dedup)
    draft = (await service.extract_slip("expense", b"img", "image/png", "s.png")).draft

    with pytest.raises(StorageWriteError, match="backend down"):
        await service.save(entry_from(draft), FailingStorage())

    assert dedup.releases == 1
    assert dedup.hashes == set()  # can be saved again
    assert "hash may be stuck" not in caplog.text


@pytest.mark.parametrize("changes", [
    {"type": "income", "category": "Food"},
    {"category": "Salary"},
    {"payment_channel": "KBank"},
    {"currency": "MMK", "amount": 3500.5},
    {"currency": "THB", "amount": 1.234},
    {"amount": 0},
])
def test_invalid_entry_rejected(changes):
    fields = dict(type="expense", amount=120, currency="THB", date="2026-10-22",
                  category="Food", payment_channel="Cash", source="text")
    with pytest.raises(ValueError):
        EntryCreate(**{**fields, **changes})


def test_valid_amounts_accepted():
    base = dict(type="expense", date="2026-10-22", category="Food", payment_channel="Cash", source="text")
    EntryCreate(**base, amount=3500, currency="MMK")
    EntryCreate(**base, amount=12.5, currency="USD")
    EntryCreate(**base, amount=120.25, currency="THB")


# --- update / delete ---------------------------------------------------------------

async def saved_entry(service, storage, **overrides):
    """One record already in storage, the way a PATCH or DELETE finds it."""
    fields = dict(type="expense", amount=120, currency="THB", date=date(2026, 10, 22),
                  category="Food", description="Lunch", payment_channel="Cash", source="text")
    await service.save(EntryCreate(**{**fields, **overrides}), storage)
    return storage.saved[0].id


async def test_update_changes_only_the_fields_sent():
    service, _, _ = entry_service(FakeExtractor(None))
    storage = RecordingStorage()
    record_id = await saved_entry(service, storage)

    response = await service.update(record_id, EntryUpdate(amount=150, category="Transportation"), storage)

    assert (response.entry.amount, response.entry.category) == (150, "Transportation")
    assert (response.entry.description, response.entry.currency) == ("Lunch", "THB")  # left out
    assert (storage.saved[0].amount, storage.saved[0].category) == (150, "Transportation")


async def test_update_can_move_an_entry_between_types():
    service, _, _ = entry_service(FakeExtractor(None))
    storage = RecordingStorage()
    record_id = await saved_entry(service, storage)

    response = await service.update(record_id, EntryUpdate(type="income", category="Salary"), storage)

    assert (response.entry.type, response.entry.category) == ("income", "Salary")


async def test_update_rejects_a_category_the_resulting_type_disallows():
    service, _, _ = entry_service(FakeExtractor(None))
    storage = RecordingStorage()
    record_id = await saved_entry(service, storage)  # Food, an expense-only category

    with pytest.raises(RequestValidationError):
        await service.update(record_id, EntryUpdate(type="income"), storage)

    assert storage.saved[0].type == "expense"  # nothing was written


async def test_update_rejects_an_amount_the_currency_cannot_hold():
    service, _, _ = entry_service(FakeExtractor(None))
    storage = RecordingStorage()
    record_id = await saved_entry(service, storage, currency="MMK")

    with pytest.raises(RequestValidationError):
        await service.update(record_id, EntryUpdate(amount=3500.5), storage)  # MMK takes whole numbers

    assert storage.saved[0].amount == 120


async def test_update_keeps_a_legacy_channel_it_cannot_validate():
    # Older Sheets rows carry a channel from before the Cash/Bank dropdown and Notion
    # keeps none at all. Neither takes part in a rule, so a plain PATCH must still land
    # — and the placeholder used for validation must never be written back.
    service, _, _ = entry_service(FakeExtractor(None))
    storage = RecordingStorage()
    record_id = await saved_entry(service, storage)
    storage.saved[0] = storage.saved[0].model_copy(update={"payment_channel": "KBank", "source": None})

    response = await service.update(record_id, EntryUpdate(description="Team lunch"), storage)

    assert response.entry.description == "Team lunch"
    assert storage.saved[0].payment_channel == "KBank"
    assert storage.saved[0].source is None


async def test_update_of_an_unknown_record_is_not_found():
    service, _, _ = entry_service(FakeExtractor(None))
    with pytest.raises(RecordNotFoundError):
        await service.update("nope", EntryUpdate(amount=1), RecordingStorage())


async def test_empty_update_writes_nothing():
    service, _, _ = entry_service(FakeExtractor(None))
    storage = RecordingStorage()
    record_id = await saved_entry(service, storage)
    before = storage.saved[0]

    response = await service.update(record_id, EntryUpdate(), storage)

    assert response.entry == before
    assert storage.saved[0] == before


async def test_update_and_delete_clear_the_records_cache():
    cleared = []
    service, _, _ = entry_service(FakeExtractor(None), on_saved=lambda: cleared.append(1))
    storage = RecordingStorage()
    record_id = await saved_entry(service, storage)
    cleared.clear()  # the save clears too

    await service.update(record_id, EntryUpdate(amount=1), storage)
    assert len(cleared) == 1

    await service.delete(record_id, storage)
    assert len(cleared) == 2
    assert storage.saved == []


async def test_delete_of_an_unknown_record_is_not_found():
    service, _, _ = entry_service(FakeExtractor(None))
    with pytest.raises(RecordNotFoundError):
        await service.delete("nope", RecordingStorage())


# --- router -------------------------------------------------------------------------

@pytest.fixture
def client():
    extractor = FakeExtractor(expense(category="Food", currency="THB", payment_channel="Bank"))
    storage = RecordingStorage()
    service, _, _ = entry_service(extractor)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, default_currency="THB")
    app.dependency_overrides[get_entry_service] = lambda: service
    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_storage_tag] = lambda: ("google_sheets", "sheet-1")
    yield TestClient(app), storage, extractor
    app.dependency_overrides.clear()


def test_extract_then_save_over_http(client):
    http, storage, _ = client
    response = http.post("/api/v1/entries/extract", data={"type": "expense", "mode": "text", "text": "Lunch 120"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft"
    assert storage.saved == []

    entry = {k: v for k, v in body["draft"].items() if k not in ("currency_source", "payment_channel_source", "source_line")}
    response = http.post("/api/v1/entries", json=entry)
    assert response.status_code == 201
    assert response.json()["entry"]["description"] == "Lunch"
    assert len(storage.saved) == 1


def test_extract_slip_and_voice_over_http(client):
    http, _, extractor = client
    response = http.post(
        "/api/v1/entries/extract",
        data={"type": "expense", "mode": "slip"},
        files={"file": ("s.png", b"img", "image/png")},
    )
    assert response.json()["draft"]["slip_hash"]
    response = http.post(
        "/api/v1/entries/extract",
        data={"type": "income", "mode": "voice", "duration_seconds": "4"},
        files={"file": ("v.webm", b"aud", "audio/webm")},
    )
    assert response.json()["draft"]["source_line"].startswith("Voice note · 0:04")
    assert extractor.calls == [("slip", "expense"), ("voice", "income")]


@pytest.mark.parametrize("data, files", [
    ({"type": "expense", "mode": "text"}, None),
    ({"type": "expense", "mode": "slip"}, None),
    ({"type": "expense", "mode": "slip"}, {"file": ("a.txt", b"x", "text/plain")}),
    ({"type": "expense", "mode": "voice"}, {"file": ("a.png", b"x", "image/png")}),
])
def test_extract_input_must_match_mode(client, data, files):
    http, _, _ = client
    assert http.post("/api/v1/entries/extract", data=data, files=files).status_code == 400


def test_save_rejects_wrong_category_for_type(client):
    http, storage, _ = client
    response = http.post("/api/v1/entries", json={
        "type": "income", "amount": 100, "currency": "THB", "date": "2026-10-22",
        "category": "Food", "payment_channel": "Cash", "source": "manual",
    })
    assert response.status_code == 422
    assert storage.saved == []


def test_duplicate_save_returns_409(client):
    http, _, _ = client
    entry = {
        "type": "expense", "amount": 100, "currency": "THB", "date": "2026-10-22",
        "category": "Food", "payment_channel": "Cash", "source": "slip", "slip_hash": "abc",
    }
    assert http.post("/api/v1/entries", json=entry).status_code == 201
    response = http.post("/api/v1/entries", json=entry)
    assert response.status_code == 409
    assert response.json() == {"code": "duplicate_entry"}


def test_old_auto_save_endpoints_are_gone(client):
    http, _, _ = client
    for path in ("/api/v1/expense/text", "/api/v1/expense/upload", "/api/v1/expense/voice"):
        assert http.post(path).status_code in (404, 405)


def _create(http, **overrides):
    entry = {"type": "expense", "amount": 120, "currency": "THB", "date": "2026-10-22",
             "category": "Food", "description": "Lunch", "payment_channel": "Cash", "source": "text"}
    response = http.post("/api/v1/entries", json={**entry, **overrides})
    assert response.status_code == 201
    return response.json()["entry"]["id"]


def test_patch_then_delete_over_http(client):
    http, storage, _ = client
    record_id = _create(http)

    response = http.patch(f"/api/v1/entries/{record_id}", json={"amount": 150, "category": "Transportation"})
    assert response.status_code == 200
    entry = response.json()["entry"]
    assert (entry["amount"], entry["category"], entry["description"]) == (150, "Transportation", "Lunch")

    assert http.delete(f"/api/v1/entries/{record_id}").status_code == 204
    assert storage.saved == []
    assert http.get("/api/v1/expense").json()["totalElements"] == 0


def test_patch_unknown_id_is_404(client):
    http, _, _ = client
    response = http.patch("/api/v1/entries/no-such-id", json={"amount": 1})
    assert response.status_code == 404
    assert response.json() == {"code": "record_not_found"}


def test_delete_unknown_id_is_404(client):
    http, _, _ = client
    response = http.delete("/api/v1/entries/no-such-id")
    assert response.status_code == 404
    assert response.json() == {"code": "record_not_found"}

    record_id = _create(http)
    assert http.delete(f"/api/v1/entries/{record_id}").status_code == 204
    assert http.delete(f"/api/v1/entries/{record_id}").status_code == 404


def test_patch_rejects_unknown_fields_and_explicit_nulls(client):
    http, storage, _ = client
    record_id = _create(http)

    for body in ({"id": "other"}, {"source": "voice"}, {"colour": "red"}, {"amount": None}):
        assert http.patch(f"/api/v1/entries/{record_id}", json=body).status_code == 422

    assert storage.saved[0].amount == 120  # nothing was written


def test_patch_that_would_leave_an_invalid_entry_is_422(client):
    http, storage, _ = client
    record_id = _create(http)  # Food is an expense-only category

    assert http.patch(f"/api/v1/entries/{record_id}", json={"type": "income"}).status_code == 422
    assert storage.saved[0].type == "expense"  # nothing was written


def test_empty_patch_returns_the_entry_unchanged(client):
    http, storage, _ = client
    record_id = _create(http)
    before = storage.saved[0]

    response = http.patch(f"/api/v1/entries/{record_id}", json={})

    assert response.status_code == 200
    assert response.json()["entry"]["id"] == record_id
    assert storage.saved[0] == before


# --- DuplicateChecker on a real DB --------------------------------------------------

async def test_claim_is_exclusive_and_release_frees_it(db):
    from services.dedup.hash_checker import DuplicateChecker

    dedup = DuplicateChecker(db, user_id=1)
    assert await dedup.claim("h") is True
    assert await dedup.claim("h") is False
    assert await dedup.exists("h")

    await dedup.release("h")
    assert not await dedup.exists("h")
    assert await dedup.claim("h") is True
