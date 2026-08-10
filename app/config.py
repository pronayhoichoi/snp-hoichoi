from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./snp.db"
    session_secret: str = "dev-secret-change-me"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    storage_dir: str = "./storage"
    max_upload_mb: int = 20

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
