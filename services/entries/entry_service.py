from typing import Callable, Optional

from constants.categories import CATEGORIES_BY_TYPE, DEFAULT_CATEGORY
from schemas.expense import (
    EntryCreate,
    EntryDraft,
    EntrySaveResponse,
    ExpenseData,
    ExpenseType,
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


def _voice_line(transcript: str, duration_seconds: Optional[int]) -> str:
    parts = ["Voice note"]
    if duration_seconds is not None:
        minutes, seconds = divmod(max(0, int(duration_seconds)), 60)
        parts.append(f"{minutes}:{seconds:02d}")
    if transcript:
        parts.append(f"“{transcript}”")
    return " · ".join(parts)


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
        # Clears the user's cached records so Insights shows the new entry.
        self.on_saved = on_saved

    # --- extract ----------------------------------------------------------------------

    async def extract_text(self, entry_type: ExpenseType, text: str) -> ExtractResponse:
        item = await self.text_extraction.extract_from_text(text, entry_type)
        return self._response(item, entry_type, "text", f"“{text.strip()}”")

    async def extract_slip(
        self, entry_type: ExpenseType, image_bytes: bytes, mime_type: str, filename: str
    ) -> ExtractResponse:
        slip_hash = await self.dedup.compute_hash(image_bytes)
        if await self.dedup.exists(slip_hash):
            return ExtractResponse(status="duplicate")  # Gemini is never called for a known slip
        item = await self.image_extraction.extract(image_bytes, mime_type, entry_type)
        return self._response(item, entry_type, "slip", filename, slip_hash)

    async def extract_voice(
        self, entry_type: ExpenseType, audio_bytes: bytes, mime_type: str, duration_seconds: Optional[int]
    ) -> ExtractResponse:
        item, transcript = await self.audio_extraction.extract_from_audio(audio_bytes, mime_type, entry_type)
        return self._response(item, entry_type, "voice", _voice_line(transcript, duration_seconds))

    def _response(
        self,
        item: Optional[ExpenseData],
        entry_type: ExpenseType,
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
                await self.dedup.release(entry.slip_hash)
            raise

        saved = results[0].data
        if self.on_saved:
            self.on_saved()
        try:
            storage_url = await storage.record_url(saved)
        except Exception:
            storage_url = None  # the row is saved; a missing link must not fail the save
        return EntrySaveResponse(entry=saved, storage_url=storage_url)
