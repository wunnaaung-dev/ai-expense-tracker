from abc import ABC, abstractmethod
from typing import Optional

from schemas.expense import ExpenseData, ExpenseType


class AudioExtractionStrategy(ABC):
    @abstractmethod
    async def extract_from_audio(
        self, audio_bytes: bytes, mime_type: str, entry_type: ExpenseType
    ) -> tuple[Optional[ExpenseData], str]:
        """One entry from a voice recording (None if no amount was heard), plus the transcript."""
        raise NotImplementedError
