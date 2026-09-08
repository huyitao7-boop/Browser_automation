from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .shared.paths import resolve_from_project


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    browser_executable_path: Path | None = None
    browser_headless: bool = True
    artifact_root: Path = Path("artifacts")
    workflow_root: Path = Path("workflows")
    action_timeout_ms: int = Field(default=15_000, ge=100)
    navigation_timeout_ms: int = Field(default=30_000, ge=100)
    max_concurrent_runs: int = Field(default=4, ge=1, le=32)
    max_browser_sessions: int = Field(default=4, ge=1, le=16)
    max_exploration_steps: int = Field(default=20, ge=1, le=100)
    max_exploration_failures: int = Field(default=3, ge=1, le=10)

    @property
    def resolved_artifact_root(self) -> Path:
        return resolve_from_project(self.artifact_root)

    @property
    def resolved_workflow_root(self) -> Path:
        return resolve_from_project(self.workflow_root)

    @property
    def resolved_browser_executable(self) -> Path | None:
        if self.browser_executable_path is None:
            return None
        return resolve_from_project(self.browser_executable_path)
