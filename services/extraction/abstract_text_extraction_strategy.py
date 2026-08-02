from abc import ABC, abstractmethod
from schemas.expense import ExpenseData


class TextExtractionStrategy(ABC):
    @abstractmethod
    async def extract_from_text(self, text: str) -> list[ExpenseData]:
        """Extract expense data from a text input."""
        raise NotImplementedError