from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    notion_token: str = ""
    notion_database_id: str = ""
    gemini_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
