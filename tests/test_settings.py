import pytest
from cryptography.fernet import Fernet
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


@pytest.mark.parametrize("missing", ["JWT_SECRET", "SESSION_SECRET"])
def test_settings_require_secrets(monkeypatch, missing):
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("secret", ["JWT_SECRET", "SESSION_SECRET"])
def test_settings_reject_short_secrets(monkeypatch, secret):
    monkeypatch.setenv(secret, "too-short")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_reject_invalid_fernet_key(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", "not-a-fernet-key")
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    assert "Fernet" in str(excinfo.value)


def test_settings_reject_empty_credentials_key(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_accepts_valid_fernet_key(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert Settings(_env_file=None).credentials_encryption_key


def test_secure_cookies_derived_from_https_frontend(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    settings = Settings(_env_file=None)
    assert settings.cookie_secure is None
    assert settings.secure_cookies is True


def test_secure_cookies_derived_from_http_frontend(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    settings = Settings(_env_file=None)
    assert settings.cookie_secure is None
    assert settings.secure_cookies is False


def test_explicit_cookie_secure_overrides_derivation(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    assert Settings(_env_file=None).secure_cookies is False

    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    assert Settings(_env_file=None).secure_cookies is True


def test_allowed_origin_regex_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGIN_REGEX", raising=False)
    assert Settings(_env_file=None).allowed_origin_regex == ""


def test_allowed_origin_regex_is_parsed(monkeypatch):
    pattern = r"^https://ai-expense-tracker-frontend-[a-z0-9]+-wunnas-projects\.vercel\.app$"
    monkeypatch.setenv("ALLOWED_ORIGIN_REGEX", pattern)
    assert Settings(_env_file=None).allowed_origin_regex == pattern


def test_cors_origins_drop_the_removed_preview_and_backend_urls(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    origins = Settings(_env_file=None).cors_origins
    assert "http://localhost:5173" in origins
    assert not any("onrender.com" in origin for origin in origins)
    assert not any("8ptxvxpby" in origin for origin in origins)
