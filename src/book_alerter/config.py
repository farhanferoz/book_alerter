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
    # Minimum calendar days of price history before BUY/WATCH/WAIT signals
    # can fire. Below this, the engine returns INSUFFICIENT_DATA regardless
    # of how many same-day observations have been collected. Gates on TIME
    # rather than COUNT because percentile-based signals are only meaningful
    # over a distribution of prices SPREAD across time — 24 identical
    # scrapes today tell us no more than 1 scrape today.
    min_days_of_history: int = 7
    # Legacy gate kept for backwards compatibility but no longer the
    # primary signal-fire condition. Set to 1 (any data at all) so the
    # days-of-history gate is what actually controls signal emergence.
    # Older configs that set this to 14 still validate; the days gate
    # supersedes it.
    min_observations_for_signal: int = 1
    buy_percentile: int = 10
    watch_percentile: int = 50
    target_tolerance_pct: int = 5
    alert_dedup_window_hours: int = 24
    # Window (days) over which percentile-based signals look at price
    # history. Per-book `Book.percentile_window_days` overrides this.
    percentile_window_days: int = 90
    # Terminal fallback for the shipping cascade in `compute_book_stats`.
    # Used when a row has no observed shipping, no per-(book, source)
    # median, no global (source, seller_class) median, and no per-book
    # median to fall back on. Tune to typical postage for your region;
    # default 280 = £2.80 (UK paperback baseline).
    default_shipping_minor: int = 280
    # Minimum bucket size for the (source, seller_class) global tier in
    # the shipping cascade. Buckets with fewer than this many observed
    # shipping rows are excluded — a small sample can mislead (e.g., 4
    # third-party Amazon rows that all happen to be free-shipping aren't
    # enough to assert "third-party Amazon ships free"). Below the
    # threshold the row falls through to the terminal default.
    min_global_median_observations: int = 10


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
    region: str = "UK"
    schedule: str = "0 */6 * * *"
    jitter_seconds: int = 600
    per_book_delay_seconds: tuple[int, int] = (5, 15)
    concurrency: int = Field(default=1, ge=1, le=5)
    timeout_seconds: int = 60
    max_consecutive_errors: int = 5


class MetadataConfig(BaseModel):
    """Metadata-lookup providers (used by /api/metadata/{lookup,search}).

    `google_books_api_key` supports `${GOOGLE_BOOKS_API_KEY}` env substitution
    via the same _substitute_env path config-wide. Empty string = anonymous
    quota (1000 req/day shared across all unauthenticated callers from the
    same IP — exhausted fast in practice).

    `amazon_uk_fallback` enables a Playwright-based dp-page scrape when OL +
    GB both return no data. Slow (~10-20s, one-shot), but covers UK trade
    titles that are missing from both providers.
    """

    google_books_api_key: str = ""
    amazon_uk_fallback: bool = True


class BackupConfig(BaseModel):
    """Weekly SQLite backup job.

    Default: Sunday 03:00 UTC, retain 7 most recent backups in `data/backups/`.
    """

    enabled: bool = True
    schedule: str = "0 3 * * 0"  # Sundays at 03:00 UTC
    directory: str = "data/backups"
    retain: int = Field(default=7, ge=1)


class Config(BaseModel):
    config_version: int = 1
    recommendation: RecommendationConfig = Field(default_factory=RecommendationConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    metadata: MetadataConfig = Field(default_factory=MetadataConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)

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
