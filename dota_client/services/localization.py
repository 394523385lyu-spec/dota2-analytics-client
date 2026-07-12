from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable

from ..paths import CACHE_DIR, resource_path
from .http_client import request_json


DOTA_HERO_LIST_ZH = "https://www.dota2.com/datafeed/herolist?language=schinese"
SOURCE_DIR = Path("/Users/lvjingru/队伍信息获取脚本合集")
CACHE_PATH = CACHE_DIR / "heroes_zh.json"
BUNDLED_CACHE_PATH = resource_path("assets", "heroes_zh.json")
CUSTOM_MAPPING_FILES = (
    SOURCE_DIR / "Dota2_英雄映射表_官方中文_定制修正版.csv",
    SOURCE_DIR / "Dota2_英雄映射表_中英对照.csv",
)

DISPLAY_LABELS = {
    "team_id": "战队 ID",
    "rating": "评分",
    "wins": "胜场",
    "losses": "负场",
    "last_match_time": "最近比赛时间",
    "delta": "评分变化",
    "match_id": "比赛 ID",
    "name": "名称",
    "tag": "简称",
    "logo_url": "队徽地址",
    "matches_requested": "请求比赛数",
    "matches_fetched": "成功抓取比赛数",
    "matches_analyzed": "实际分析比赛数",
    "win_rate": "胜率",
    "account_id": "账号 ID",
    "games_played": "比赛场次",
    "is_current": "当前队员",
    "opponent": "对手",
    "result": "结果",
    "duration_minutes": "时长（分钟）",
    "radiant_score": "天辉击杀",
    "dire_score": "夜魇击杀",
    "league_id": "联赛 ID",
    "start_time": "开始时间",
    "Match ID": "比赛 ID",
    "Account ID": "账号 ID",
    "Steam ID": "Steam ID",
    "KDA": "击杀助攻死亡比（KDA）",
    "平均GPM": "平均每分钟金钱（GPM）",
    "平均XPM": "平均每分钟经验（XPM）",
}


def _normalized(value: Any) -> str:
    text = str(value or "").replace("’", "'").replace("\u200b", "")
    text = re.sub(r"['’]", "", text)
    text = re.sub(r"[\s_\-\u00a0]+", " ", text)
    return text.strip().lower()


def _guess_columns(fieldnames: Iterable[str]) -> tuple[str | None, str | None]:
    fields = list(fieldnames)
    english = next(
        (
            field
            for field in fields
            if _normalized(field)
            in {
                "英文名",
                "英文名称",
                "english",
                "english name",
                "localized name en",
            }
        ),
        None,
    )
    chinese = next(
        (
            field
            for field in fields
            if _normalized(field)
            in {
                "中文名",
                "中文名称",
                "chinese",
                "localized name",
                "hero zh",
            }
        ),
        None,
    )
    return english, chinese


def load_custom_hero_mapping() -> Dict[str, str]:
    for path in CUSTOM_MAPPING_FILES:
        if not path.exists():
            continue
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                with path.open("r", encoding=encoding, newline="") as file:
                    reader = csv.DictReader(file)
                    english, chinese = _guess_columns(reader.fieldnames or [])
                    if not english or not chinese:
                        break
                    mapping = {
                        _normalized(row.get(english)): str(
                            row.get(chinese) or ""
                        ).strip()
                        for row in reader
                        if row.get(english) and row.get(chinese)
                    }
                    if mapping:
                        return mapping
            except UnicodeDecodeError:
                continue
    return {}


def _official_hero_rows() -> list[Dict[str, Any]]:
    payload = request_json(DOTA_HERO_LIST_ZH, timeout=35, retries=2)
    return payload.get("result", {}).get("data", {}).get("heroes", [])


def _read_cache() -> list[Dict[str, Any]]:
    for path in (CACHE_PATH, BUNDLED_CACHE_PATH):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return payload
        except (OSError, ValueError):
            continue
    return []


def get_chinese_hero_maps(
    english_by_id: Dict[int, str],
) -> tuple[Dict[int, str], Dict[str, str]]:
    cache_fresh = (
        CACHE_PATH.exists()
        and time.time() - CACHE_PATH.stat().st_mtime < 86400
    )
    rows = _read_cache() if cache_fresh else []
    if not rows:
        try:
            rows = _official_hero_rows()
            if rows:
                CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                CACHE_PATH.write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            rows = _read_cache()

    by_id: Dict[int, str] = {}
    by_english: Dict[str, str] = {}
    for row in rows:
        try:
            hero_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        chinese = str(row.get("name_loc") or "").strip()
        english = str(row.get("name_english_loc") or "").strip()
        if chinese:
            by_id[hero_id] = chinese
        if english and chinese:
            by_english[_normalized(english)] = chinese

    # 官方接口个别词条可能返回繁体或异常短名，按大陆常用官方译名修正。
    if by_id.get(137) == "獸":
        by_id[137] = "原始兽"
        by_english[_normalized("Primal Beast")] = "原始兽"

    custom = load_custom_hero_mapping()
    by_english.update(custom)
    for hero_id, english in english_by_id.items():
        custom_name = by_english.get(_normalized(english))
        if custom_name:
            by_id[hero_id] = custom_name
        elif hero_id not in by_id:
            by_id[hero_id] = english
    return by_id, by_english


def display_label(value: Any) -> str:
    return DISPLAY_LABELS.get(str(value), str(value))


def display_value(value: Any) -> Any:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return value
