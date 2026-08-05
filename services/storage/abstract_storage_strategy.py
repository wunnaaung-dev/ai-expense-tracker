from abc import ABC, abstractmethod
from typing import List, Tuple, Optional
from schemas.expense import ExpenseData, BatchResult

class StorageStrategy(ABC):
    @abstractmethod
    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        ...

    @abstractmethod
    async def get_all(self, page_size: int, start_cursor: Optional[str] = None) -> Tuple[List[ExpenseData], Optional[str]]:
        ...