import json
import pytest
from pathlib import Path
from config import load_config


def test_load_config_success(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "groups": {
            "my-team": {"webhook_url": "https://openapi.seatalk.io/webhook/group/abc123"}
        },
        "bot_token": "test-token",
        "signing_secret": "test-secret"
    }))

    config = load_config(str(config_file))

    assert "my-team" in config.groups
    assert config.groups["my-team"].webhook_url == "https://openapi.seatalk.io/webhook/group/abc123"
    assert config.bot_token == "test-token"
    assert config.signing_secret == "test-secret"


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError, match="config.json"):
        load_config("/nonexistent/config.json")


def test_load_config_missing_groups_key(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"bot_token": "tok"}))

    with pytest.raises(ValueError, match="groups"):
        load_config(str(config_file))


def test_load_config_empty_groups(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"groups": {}}))

    with pytest.raises(ValueError, match="at least one group"):
        load_config(str(config_file))


def test_load_config_group_missing_webhook(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "groups": {"my-team": {}}
    }))

    with pytest.raises(ValueError, match="webhook_url"):
        load_config(str(config_file))


def test_load_config_optional_fields_absent(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "groups": {"my-team": {"webhook_url": "https://example.com/wh"}}
    }))

    config = load_config(str(config_file))

    assert config.bot_token is None
    assert config.signing_secret is None


def test_load_config_group_empty_webhook(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"groups": {"my-team": {"webhook_url": ""}}}))

    with pytest.raises(ValueError, match="webhook_url"):
        load_config(str(config_file))
