"""Integration tests for the Config endpoints (Task 7.5)."""

from __future__ import annotations

from pathlib import Path

import yaml

from book_alerter.config import Config

# --- GET /api/config ---------------------------------------------------------


def test_get_config_returns_current(api_client):
    resp = api_client.get("/api/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # `Config.load(<missing-path>)` returns defaults → version 1.
    assert body["config_version"] == 1
    assert "recommendation" in body
    assert "notifications" in body
    assert "sources" in body


# --- GET /api/config/schema --------------------------------------------------


def test_get_config_schema_returns_json_schema(api_client):
    resp = api_client.get("/api/config/schema")
    assert resp.status_code == 200, resp.text
    schema = resp.json()
    assert "properties" in schema
    assert "config_version" in schema["properties"]
    # Pydantic emits nested model definitions under `$defs`.
    assert "$defs" in schema


# --- PUT /api/config — happy path -------------------------------------------


def test_put_config_apply_writes_yaml_and_swaps_state(api_client, tmp_path):
    config_path: Path = api_client.app.state.config_path
    cfg: Config = api_client.app.state.config
    new_body = cfg.model_dump(mode="json")
    new_body["config_version"] = 2

    resp = api_client.put("/api/config", json={"config": new_body, "dry_run": False})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["applied"] is True
    assert result["errors"] is None
    # Diff captures the changed key with before/after.
    assert "config_version" in result["diff"]["changed"]
    assert result["diff"]["changed"]["config_version"] == {"before": 1, "after": 2}

    # On-disk YAML reflects the change.
    assert config_path.exists()
    on_disk = yaml.safe_load(config_path.read_text())
    assert on_disk["config_version"] == 2

    # `app.state.config` was swapped.
    assert api_client.app.state.config.config_version == 2


# --- PUT /api/config — dry-run ----------------------------------------------


def test_put_config_dry_run_does_not_write_or_mutate(api_client):
    config_path: Path = api_client.app.state.config_path
    cfg_before: Config = api_client.app.state.config
    assert not config_path.exists()  # default `api_client` starts with no file

    new_body = cfg_before.model_dump(mode="json")
    new_body["config_version"] = 99

    resp = api_client.put("/api/config", json={"config": new_body, "dry_run": True})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["applied"] is False
    assert result["errors"] is None
    assert result["diff"]["changed"]["config_version"] == {"before": 1, "after": 99}

    # No file written.
    assert not config_path.exists()
    # `app.state.config` unchanged.
    assert api_client.app.state.config.config_version == 1
    assert api_client.app.state.config is cfg_before


# --- PUT /api/config — validation failure ------------------------------------


def test_put_config_invalid_returns_422(api_client):
    config_path: Path = api_client.app.state.config_path
    bad_body = {"config_version": "not-an-int"}

    resp = api_client.put("/api/config", json={"config": bad_body, "dry_run": False})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "errors" in detail
    assert len(detail["errors"]) > 0

    # No file written, no state mutation.
    assert not config_path.exists()
    assert api_client.app.state.config.config_version == 1


def test_put_config_invalid_dry_run_also_returns_422(api_client):
    """Validation runs in both modes — dry-run with invalid data still 422s."""
    bad_body = {"config_version": "not-an-int"}
    resp = api_client.put("/api/config", json={"config": bad_body, "dry_run": True})
    assert resp.status_code == 422, resp.text


# --- Backup rotation ---------------------------------------------------------


def test_put_config_rotates_existing_file_to_bak(api_client):
    """Pre-existing config.yaml → `.bak` after PUT, containing original content."""
    config_path: Path = api_client.app.state.config_path
    cfg: Config = api_client.app.state.config
    # Pre-create the on-disk YAML so rotation has something to copy.
    cfg.save(config_path)
    original_text = config_path.read_text()

    new_body = cfg.model_dump(mode="json")
    new_body["config_version"] = 7

    resp = api_client.put("/api/config", json={"config": new_body, "dry_run": False})
    assert resp.status_code == 200, resp.text

    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    assert backup_path.exists()
    assert backup_path.read_text() == original_text

    # New config landed in the live file.
    on_disk = yaml.safe_load(config_path.read_text())
    assert on_disk["config_version"] == 7


def test_put_config_first_write_no_backup_created(api_client):
    """No pre-existing file → no `.bak` file (nothing to back up)."""
    config_path: Path = api_client.app.state.config_path
    assert not config_path.exists()

    cfg: Config = api_client.app.state.config
    new_body = cfg.model_dump(mode="json")
    new_body["config_version"] = 3

    resp = api_client.put("/api/config", json={"config": new_body, "dry_run": False})
    assert resp.status_code == 200, resp.text

    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    assert not backup_path.exists()
    assert config_path.exists()


# --- Diff structure smoke test ----------------------------------------------


def test_put_config_diff_top_level_change(api_client):
    cfg: Config = api_client.app.state.config
    new_body = cfg.model_dump(mode="json")
    new_body["config_version"] = 2

    resp = api_client.put("/api/config", json={"config": new_body, "dry_run": True})
    assert resp.status_code == 200, resp.text
    diff = resp.json()["diff"]
    assert diff["added"] == {}
    assert diff["removed"] == {}
    assert "config_version" in diff["changed"]
    assert diff["changed"]["config_version"]["before"] == 1
    assert diff["changed"]["config_version"]["after"] == 2
