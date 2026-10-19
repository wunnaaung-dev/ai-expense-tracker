import pytest
from pydantic import ValidationError

from core.config import Settings


@pytest.mark.parametrize("missing", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"])
def test_settings_fail_fast_without_google_credentials(monkeypatch, missing):
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_reject_empty_google_credentials(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
