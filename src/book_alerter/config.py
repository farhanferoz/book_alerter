from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), "")
        return _ENV_REF.sub(repl, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


class RecommendationConfig(BaseModel):
    min_observations_for_signal: int = 14
    buy_percentile: int = 25
    watch_percentile: int = 50
    target_tolerance_pct: int = 5
    alert_dedup_window_hours: int = 24


class QuietHours(BaseModel):
    start: str = "22:00"
    end: str = "08:00"
    tz: str = "Europe/London"


class InAppChannelConfig(BaseModel):
    enabled: bool = True


class NtfyChannelConfig(BaseModel):
    enabled: bool = False
    server: str = "https://ntfy.sh"
    topic: str = ""
    priority: str = "default"
    tags: list[str] = Field(default_factory=lambda: ["book", "money"])


class NotificationChannels(BaseModel):
    inapp: InAppChannelConfig = Field(default_factory=InAppChannelConfig)
    ntfy: NtfyChannelConfig = Field(default_factory=NtfyChannelConfig)


AlertKind = Literal["target_hit", "percentile_cross", "new_low"]


class NotificationsConfig(BaseModel):
    alert_kinds_enabled: list[AlertKind] = Field(
        default_factory=lambda: ["target_hit", "percentile_cross", "new_low"]
    )
    quiet_hours: QuietHours | None = Field(default_factory=QuietHours)
    channels: NotificationChannels = Field(default_factory=NotificationChannels)


class SourceConfig(BaseModel):
    enabled: bool = True
    type: Literal["subprocess", "inline"] = "subprocess"
    binary: str | None = None
    region: str = "UK"
    schedule: str = "0 */6 * * *"
    jitter_seconds: int = 600
    per_book_delay_seconds: tuple[int, int] = (5, 15)
    concurrency: int = Field(default=1, ge=1, le=5)
    timeout_seconds: int = 60
    max_consecutive_errors: int = 5


class Config(BaseModel):
    config_version: int = 1
    recommendation: RecommendationConfig = Field(default_factory=RecommendationConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text()) or {}
        raw = _substitute_env(raw)
        return cls.model_validate(raw)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False))
        tmp.replace(path)
