import json
from pathlib import Path
from dataclasses import dataclass


@dataclass
class GroupConfig:
    group_id: str
    name: str | None = None


@dataclass
class Config:
    app_id: str
    app_secret: str
    groups: dict[str, GroupConfig]
    signing_secret: str | None = None


def load_config(path: str | None = None) -> "Config":
    if path is None:
        path = str(Path(__file__).parent / "config.json")

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. "
            "Copy config.example.json to config.json and fill in your values."
        )

    with open(config_path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"config.json contains invalid JSON: {exc}") from exc

    if "app_id" not in data or not isinstance(data["app_id"], str) or not data["app_id"].strip():
        raise ValueError("config.json must contain a non-empty 'app_id'")

    if "app_secret" not in data or not isinstance(data["app_secret"], str) or not data["app_secret"].strip():
        raise ValueError("config.json must contain a non-empty 'app_secret'")

    if "groups" not in data or not isinstance(data["groups"], dict):
        raise ValueError("config.json must contain a 'groups' object")

    if not data["groups"]:
        raise ValueError("config.json must contain at least one group")

    groups = {}
    for key, group_data in data["groups"].items():
        if "group_id" not in group_data or not str(group_data["group_id"]).strip():
            raise ValueError(f"Group '{key}' is missing 'group_id'")
        groups[key] = GroupConfig(
            group_id=str(group_data["group_id"]),
            name=group_data.get("name"),
        )

    return Config(
        app_id=data["app_id"],
        app_secret=data["app_secret"],
        signing_secret=data.get("signing_secret"),
        groups=groups,
    )
