from abc import ABC, abstractmethod
from typing import List, Optional
from schemas.expense import EntryFilter, ExpenseData, BatchResult

class StorageStrategy(ABC):
    @abstractmethod
    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        ...

    @abstractmethod
    async def get_all(self, filters: EntryFilter) -> List[ExpenseData]:
        """Every record matching `filters`, newest first. Paging is done by the caller."""
        ...

    async def record_url(self, record: ExpenseData) -> Optional[str]:
        """A link that opens the saved record in the user's storage, if the backend can build one."""
        return None
