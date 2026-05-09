from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    slack_bot_token: str = Field(min_length=1)
    slack_app_token: str = Field(min_length=1)


def load() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
