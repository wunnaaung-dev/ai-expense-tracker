from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Local dev plus the deployed frontend. Environment-specific origins belong in
# ALLOWED_ORIGINS / ALLOWED_ORIGIN_REGEX, not hardcoded here.
_DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:5173,"
    "https://ai-expense-tracker-frontend-bice.vercel.app"
)


class Settings(BaseSettings):
    gemini_api_key: str = ""
    # Required: startup fails fast if these are missing.
    google_client_id: str = Field(min_length=1)
    google_client_secret: str = Field(min_length=1)
    redirect_uri: str = ""
    notion_client_id: str = ""
    notion_client_secret: str = ""
    notion_redirect_uri: str = ""
    frontend_url: str = "http://localhost:5173"
    database_url: str = ""
    jwt_secret: str = Field(min_length=32)
    session_secret: str = Field(min_length=32)
    credentials_encryption_key: str = Field(min_length=1)
    # None = derive from frontend_url (see secure_cookies).
    cookie_secure: bool | None = None
    allowed_origins: str = _DEFAULT_ALLOWED_ORIGINS
    # Optional extra CORS pattern; empty = unused. Project-scoped and anchored, e.g.
    # ^https://ai-expense-tracker-frontend-[a-z0-9]+-wunnas-projects\.vercel\.app$
    allowed_origin_regex: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("credentials_encryption_key")
    @classmethod
    def _valid_fernet_key(cls, value: str) -> str:
        try:
            Fernet(value.encode("utf-8"))
        except Exception as exc:  # InvalidToken/ValueError/TypeError from cryptography
            raise ValueError(
                "CREDENTIALS_ENCRYPTION_KEY must be a valid Fernet key; "
                'generate one with: python -c "from cryptography.fernet import '
                'Fernet; print(Fernet.generate_key().decode())"'
            ) from exc
        return value

    @property
    def secure_cookies(self) -> bool:
        """Cookies are Secure when explicitly asked for, or automatically on https."""
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.frontend_url.startswith("https://")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


settings = Settings()
