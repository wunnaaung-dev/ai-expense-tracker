from abc import ABC, abstractmethod
from typing import Optional

from schemas.expense import ExpenseData, ExpenseType


class TextExtractionStrategy(ABC):
    @abstractmethod
    async def extract_from_text(self, text: str, entry_type: ExpenseType) -> Optional[ExpenseData]:
        """One entry from a text input, or None if no amount was found."""
        raise NotImplementedError
