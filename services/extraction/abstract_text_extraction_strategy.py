from abc import ABC, abstractmethod
from typing import Optional

from schemas.expense import ExpenseData, EntryType


class TextExtractionStrategy(ABC):
    @abstractmethod
    async def extract_from_text(self, text: str, entry_type: EntryType) -> Optional[ExpenseData]:
        """One entry from a text input, or None if no amount was found."""
        raise NotImplementedError
