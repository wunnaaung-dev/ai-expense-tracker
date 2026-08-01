import asyncio
import hashlib
import aiosqlite
from pathlib import Path

from core.db import DB_PATH


class DuplicateChecker:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
    
    @staticmethod
    def compute_hash_sync(file_bytes: bytes) -> str:
        return hashlib.sha256(file_bytes).hexdigest()
    
    async def compute_hash(self, file_bytes: bytes) -> str:
        return await asyncio.to_thread(self.compute_hash_sync, file_bytes)
    
    async def exists(self, file_hash: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM processed_hashes WHERE file_hash = ?",
                (file_hash,)
            )
            row = await cursor.fetchone()
            return row is not None
        
    async def mark_processed(self, file_hash: str, filename: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO processed_hashes (file_hash, filename) VALUES (?, ?)",
                    (file_hash, filename)
                )
                await db.commit()
            except aiosqlite.IntegrityError:
                # Hash already exists, ignore
                pass