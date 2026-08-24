import asyncio
import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.storage import ExpensesDedup


class DuplicateChecker:
    def __init__(self, db: AsyncSession, user_id: int):
        self.db = db
        self.user_id = user_id

    @staticmethod
    def compute_hash_sync(file_bytes: bytes) -> str:
        return hashlib.sha256(file_bytes).hexdigest()

    async def compute_hash(self, file_bytes: bytes) -> str:
        return await asyncio.to_thread(self.compute_hash_sync, file_bytes)

    async def exists(self, file_hash: str) -> bool:
        result = await self.db.execute(
            select(ExpensesDedup.id).where(
                ExpensesDedup.user_id == self.user_id,
                ExpensesDedup.hash == file_hash,
            )
        )
        return result.scalar_one_or_none() is not None

    async def mark_processed(self, file_hash: str) -> None:
        self.db.add(ExpensesDedup(user_id=self.user_id, hash=file_hash))
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
