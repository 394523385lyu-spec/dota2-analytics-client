from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import CONFIG_PATH, DESKTOP


@dataclass
class AppSettings:
    max_rows_per_sheet: int = 500
    output_dir: str = str(DESKTOP)


def load_settings() -> AppSettings:
    if not CONFIG_PATH.exists():
        return AppSettings()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        allowed = AppSettings.__dataclass_fields__
        return AppSettings(**{k: v for k, v in data.items() if k in allowed})
    except (OSError, ValueError, TypeError):
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
