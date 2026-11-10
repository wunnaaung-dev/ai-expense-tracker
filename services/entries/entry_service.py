import logging
from typing import Callable, Optional

from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from constants.categories import CATEGORIES_BY_TYPE, DEFAULT_CATEGORY
from constants.currencies import DEFAULT_CURRENCY
from constants.payment_channels import DEFAULT_PAYMENT_CHANNEL, PAYMENT_CHANNELS
from schemas.expense import (
    EntryCreate,
    EntryDraft,
    EntrySaveResponse,
    EntryUpdate,
    ExpenseData,
    EntryType,
    ExtractMode,
    ExtractResponse,
)
from services.dedup.hash_checker import DuplicateChecker
from services.entries.entry_defaults import EntryDefaults
from services.entries.errors import DuplicateEntryError
from services.extraction.abstract_audio_extraction_strategy import AudioExtractionStrategy
from services.extraction.abstract_image_extraction_strategy import ImageExtractionStrategy
from services.extraction.abstract_text_extraction_strategy import TextExtractionStrategy
from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.errors import StorageWriteError

logger = logging.getLogger(__name__)


def _voice_line(transcript: str, duration_seconds: Optional[int]) -> str:
    parts = ["Voice note"]
    if duration_seconds is not None:
        minutes, seconds = divmod(max(0, int(duration_seconds)), 60)
        parts.append(f"{minutes}:{seconds:02d}")
    if transcript:
        parts.append(f"“{transcript}”")
    return " · ".join(parts)


def _as_complete_entry(entry: ExpenseData) -> dict:
    """The merged record in the shape EntryCreate validates.

    Storage can legitimately hold no channel or source (Notion keeps neither) or a
    free-text channel from before the Cash/Bank dropdown (older Sheets rows). Neither
    takes part in any rule, so a permitted value stands in for them — and nothing built
    here is ever written back.
    """
    return {
        "type": entry.type,
        "amount": entry.amount,
        "currency": entry.currency or DEFAULT_CURRENCY,
        "date": entry.date,
        "category": entry.category,
        "description": entry.description,
        "payment_channel": (
            entry.payment_channel if entry.payment_channel in PAYMENT_CHANNELS else DEFAULT_PAYMENT_CHANNEL
        ),
        "source": entry.source or "manual",
    }


def _validate_as_entry(entry: ExpenseData) -> None:
    """Raise 422 unless the merged record would be acceptable as a newly saved entry."""
    try:
        EntryCreate(**_as_complete_entry(entry))
    except ValidationError as exc:
        # FastAPI only maps a ValidationError raised while parsing the request, so one
        # raised inside the endpoint has to be handed back as a request error itself.
        raise RequestValidationError(exc.errors()) from exc


