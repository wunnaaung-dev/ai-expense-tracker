from abc import ABC, abstractmethod
from ast import List
from typing import List
from schemas.expense import ExpenseData


class AudioExtractionStrategy(ABC):
    @abstractmethod
    async def extract_from_audio(self, audio_bytes: bytes, mime_type: str) -> List[ExpenseData]:
        """Extract expense data from a voice recording."""
        raise NotImplementedError