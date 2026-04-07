import json
from pathlib import Path
from dataclasses import dataclass


@dataclass
class GroupConfig:
    webhook_url: str


@dataclass
class Config:
    groups: dict[str, GroupConfig]
    bot_token: str | None
    signing_secret: str | None


def load_config(path: str | None = None) -> Config:
    if path is None:
        path = str(Path(__file__).parent / "config.json")

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. "
            "Copy config.example.json to config.json and fill in your values."
        )

    with open(config_path) as f:
        data = json.load(f)

    if "groups" not in data or not isinstance(data["groups"], dict):
        raise ValueError("config.json must contain a 'groups' object")

    if not data["groups"]:
        raise ValueError("config.json must contain at least one group")

    groups = {}
    for name, group_data in data["groups"].items():
        if "webhook_url" not in group_data:
            raise ValueError(f"Group '{name}' is missing 'webhook_url'")
        groups[name] = GroupConfig(webhook_url=group_data["webhook_url"])

    return Config(
        groups=groups,
        bot_token=data.get("bot_token"),
        signing_secret=data.get("signing_secret"),
    )
