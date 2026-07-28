from abc import ABC, abstractmethod
from schemas.expense import ExpenseData

class ExtractionStrategy(ABC):
    @abstractmethod
    async def extract(self, image_bytes: bytes, mime_type: str) -> ExpenseData:
        """Extract expense data from a slip image."""
        raise NotImplementedError