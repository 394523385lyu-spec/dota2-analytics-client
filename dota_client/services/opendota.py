from __future__ import annotations

from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
from threading import Lock
from time import monotonic, time
from typing import Any, Dict, List

from .http_client import request_json, with_query
from .localization import get_chinese_hero_maps
from ..paths import CACHE_DIR


BASE_URL = "https://api.opendota.com/api"
MATCH_CACHE_DIR = CACHE_DIR / "matches"
API_CACHE_DIR = CACHE_DIR / "api"


class OpenDotaService:
    def __init__(self) -> None:
        self._cache: Dict[str, tuple[float, Any]] = {}
        self._cache_lock = Lock()

    def _cached(self, key: str, ttl: int, loader: Any) -> Any:
        now = monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < ttl:
                return cached[1]
        cache_name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json.gz"
        cache_path = API_CACHE_DIR / cache_name
        disk_value = None
        if cache_path.exists():
            try:
                with gzip.open(cache_path, "rt", encoding="utf-8") as file:
                    disk_value = json.load(file)
                if time() - cache_path.stat().st_mtime < ttl:
                    with self._cache_lock:
                        self._cache[key] = (now, disk_value)
                    return disk_value
            except (OSError, ValueError):
                disk_value = None
        try:
            value = loader()
        except Exception:
            # OpenDota 偶发 521/超时：已有成功缓存时继续使用旧数据。
            if disk_value is not None:
                with self._cache_lock:
                    self._cache[key] = (now, disk_value)
                return disk_value
            raise
        try:
            API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            temp_path = cache_path.with_suffix(".tmp")
            with gzip.open(temp_path, "wt", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False)
            temp_path.replace(cache_path)
        except OSError:
            pass
        with self._cache_lock:
            if len(self._cache) > 300:
                oldest = sorted(
                    self._cache.items(), key=lambda item: item[1][0]
                )[:80]
                for old_key, _ in oldest:
                    self._cache.pop(old_key, None)
            self._cache[key] = (now, value)
        return value

    def search_teams(self, name: str) -> List[Dict[str, Any]]:
        teams = self._cached(
            "teams",
            1800,
            lambda: request_json(f"{BASE_URL}/teams"),
        )
        needle = name.strip().lower()
        matches = [
            team
            for team in teams
            if needle in str(team.get("name", "")).lower()
            or needle in str(team.get("tag", "")).lower()
        ]
        return sorted(
            matches,
            key=lambda item: int(item.get("rating") or 0),
            reverse=True,
        )[:30]

    def get_team(self, team_id: int) -> Dict[str, Any]:
        return self._cached(
            f"team:{team_id}",
            900,
            lambda: request_json(f"{BASE_URL}/teams/{team_id}"),
        )

    def get_players(self, team_id: int) -> List[Dict[str, Any]]:
        return self._cached(
            f"team_players:{team_id}",
            900,
            lambda: request_json(f"{BASE_URL}/teams/{team_id}/players"),
        )

    def get_matches(self, team_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        matches = self._cached(
            f"team_matches:{team_id}",
            300,
            lambda: request_json(f"{BASE_URL}/teams/{team_id}/matches"),
        )
        return matches[: max(1, min(limit, 500))]

    def get_match(self, match_id: int) -> Dict[str, Any]:
        def load_match() -> Dict[str, Any]:
            cache_path = MATCH_CACHE_DIR / f"{match_id}.json.gz"
            if (
                cache_path.exists()
                and time() - cache_path.stat().st_mtime < 86400
            ):
                try:
                    with gzip.open(cache_path, "rt", encoding="utf-8") as file:
                        return json.load(file)
                except (OSError, ValueError):
                    pass
            value = request_json(f"{BASE_URL}/matches/{match_id}")
            try:
                MATCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                temp_path = cache_path.with_suffix(".tmp")
                with gzip.open(temp_path, "wt", encoding="utf-8") as file:
                    json.dump(value, file, ensure_ascii=False)
                temp_path.replace(cache_path)
            except OSError:
                pass
            return value

        return self._cached(f"match:{match_id}", 1800, load_match)

    def get_heroes(self) -> Dict[int, str]:
        heroes = self._cached(
            "heroes_en",
            86400,
            lambda: request_json(f"{BASE_URL}/heroes"),
        )
        english = {int(hero["id"]): hero["localized_name"] for hero in heroes}
        chinese, _ = get_chinese_hero_maps(english)
        return chinese

    def get_patches(self) -> List[Dict[str, Any]]:
        payload = self._cached(
            "constants_patch",
            86400,
            lambda: request_json(f"{BASE_URL}/constants/patch"),
        )
        rows: List[Dict[str, Any]] = []
        if isinstance(payload, dict):
            iterable = payload.items()
        else:
            iterable = enumerate(payload if isinstance(payload, list) else [])
        for key, value in iterable:
            if not isinstance(value, dict):
                continue
            try:
                patch_id = int(value.get("id", key))
            except (TypeError, ValueError):
                continue
            name = str(
                value.get("name")
                or value.get("patch")
                or value.get("version")
                or patch_id
            )
            rows.append(
                {
                    "id": patch_id,
                    "name": name,
                    "date": value.get("date") or value.get("timestamp") or "",
                }
            )
        rows.sort(key=lambda row: row["id"], reverse=True)
        return rows

    def get_hero_stats(self) -> List[Dict[str, Any]]:
        heroes = self._cached(
            "hero_stats",
            21600,
            lambda: request_json(f"{BASE_URL}/heroStats"),
        )
        english = {
            int(hero["id"]): hero.get("localized_name") or str(hero.get("name") or "")
            for hero in heroes
            if hero.get("id") is not None
        }
        chinese, _ = get_chinese_hero_maps(english)
        rows: List[Dict[str, Any]] = []
        for hero in heroes:
            try:
                hero_id = int(hero.get("id"))
            except (TypeError, ValueError):
                continue
            pro_pick = int(hero.get("pro_pick") or 0)
            pro_ban = int(hero.get("pro_ban") or 0)
            pro_win = int(hero.get("pro_win") or 0)
            pro_bp = pro_pick + pro_ban
            rows.append(
                {
                    "英雄ID": hero_id,
                    "英雄名称": chinese.get(hero_id)
                    or hero.get("localized_name")
                    or f"Hero {hero_id}",
                    "选取次数": pro_pick,
                    "禁用次数": pro_ban,
                    "BP总次数": pro_bp,
                    "胜场数": pro_win,
                    "选取率(%)": round(pro_pick / pro_bp * 100, 2)
                    if pro_bp
                    else 0,
                    "禁用率(%)": round(pro_ban / pro_bp * 100, 2)
                    if pro_bp
                    else 0,
                    "胜率(%)": round(pro_win / pro_pick * 100, 2)
                    if pro_pick
                    else 0,
                    "统计范围": "OpenDota 职业公开 Meta 汇总",
                }
            )
        rows.sort(key=lambda row: (row["BP总次数"], row["选取次数"]), reverse=True)
        return rows

    def _patch_for_filter(self, patch_filter: Any) -> Dict[str, Any]:
        patches = self.get_patches()
        if not patches:
            raise RuntimeError("无法获取 OpenDota 版本列表。")
        selected: Dict[str, Any] | None = None
        if patch_filter in (None, "", "latest"):
            selected = patches[0]
        else:
            text = str(patch_filter).strip()
            if text.isdigit():
                selected = next(
                    (patch for patch in patches if int(patch["id"]) == int(text)),
                    None,
                )
            if selected is None:
                lowered = text.lower()
                selected = next(
                    (
                        patch
                        for patch in patches
                        if lowered in str(patch.get("name") or "").lower()
                    ),
                    None,
                )
        if selected is None:
            raise RuntimeError(f"没有找到版本：{patch_filter}")
        return selected

    def get_patch_hero_win_rates(
        self, patch_filter: Any = "latest", scope: str = "pro"
    ) -> List[Dict[str, Any]]:
        patch = self._patch_for_filter(patch_filter)
        patch_id = int(patch["id"])
        patch_name = str(patch.get("name") or patch_id)
        normalized_scope = "public" if scope == "public" else "pro"
        cache_key = f"patch_hero_win_rates:{normalized_scope}:{patch_name}"

        def load() -> Any:
            if normalized_scope == "public":
                sql = f"""
WITH picks AS (
  SELECT unnest(pm.radiant_team) AS hero_id, pm.radiant_win AS won
  FROM public_matches pm
  JOIN match_patch mp ON pm.match_id = mp.match_id
  WHERE mp.patch = '{patch_name}'
  UNION ALL
  SELECT unnest(pm.dire_team) AS hero_id, NOT pm.radiant_win AS won
  FROM public_matches pm
  JOIN match_patch mp ON pm.match_id = mp.match_id
  WHERE mp.patch = '{patch_name}'
)
SELECT hero_id, COUNT(*) AS games, SUM(CASE WHEN won THEN 1 ELSE 0 END) AS wins
FROM picks
WHERE hero_id IS NOT NULL
GROUP BY hero_id
ORDER BY games DESC
LIMIT 200
"""
            else:
                sql = f"""
WITH picks AS (
  SELECT (pb->>'hero_id')::int AS hero_id,
         CASE WHEN (pb->>'team')::int = 0 THEN m.radiant_win ELSE NOT m.radiant_win END AS won
  FROM matches m
  JOIN match_patch mp ON m.match_id = mp.match_id
  JOIN leagues l ON m.leagueid = l.leagueid
  CROSS JOIN LATERAL unnest(m.picks_bans) pb
  WHERE mp.patch = '{patch_name}'
    AND l.tier = 'professional'
    AND (pb->>'is_pick')::boolean = true
)
SELECT hero_id, COUNT(*) AS games, SUM(CASE WHEN won THEN 1 ELSE 0 END) AS wins
FROM picks
WHERE hero_id IS NOT NULL
GROUP BY hero_id
ORDER BY games DESC
LIMIT 200
"""
            return request_json(
                with_query(f"{BASE_URL}/explorer", {"sql": sql}),
                timeout=90,
                retries=2,
            )

        payload = self._cached(cache_key, 86400, load)
        raw_rows = payload.get("rows", []) if isinstance(payload, dict) else []
        hero_map = self.get_heroes()
        rows: List[Dict[str, Any]] = []
        for row in raw_rows:
            try:
                hero_id = int(row.get("hero_id"))
                games = int(row.get("games") or 0)
                wins = int(row.get("wins") or 0)
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "版本": patch_name,
                    "版本ID": patch_id,
                    "英雄ID": hero_id,
                    "英雄名称": hero_map.get(hero_id, f"Hero {hero_id}"),
                    "场次": games,
                    "胜场": wins,
                    "负场": max(0, games - wins),
                    "胜率(%)": round(wins / games * 100, 2) if games else 0,
                    "数据口径": "玩家公开对局"
                    if normalized_scope == "public"
                    else "职业比赛",
                    "统计范围": "OpenDota Explorer · match_patch 小版本聚合",
                }
            )
        rows.sort(key=lambda row: (row["场次"], row["胜率(%)"]), reverse=True)
        return rows

    def get_player_profile(self, account_id: int) -> Dict[str, Any]:
        return self._cached(
            f"player:{account_id}",
            1800,
            lambda: request_json(f"{BASE_URL}/players/{account_id}"),
        )

    def get_player_wl(self, account_id: int) -> Dict[str, Any]:
        return self._cached(
            f"player_wl:{account_id}",
            1800,
            lambda: request_json(f"{BASE_URL}/players/{account_id}/wl"),
        )

    def get_player_heroes(self, account_id: int) -> List[Dict[str, Any]]:
        return self._cached(
            f"player_heroes:{account_id}",
            1800,
            lambda: request_json(f"{BASE_URL}/players/{account_id}/heroes"),
        )

    def summarize_team(self, team_id: int, limit: int = 50) -> Dict[str, Any]:
        team = self.get_team(team_id)
        players = self.get_players(team_id)
        matches = self.get_matches(team_id, limit)
        wins = 0
        opponents: Counter[str] = Counter()
        leagues: Counter[str] = Counter()
        durations: List[int] = []
        rows = []
        for match in matches:
            radiant = int(match.get("radiant_team_id") or 0) == team_id
            won = bool(match.get("radiant_win")) == radiant
            wins += int(won)
            opponent = (
                match.get("dire_name") if radiant else match.get("radiant_name")
            ) or "未知对手"
            opponents[opponent] += 1
            if match.get("league_name"):
                leagues[str(match["league_name"])] += 1
            duration = int(match.get("duration") or 0)
            if duration:
                durations.append(duration)
            rows.append(
                {
                    "match_id": match.get("match_id"),
                    "opponent": opponent,
                    "result": "胜" if won else "负",
                    "duration_minutes": round(duration / 60, 1) if duration else None,
                    "league": match.get("league_name") or "",
                    "start_time": match.get("start_time"),
                }
            )
        total = len(matches)
        return {
            "team": team,
            "active_players": [
                {
                    "account_id": player.get("account_id"),
                    "name": player.get("name") or "未知选手",
                    "games_played": player.get("games_played"),
                    "wins": player.get("wins"),
                    "is_current": player.get("is_current_team_member"),
                }
                for player in players
                if player.get("is_current_team_member")
            ],
            "statistics": {
                "matches": total,
                "wins": wins,
                "losses": total - wins,
                "win_rate": round(wins / total * 100, 2) if total else 0,
                "average_duration_minutes": round(
                    sum(durations) / len(durations) / 60, 1
                )
                if durations
                else 0,
                "frequent_opponents": opponents.most_common(8),
                "leagues": leagues.most_common(8),
            },
            "matches": rows,
        }
