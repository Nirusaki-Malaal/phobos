from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PHOBOS_", env_file=".env", extra="ignore")

    api_token: str = Field(min_length=24, repr=False)
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    data_dir: Path = Path("data")
    workspace_dir: Path = Path("workspace")
    extension_root: Path = Path(__file__).resolve().parents[2]
    gateway_url: str = "http://127.0.0.1:4000/v1"
    gateway_key: str = Field(default="", repr=False)
    model: str = ""
    telegram_token: str = Field(default="", repr=False)
    telegram_users: str = ""
    telegram_channel: str = ""
    telegram_project: str = "personal"
    report_hour_utc: int = Field(default=18, ge=0, le=23)
    skill_auto_install: bool = True
    skill_sources: str = (
        "morluto/rea,alchaincyf/huashu-motion,anthropics/skills,"
        "vercel-labs/agent-skills,openai/skills"
    )
    graph_memory: bool = True
    transcription_model_dir: Path = Path("/models/whisper")

    @property
    def trusted_skill_sources(self) -> set[str]:
        return {source.strip() for source in self.skill_sources.split(",") if source.strip()}

    @field_validator("gateway_url")
    @classmethod
    def validate_gateway(cls, value: str) -> str:
        from urllib.parse import urlparse

        url = urlparse(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
        ):
            raise ValueError("gateway URL must be HTTP(S) without embedded credentials")
        return value.rstrip("/")

    @property
    def allowed_users(self) -> set[int]:
        return {int(user.strip()) for user in self.telegram_users.split(",") if user.strip()}
