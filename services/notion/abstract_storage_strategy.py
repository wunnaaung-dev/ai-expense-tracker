from abc import ABC, abstractmethod
from typing import List
from schemas.expense import ExpenseData, BatchResult

class StorageStrategy(ABC):
    @abstractmethod
    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        ...