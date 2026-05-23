import yaml

from book_alerter.config import Config


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
