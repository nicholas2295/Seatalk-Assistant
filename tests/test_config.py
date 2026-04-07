import json
import pytest
from config import load_config


def _write(tmp_path, data):
    f = tmp_path / "config.json"
    f.write_text(json.dumps(data))
    return str(f)


def _valid(tmp_path, **overrides):
    data = {
        "app_id": "my-app-id",
        "app_secret": "my-app-secret",
        "signing_secret": "my-signing-secret",
        "groups": {
            "my-team": {"group_id": "grp-abc123", "name": "My Team"}
        },
    }
    data.update(overrides)
    return _write(tmp_path, data)


def test_load_config_success(tmp_path):
    config = load_config(_valid(tmp_path))

    assert config.app_id == "my-app-id"
    assert config.app_secret == "my-app-secret"
    assert config.signing_secret == "my-signing-secret"
    assert "my-team" in config.groups
    assert config.groups["my-team"].group_id == "grp-abc123"
    assert config.groups["my-team"].name == "My Team"


def test_load_config_name_optional(tmp_path):
    data = {
        "app_id": "a", "app_secret": "b",
        "groups": {"my-team": {"group_id": "grp-abc123"}},
    }
    config = load_config(_write(tmp_path, data))

    assert config.groups["my-team"].name is None


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError, match="config.json"):
        load_config("/nonexistent/config.json")


def test_load_config_missing_app_id(tmp_path):
    data = {
        "app_secret": "b",
        "groups": {"my-team": {"group_id": "grp-abc123"}},
    }
    with pytest.raises(ValueError, match="app_id"):
        load_config(_write(tmp_path, data))


def test_load_config_missing_app_secret(tmp_path):
    data = {
        "app_id": "a",
        "groups": {"my-team": {"group_id": "grp-abc123"}},
    }
    with pytest.raises(ValueError, match="app_secret"):
        load_config(_write(tmp_path, data))


def test_load_config_missing_groups_key(tmp_path):
    data = {"app_id": "a", "app_secret": "b"}
    with pytest.raises(ValueError, match="groups"):
        load_config(_write(tmp_path, data))


def test_load_config_empty_groups(tmp_path):
    data = {"app_id": "a", "app_secret": "b", "groups": {}}
    with pytest.raises(ValueError, match="at least one group"):
        load_config(_write(tmp_path, data))


def test_load_config_group_missing_group_id(tmp_path):
    data = {"app_id": "a", "app_secret": "b", "groups": {"my-team": {}}}
    with pytest.raises(ValueError, match="group_id"):
        load_config(_write(tmp_path, data))


def test_load_config_malformed_json(tmp_path):
    f = tmp_path / "config.json"
    f.write_text("{bad json")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_config(str(f))
