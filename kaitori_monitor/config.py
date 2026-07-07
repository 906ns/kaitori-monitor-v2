"""環境変数 (.env) と targets.yaml の読み込み。"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

DEFAULT_NOTIFY_TIME = "09:00"


@dataclass
class Config:
    token: str
    channel_id: int
    db_path: Path
    yaml_notify_time: str = DEFAULT_NOTIFY_TIME
    yaml_targets: list[dict] = field(default_factory=list)


def load_config() -> Config:
    load_dotenv()

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("DISCORD_BOT_TOKEN が設定されていません (.env.example を参照)")

    channel_raw = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not channel_raw.isdigit():
        sys.exit("DISCORD_CHANNEL_ID が設定されていません (.env.example を参照)")

    db_path = Path(os.environ.get("DB_PATH", "data/monitor.sqlite3"))

    yaml_path = Path(os.environ.get("TARGETS_YAML", "config/targets.yaml"))
    notify_time = DEFAULT_NOTIFY_TIME
    targets: list[dict] = []
    if yaml_path.exists():
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        notify_time = str(data.get("notify_time", DEFAULT_NOTIFY_TIME))
        raw_targets = data.get("targets") or []
        if not isinstance(raw_targets, list):
            sys.exit(f"{yaml_path}: targets はリストで指定してください")
        for entry in raw_targets:
            if isinstance(entry, dict) and ("jan" in entry or "name" in entry):
                targets.append({k: str(v) for k, v in entry.items()})
            else:
                sys.exit(f"{yaml_path}: 不正な監視対象の指定です: {entry!r}")

    return Config(
        token=token,
        channel_id=int(channel_raw),
        db_path=db_path,
        yaml_notify_time=notify_time,
        yaml_targets=targets,
    )
