from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TYNODE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./tynode.db"
    api_key: Optional[str] = None
    cors_origins: List[str] = ["*"]


settings = Settings()