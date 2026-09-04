import pytest
import yaml
from pydantic import ValidationError

from book_alerter.config import Config, SchedulerConfig


def test_config_defaults_when_no_file(tmp_path):
    cfg = Config.load(tmp_path / "missing.yaml")
    assert cfg.recommendation.buy_percentile == 10
    assert cfg.recommendation.percentile_window_days == 90
    assert cfg.recommendation.min_days_of_history == 7
    assert cfg.recommendation.min_observations_for_signal == 1
    assert cfg.notifications.alert_kinds_enabled == ["target_hit", "percentile_cross", "new_low"]
    assert set(cfg.sources) == {"wob", "bookfinder", "amazon", "amazon_uk_product"}
    assert all(s.enabled for s in cfg.sources.values())
    assert cfg.sources["amazon_uk_product"].item_kinds == ["product"]
    assert cfg.sources["wob"].item_kinds == ["book"]


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "config_version": 1,
        "recommendation": {"buy_percentile": 20},
    }))
    cfg = Config.load(path)
    assert cfg.recommendation.buy_percentile == 20

    cfg.save(path)
    cfg2 = Config.load(path)
    assert cfg2.recommendation.buy_percentile == 20
    assert cfg2.config_version == 1


def test_config_env_substitution(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_NTFY_TOPIC", "secret-topic")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "notifications": {"channels": {"ntfy": {"enabled": True, "topic": "${MY_NTFY_TOPIC}"}}},
    }))
    cfg = Config.load(path)
    assert cfg.notifications.channels.ntfy.topic == "secret-topic"


def test_default_source_schedules_are_staggered() -> None:
    """T1.4. All four defaulted to `0 */6 * * *`, so every Playwright-backed
    source launched a browser on the same instant — the load spike
    `max_concurrent_browsers` caps, and a needlessly synchronised burst of
    traffic at Amazon from one address."""
    sources = Config().sources
    minutes = [s.schedule.split()[0] for s in sources.values()]
    assert sorted(minutes) == ["0", "15", "30", "45"]
    assert len(set(minutes)) == len(minutes), "no two sources may share a slot"


def test_max_concurrent_browsers_defaults_to_two_and_rejects_zero() -> None:
    """Zero would deadlock every source at the first `start()` rather than
    failing loudly, so the floor is enforced by the model, not by a caller."""
    assert Config().scheduler.max_concurrent_browsers == 2
    with pytest.raises(ValidationError):
        SchedulerConfig(max_concurrent_browsers=0)