class EntryService:
    """Two-step entry flow: extract returns a draft and never writes; save writes the reviewed entry."""

    def __init__(
        self,
        image_extraction: ImageExtractionStrategy,
        text_extraction: TextExtractionStrategy,
        audio_extraction: AudioExtractionStrategy,
        dedup: DuplicateChecker,
        defaults: EntryDefaults,
        on_saved: Optional[Callable[[], None]] = None,
    ):
        self.image_extraction = image_extraction
        self.text_extraction = text_extraction
        self.audio_extraction = audio_extraction
        self.dedup = dedup
        self.defaults = defaults
        # Clears the user's cached records so Insights and Dashboard reflect a change.
        self.on_saved = on_saved

    # --- extract ----------------------------------------------------------------------

    async def extract_text(self, entry_type: EntryType, text: str) -> ExtractResponse:
        item = await self.text_extraction.extract_from_text(text, entry_type)
        return self._response(item, entry_type, "text", f"“{text.strip()}”")

    async def extract_slip(
        self, entry_type: EntryType, image_bytes: bytes, mime_type: str, filename: str
    ) -> ExtractResponse:
        slip_hash = await self.dedup.compute_hash(image_bytes)
        if await self.dedup.exists(slip_hash):
            return ExtractResponse(status="duplicate")  # Gemini is never called for a known slip
        item = await self.image_extraction.extract(image_bytes, mime_type, entry_type)
        return self._response(item, entry_type, "slip", filename, slip_hash)

    async def extract_voice(
        self, entry_type: EntryType, audio_bytes: bytes, mime_type: str, duration_seconds: Optional[int]
    ) -> ExtractResponse:
        item, transcript = await self.audio_extraction.extract_from_audio(audio_bytes, mime_type, entry_type)
        return self._response(item, entry_type, "voice", _voice_line(transcript, duration_seconds))

    def _response(
        self,
        item: Optional[ExpenseData],
        entry_type: EntryType,
        mode: ExtractMode,
        source_line: str,
        slip_hash: Optional[str] = None,
    ) -> ExtractResponse:
        if item is None:
            return ExtractResponse(status="unreadable", mode=mode)

        currency, currency_source = self.defaults.currency(item.currency)
        channel, channel_source = self.defaults.payment_channel(item.payment_channel)
        categories = CATEGORIES_BY_TYPE[entry_type]
        draft = EntryDraft(
            type=entry_type,
            amount=item.amount,
            currency=currency,
            currency_source=currency_source,
            date=item.date,
            category=item.category if item.category in categories else DEFAULT_CATEGORY,
            description=item.description.strip(),
            payment_channel=channel,
            payment_channel_source=channel_source,
            source=mode,
            source_line=source_line,
            slip_hash=slip_hash,
        )
        return ExtractResponse(status="draft", draft=draft)

    # --- save -------------------------------------------------------------------------

    async def save(self, entry: EntryCreate, storage: StorageStrategy) -> EntrySaveResponse:
        # Claimed before writing so the same slip saved from two tabs lands only once.
        if entry.slip_hash and not await self.dedup.claim(entry.slip_hash):
            raise DuplicateEntryError(entry.slip_hash)

        item = ExpenseData(
            date=entry.date,
            amount=entry.amount,
            description=entry.description,
            category=entry.category,
            type=entry.type,
            payment_channel=entry.payment_channel,
            source=entry.source,
            currency=entry.currency,
        )
        try:
            results = await storage.insert_many([item])
            if not results or results[0].status != "success" or results[0].data is None:
                raise StorageWriteError(results[0].error if results else "Nothing was saved")
        except Exception:
            if entry.slip_hash:
                try:
                    await self.dedup.release(entry.slip_hash)
                except Exception:
                    # a failed release must not mask the write error or leave the hash locked
                    logger.exception(
                        "Failed to release slip hash %s after write failure; hash may be stuck",
                        entry.slip_hash,
                    )
            raise

        saved = results[0].data
        if self.on_saved:
            try:
                self.on_saved()
            except Exception:
                # the row is written; a stale cache must not fail the call
                logger.exception(
                    "on_saved failed for user %s; the entry is saved but the cache may be stale",
                    self.defaults.user.id,
                )
        return await self._with_url(saved, storage)

    # --- update / delete --------------------------------------------------------------

    async def update(self, record_id: str, patch: EntryUpdate, storage: StorageStrategy) -> EntrySaveResponse:
        """Change only the fields the client sent; the record must stay a valid entry."""
        existing = await storage.get_one(record_id)  # 404 when nothing carries the id
        changes = patch.model_dump(exclude_unset=True)
        if not changes:
            return await self._with_url(existing, storage)  # nothing sent, so nothing written

        merged = existing.model_copy(update=changes)
        _validate_as_entry(merged)

        await storage.update(record_id, changes)
        if self.on_saved:
            self.on_saved()
        return await self._with_url(merged, storage)

    async def delete(self, record_id: str, storage: StorageStrategy) -> None:
        await storage.delete(record_id)  # 404 when nothing carries the id
        if self.on_saved:
            self.on_saved()

    async def _with_url(self, entry: ExpenseData, storage: StorageStrategy) -> EntrySaveResponse:
        try:
            storage_url = await storage.record_url(entry)
        except Exception:
            storage_url = None  # the row is written; a missing link must not fail the call
        return EntrySaveResponse(entry=entry, storage_url=storage_url)
