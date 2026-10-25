import httpx
import pytest

from services.google.errors import GoogleApiError
from services.google.google_api import GoogleApi


def api_returning(status: int, body: dict | str) -> GoogleApi:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return GoogleApi("token", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_error_info_reason_is_parsed():
    api = api_returning(403, {"error": {
        "code": 403,
        "message": "Google Drive API has not been used in project 1 before or it is disabled.",
        "status": "PERMISSION_DENIED",
        "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED"}],
    }})

    with pytest.raises(GoogleApiError) as info:
        await api.get_file("abc")

    assert info.value.error_reason == "SERVICE_DISABLED"
    assert str(info.value) == (
        "Google API error 403 (SERVICE_DISABLED): "
        "Google Drive API has not been used in project 1 before or it is disabled."
    )


async def test_legacy_errors_reason_is_used_without_details():
    api = api_returning(403, {"error": {"message": "Rate limit", "errors": [{"reason": "rateLimitExceeded"}]}})

    with pytest.raises(GoogleApiError) as info:
        await api.get_file("abc")

    assert info.value.error_reason == "rateLimitExceeded"
    assert info.value.reason == "Rate limit"


async def test_non_json_error_keeps_body_text():
    api = api_returning(502, "Bad Gateway")

    with pytest.raises(GoogleApiError) as info:
        await api.get_file("abc")

    assert str(info.value) == "Google API error 502: Bad Gateway"
