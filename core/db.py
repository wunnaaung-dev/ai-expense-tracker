import os
import aiosqlite
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typing import AsyncGenerator

load_dotenv()
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_hashes (
                file_hash TEXT PRIMARY KEY,
                filename TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()

raw_database_url = os.getenv("DATABASE_URL", "").replace(
    "postgresql://",
    "postgresql+asyncpg://",
    1,
)
url = make_url(raw_database_url)
query = dict(url.query)
query.pop("sslmode", None)
query.pop("channel_binding", None)
database_url = url.set(query=query)

engine = create_async_engine(
    database_url,
    connect_args={
        "ssl": True,
        "statement_cache_size": 0,
    },
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session