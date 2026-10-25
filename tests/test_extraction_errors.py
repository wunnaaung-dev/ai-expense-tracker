from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

from constants.ai_models import GEMINI_FALLBACK_MODEL, GEMINI_MODEL_FOR_OCR
from core.dependencies import get_storage_service, get_text_extraction_service
from core.security import get_current_user
from main import app
from services.extraction.errors import ExtractionUnavailableError
from services.extraction.gemini_extractor import GeminiExtractor
from services.storage.abstract_storage_strategy import StorageStrategy


def _extractor_raising(exc: Exception) -> GeminiExtractor:
    extractor = GeminiExtractor.__new__(GeminiExtractor)
    extractor.client = MagicMock()
    extractor.client.models.generate_content.side_effect = exc
    return extractor


def _server_error() -> genai_errors.ServerError:
    return genai_errors.ServerError(
        503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}}
    )


async def test_server_error_becomes_retryable_extraction_error():
    with pytest.raises(ExtractionUnavailableError) as info:
        await _extractor_raising(_server_error()).extract_from_text("lunch 120", "expense")
    assert info.value.retryable is True


async def test_falls_back_to_second_model_when_primary_is_busy():
    extractor = _extractor_raising(None)
    ok = MagicMock(text='{"date": "2026-10-22", "amount": 18, "description": "garlic"}')
    extractor.client.models.generate_content.side_effect = [_server_error(), ok]

    item = await extractor.extract_from_text("garlic 18", "expense")

    assert item.amount == 18
    models = [c.kwargs["model"] for c in extractor.client.models.generate_content.call_args_list]
    assert models == [GEMINI_MODEL_FOR_OCR, GEMINI_FALLBACK_MODEL]


async def test_malformed_json_becomes_retryable_extraction_error():
    extractor = _extractor_raising(None)
    extractor.client.models.generate_content.side_effect = None
    extractor.client.models.generate_content.return_value = MagicMock(
        text='[{"description": "\\u104"}]'
    )
    with pytest.raises(ExtractionUnavailableError) as info:
        await extractor.extract_from_text("garlic 18", "expense")
    assert info.value.retryable is True


async def test_client_error_does_not_fall_back():
    error = genai_errors.ClientError(
        400, {"error": {"code": 400, "message": "API key not valid", "status": "INVALID_ARGUMENT"}}
    )
    extractor = _extractor_raising(error)
    with pytest.raises(ExtractionUnavailableError):
        await extractor.extract_from_text("lunch 120", "expense")
    assert extractor.client.models.generate_content.call_count == 1


async def test_client_error_is_not_retryable():
    error = genai_errors.ClientError(
        400, {"error": {"code": 400, "message": "API key not valid", "status": "INVALID_ARGUMENT"}}
    )
    with pytest.raises(ExtractionUnavailableError) as info:
        await _extractor_raising(error).extract_from_text("lunch 120", "expense")
    assert info.value.retryable is False


class UnusedStorage(StorageStrategy):
    async def insert_many(self, items):
        raise AssertionError("storage must not be called")

    async def get_all(self, filters):
        return []


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, default_currency="THB")
    app.dependency_overrides[get_text_extraction_service] = lambda: _extractor_raising(_server_error())
    app.dependency_overrides[get_storage_service] = UnusedStorage
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_gemini_outage_returns_503_with_cors_header(client):
    response = client.post(
        "/api/v1/entries/extract",
        data={"type": "expense", "mode": "text", "text": "lunch 120"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "extraction_unavailable"
    assert response.json()["retryable"] is True
    assert "access-control-allow-origin" in response.headers
