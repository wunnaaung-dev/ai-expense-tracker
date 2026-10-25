from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.dependencies import get_settings_service
from core.security import get_current_user
from main import app
from schemas.settings import UserSettingsUpdate
from services.settings.settings_service import SettingsService
from tests.test_expense_schema import FakeDb


def settings_service(**overrides):
    user = SimpleNamespace(
        id=1,
        **{"default_currency": "THB", "default_payment_channel": "Cash", "week_start": "monday", **overrides},
    )
    db = FakeDb()
    return SettingsService(db, user), db, user


# --- service ------------------------------------------------------------------------

def test_get_returns_the_users_settings():
    service, _, _ = settings_service(default_currency="MMK")
    assert service.get().model_dump() == {
        "default_currency": "MMK",
        "default_payment_channel": "Cash",
        "week_start": "monday",
    }


async def test_update_changes_only_the_fields_sent():
    service, db, user = settings_service()
    result = await service.update(UserSettingsUpdate(default_payment_channel="Bank"))
    assert (user.default_currency, user.default_payment_channel, user.week_start) == ("THB", "Bank", "monday")
    assert result.default_payment_channel == "Bank"
    assert db.commits == 1


async def test_update_without_changes_does_not_commit():
    service, db, _ = settings_service()
    await service.update(UserSettingsUpdate(default_currency="THB"))
    await service.update(UserSettingsUpdate())
    assert db.commits == 0


# --- router -------------------------------------------------------------------------

@pytest.fixture
def client():
    service, _, user = settings_service()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_settings_service] = lambda: service
    yield TestClient(app), user
    app.dependency_overrides.clear()


def test_get_settings_over_http(client):
    http, _ = client
    response = http.get("/api/v1/settings")
    assert response.status_code == 200
    assert response.json() == {"default_currency": "THB", "default_payment_channel": "Cash", "week_start": "monday"}


def test_patch_settings_over_http(client):
    http, user = client
    response = http.patch("/api/v1/settings", json={"default_currency": "MMK", "week_start": "sunday"})
    assert response.status_code == 200
    assert response.json() == {"default_currency": "MMK", "default_payment_channel": "Cash", "week_start": "sunday"}
    assert (user.default_currency, user.week_start) == ("MMK", "sunday")


@pytest.mark.parametrize("body", [
    {"default_currency": "EUR"},
    {"default_payment_channel": "Card"},
    {"week_start": "friday"},
    {"default_currency": None},
    {"unknown": "x"},
])
def test_patch_rejects_invalid_values(client, body):
    http, user = client
    assert http.patch("/api/v1/settings", json=body).status_code == 422
    assert (user.default_currency, user.default_payment_channel, user.week_start) == ("THB", "Cash", "monday")


def test_settings_require_login():
    assert TestClient(app).get("/api/v1/settings").status_code == 401
