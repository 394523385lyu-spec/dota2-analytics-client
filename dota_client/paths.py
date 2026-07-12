from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict


def resource_path(*parts: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root.joinpath(*parts)


APP_SUPPORT = (
    Path.home() / "Library" / "Application Support" / "Dota2数据分析"
)
CACHE_DIR = APP_SUPPORT / "cache"
LOG_DIR = APP_SUPPORT / "logs"
CONFIG_PATH = APP_SUPPORT / "settings.json"
DESKTOP = Path.home() / "Desktop"


def safe_name(value: Any) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "_", str(value or "目标战队")).strip()
    return text[:80] or "目标战队"


def team_output_dir(dataset: Dict[str, Any]) -> Path:
    team = dataset.get("team", {})
    name = team.get("name") or f"Team_{team.get('team_id') or '数据'}"
    target = DESKTOP / safe_name(name)
    target.mkdir(parents=True, exist_ok=True)
    return target


def global_output_dir(name: str = "Dota2版本英雄数据") -> Path:
    target = DESKTOP / safe_name(name)
    target.mkdir(parents=True, exist_ok=True)
    return target
