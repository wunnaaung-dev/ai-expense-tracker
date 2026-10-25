import csv
import io
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from core.dependencies import get_storage_service
from core.security import get_current_user
from main import app
from schemas.expense import ExpenseData
from services.export.exporter import HEADERS
from tests.test_expense_schema import RecordingStorage

RECORDS = [
    ExpenseData(date=date(2026, 10, 22), amount=3500, description="မုန့်ဟင်းခါး", category="Food",
                type="expense", currency="MMK", payment_channel="Cash", source="voice"),
    ExpenseData(date=date(2026, 10, 1), amount=45000.5, description="Salary", category="Salary",
                type="income", currency="THB", payment_channel="Bank", source="manual"),
]


class ExportStorage(RecordingStorage):
    filters = []

    async def get_all(self, filters):
        self.filters.append(filters)
        return list(RECORDS)


@pytest.fixture
def client():
    ExportStorage.filters = []
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_storage_service] = ExportStorage
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_csv_export(client):
    response = client.get("/api/v1/export", params={"format": "csv"})
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith('attachment; filename="expense-tracker-')
    assert response.content.startswith(b"\xef\xbb\xbf")  # BOM for Excel
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0] == HEADERS
    assert rows[1] == ["2026-10-22", "expense", "3500.0", "MMK", "Food", "မုန့်ဟင်းခါး", "Cash", "voice"]
    assert rows[2][:4] == ["2026-10-01", "income", "45000.5", "THB"]


def test_xlsx_export(client):
    response = client.get("/api/v1/export", params={"format": "xlsx"})
    assert response.status_code == 200
    sheet = load_workbook(io.BytesIO(response.content)).active
    rows = list(sheet.iter_rows(values_only=True))
    assert list(rows[0]) == HEADERS
    assert rows[1][:6] == ("2026-10-22", "expense", 3500, "MMK", "Food", "မုန့်ဟင်းခါး")
    assert len(rows) == 3


def test_filters_are_passed_and_type_is_optional(client):
    client.get("/api/v1/export", params={"date_from": "2026-10-01", "date_to": "2026-10-31", "currency": "MMK"})
    client.get("/api/v1/export/count", params={"type": "income"})
    both, income = ExportStorage.filters
    assert (both.entry_type, both.currency, both.date_from, both.date_to) == (None, "MMK", date(2026, 10, 1), date(2026, 10, 31))
    assert income.entry_type == "income"


def test_count(client):
    assert client.get("/api/v1/export/count").json() == {"count": 2}


@pytest.mark.parametrize("params", [
    {"date_from": "2026-10-31", "date_to": "2026-10-01"},
    {"format": "pdf"},
    {"currency": "EUR"},
    {"type": "both"},
])
def test_bad_params_are_422(client, params):
    assert client.get("/api/v1/export", params=params).status_code == 422
