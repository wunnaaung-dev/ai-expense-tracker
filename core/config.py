from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:5173,"
    "https://ai-expense-tracker-xbae.onrender.com,"
    "https://ai-expense-tracker-frontend-8ptxvxpby-wunnas-projects.vercel.app,"
    "https://ai-expense-tracker-frontend-bice.vercel.app"
)


class Settings(BaseSettings):
    gemini_api_key: str = ""
    redirect_uri: str = ""
    database_url: str = ""
    jwt_secret: str = ""
    credentials_encryption_key: str = ""
    cookie_secure: bool = False
    allowed_origins: str = _DEFAULT_ALLOWED_ORIGINS
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


settings = Settings()

