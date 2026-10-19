import os

from cryptography.fernet import Fernet

# Tests never read the developer's .env values for these: set them before any app import.
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
os.environ["JWT_SECRET"] = "test-jwt-secret"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["CREDENTIALS_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
os.environ["FRONTEND_URL"] = "http://frontend.test"

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from models import Base  # noqa: E402


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db(session_factory):
    async with session_factory() as session:
        yield session
