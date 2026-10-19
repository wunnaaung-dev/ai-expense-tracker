from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:5173,"
    "https://ai-expense-tracker-xbae.onrender.com,"
    "https://ai-expense-tracker-frontend-8ptxvxpby-wunnas-projects.vercel.app,"
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
    jwt_secret: str = ""
    session_secret: str = ""
    credentials_encryption_key: str = ""
    cookie_secure: bool = False
    allowed_origins: str = _DEFAULT_ALLOWED_ORIGINS
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


settings = Settings()

