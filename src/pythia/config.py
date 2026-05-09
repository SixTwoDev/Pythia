from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    slack_bot_token: str = Field(min_length=1)
    slack_app_token: str = Field(min_length=1)

    openai_api_key: str = Field(min_length=1)
    openai_base_url: str = "https://openrouter.ai/api/v1"
    openai_model: str = Field(min_length=1)

    pythia_system_prompt_file: str | None = None
    mcp_servers_config: str | None = None
    codebase_repos: str | None = None


def load() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
