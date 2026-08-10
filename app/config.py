from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./snp.db"
    session_secret: str = "dev-secret-change-me"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    storage_dir: str = "./storage"
    max_upload_mb: int = 20

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
