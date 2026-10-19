"""Two simultaneous first logins must create one spreadsheet.

Needs real Postgres row-level behaviour, so it runs only with TEST_DATABASE_URL
(e.g. a Neon branch). Tables are created in a throwaway schema and dropped after.
"""
import asyncio
import os
import uuid

import pytest
from dotenv import dotenv_values
from sqlalchemy import func, select, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from constants.storage_status import STATUS_READY
from models import Base
from models.storage import UserStorageConfig
from models.users import User
from services.auth.auth_service import GOOGLE_LOGIN_OK, AuthService
from services.google_sheets.provisioning import SheetsProvisioningService
from tests.fake_google import FakeGoogle
from tests.test_google_login import google_token

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def _async_url_and_ssl(raw_url: str):
    # Same rewriting as core/db.py, but SSL only when the URL asks for it.
    url = make_url(raw_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    return url.set(query=query), sslmode not in (None, "disable")


@pytest.fixture
async def pg_session_factory():
    if TEST_DATABASE_URL == dotenv_values(".env").get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must not point at the app's DATABASE_URL")

    url, ssl = _async_url_and_ssl(TEST_DATABASE_URL)
    schema = f"test_{uuid.uuid4().hex[:10]}"
    connect_args = {"ssl": ssl, "statement_cache_size": 0}

    admin = create_async_engine(url, connect_args=connect_args)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        url,
        connect_args={**connect_args, "server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_concurrent_first_logins_create_one_spreadsheet(pg_session_factory):
    fake = FakeGoogle()
    # Keep the first request inside provisioning while the second one arrives.
    fake.create_delay = 0.5

    async def api_factory(config):
        return fake

    async def login():
        async with pg_session_factory() as session:
            service = AuthService(session, SheetsProvisioningService(session, api_factory))
            result = await service.login_with_google(google_token())
            return result.outcome

    outcomes = await asyncio.gather(login(), login())

    assert outcomes == [GOOGLE_LOGIN_OK, GOOGLE_LOGIN_OK]
    assert fake.create_calls == 1
    async with pg_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        config = (await session.execute(select(UserStorageConfig))).scalar_one()
        assert config.storage_status == STATUS_READY
        assert config.target_id == fake.only().spreadsheet_id
