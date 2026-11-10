from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from schemas.expense import EntryFilter, ExpenseData, BatchResult

class StorageStrategy(ABC):
    @abstractmethod
    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        ...

    @abstractmethod
    async def get_all(self, filters: EntryFilter) -> List[ExpenseData]:
        """Every record matching `filters`, newest first. Paging is done by the caller."""
        ...

    @abstractmethod
    async def get_one(self, record_id: str) -> ExpenseData:
        """The record with this `id`, for reading the values a PATCH merges into.

        Raises RecordNotFoundError when no record carries the id.
        """
        ...

    @abstractmethod
    async def update(self, record_id: str, changes: Dict[str, Any]) -> None:
        """Write only the named fields of one record; every other field is left alone.

        Raises RecordNotFoundError when no record carries the id.
        """
        ...

    @abstractmethod
    async def delete(self, record_id: str) -> None:
        """Remove one record. Raises RecordNotFoundError when no record carries the id."""
        ...

    async def record_url(self, record: ExpenseData) -> Optional[str]:
        """A link that opens the saved record in the user's storage, if the backend can build one."""
        return None
