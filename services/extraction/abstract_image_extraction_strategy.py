from abc import ABC, abstractmethod
from typing import Optional

from schemas.expense import ExpenseData, ExpenseType


class ImageExtractionStrategy(ABC):
    @abstractmethod
    async def extract(self, image_bytes: bytes, mime_type: str, entry_type: ExpenseType) -> Optional[ExpenseData]:
        """One entry from a slip image, or None if it isn't readable."""
        raise NotImplementedError
