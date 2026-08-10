from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./snp.db"
    session_secret: str = "dev-secret-change-me"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    storage_dir: str = "./storage"
    max_upload_mb: int = 20

    # Microsoft Entra ID (Azure AD) OAuth — set via env vars, never hardcoded.
    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_tenant_id: str = ""
    ms_redirect_uri: str = ""  # e.g. https://your-app.up.railway.app/auth/microsoft/callback
    ms_scopes: str = "User.Read"  # space-separated

    @property
    def ms_enabled(self) -> bool:
        return bool(self.ms_client_id and self.ms_client_secret and self.ms_tenant_id and self.ms_redirect_uri)

    @property
    def ms_authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.ms_tenant_id}"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
