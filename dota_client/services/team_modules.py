from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Sequence

from .opendota import OpenDotaService


def _patch_id(detail: Dict[str, Any]) -> int | None:
    try:
        value = int(detail.get("patch") or 0)
    except (TypeError, ValueError):
        return None
    return value or None


def _patch_label(patch_id: int | None, patch_lookup: Dict[int, str]) -> str:
    if not patch_id:
        return "未知版本"
    return patch_lookup.get(patch_id) or f"Patch {patch_id}"


def _target_is_radiant(detail: Dict[str, Any], team_id: int) -> bool | None:
    if int(detail.get("radiant_team_id") or 0) == team_id:
        return True
    if int(detail.get("dire_team_id") or 0) == team_id:
        return False
    return None


def _won(detail: Dict[str, Any], team_is_radiant: bool) -> bool:
    return bool(detail.get("radiant_win")) == team_is_radiant


def _opponent_name(detail: Dict[str, Any], team_is_radiant: bool) -> str:
    team = detail.get("dire_team") if team_is_radiant else detail.get("radiant_team")
    fallback = detail.get("dire_name") if team_is_radiant else detail.get("radiant_name")
    return (team or {}).get("name") or fallback or "未知对手"


def _hero_name(hero_map: Dict[int, str], hero_id: Any) -> str:
    try:
        return hero_map.get(int(hero_id), f"Hero {hero_id}")
    except (TypeError, ValueError):
        return "未知英雄"


def build_bp_analysis(
    details: Sequence[Dict[str, Any]],
    team_id: int,
    hero_map: Dict[int, str],
    patch_lookup: Dict[int, str] | None = None,
) -> Dict[str, Any]:
    patch_lookup = patch_lookup or {}
    stats: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {
            "我方选取": 0,
            "选取胜场": 0,
            "我方禁用": 0,
            "对手禁用": 0,
            "首轮选取": 0,
            "首轮禁用": 0,
            "末轮选取": 0,
            "末轮禁用": 0,
        }
    )
    actions: List[Dict[str, Any]] = []
    lost_timeline: List[Dict[str, Any]] = []
    processed = 0
    missing = 0
    for detail in details:
        team_is_radiant = _target_is_radiant(detail, team_id)
        draft = detail.get("picks_bans") or []
        if team_is_radiant is None or not draft:
            missing += 1
            continue
        processed += 1
        won = _won(detail, team_is_radiant)
        target_team_index = 0 if team_is_radiant else 1
        match_id = detail.get("match_id")
        for index, action in enumerate(draft):
            hero = _hero_name(hero_map, action.get("hero_id"))
            is_pick = bool(action.get("is_pick"))
            is_target = action.get("team") == target_team_index
            action_type = "选择" if is_pick else "禁用"
            actor = "我方" if is_target else "对手"
            row = {
                "Match ID": match_id,
                "版本": _patch_label(_patch_id(detail), patch_lookup),
                "顺序": index + 1,
                "时间(秒)": action.get("time"),
                "执行方": actor,
                "类型": action_type,
                "英雄": hero,
                "比赛结果": "胜" if won else "负",
                "对手": _opponent_name(detail, team_is_radiant),
            }
            actions.append(row)
            if not won:
                lost_timeline.append(row)
            if is_target and is_pick:
                stats[hero]["我方选取"] += 1
                stats[hero]["选取胜场"] += int(won)
            elif is_target:
                stats[hero]["我方禁用"] += 1
            elif not is_pick:
                stats[hero]["对手禁用"] += 1
            if is_target and index <= 1:
                stats[hero]["首轮选取" if is_pick else "首轮禁用"] += 1
            if is_target and index == len(draft) - 1:
                stats[hero]["末轮选取" if is_pick else "末轮禁用"] += 1

    summary = []
    for hero, values in stats.items():
        picks = values["我方选取"]
        summary.append(
            {
                "英雄": hero,
                **values,
                "选取胜率(%)": round(values["选取胜场"] / picks * 100, 1)
                if picks
                else None,
                "总关注次数": values["我方选取"]
                + values["我方禁用"]
                + values["对手禁用"],
            }
        )
    summary.sort(key=lambda row: (row["总关注次数"], row["我方选取"]), reverse=True)
    return {
        "summary": summary,
        "actions": actions,
        "lost_timeline": lost_timeline,
        "quality": {
            "有效BP比赛": processed,
            "缺少BP数据比赛": missing,
        },
    }


def build_recent_player_analysis(
    details: Sequence[Dict[str, Any]], team_id: int, hero_map: Dict[int, str]
) -> Dict[str, Any]:
    aggregates: Dict[int, Dict[str, Any]] = {}
    hero_stats: Dict[tuple[int, int], Dict[str, int]] = defaultdict(
        lambda: {"场次": 0, "胜场": 0}
    )
    for detail in details:
        team_is_radiant = _target_is_radiant(detail, team_id)
        if team_is_radiant is None:
            continue
        won = _won(detail, team_is_radiant)
        for player in detail.get("players") or []:
            if player.get("isRadiant") != team_is_radiant:
                continue
            account_id = int(player.get("account_id") or 0)
            if not account_id:
                continue
            row = aggregates.setdefault(
                account_id,
                {
                    "Account ID": account_id,
                    "选手": player.get("personaname") or f"ID_{account_id}",
                    "场次": 0,
                    "胜场": 0,
                    "击杀": 0,
                    "死亡": 0,
                    "助攻": 0,
                    "GPM": 0,
                    "XPM": 0,
                    "英雄伤害": 0,
                    "推塔伤害": 0,
                    "正补": 0,
                },
            )
            row["场次"] += 1
            row["胜场"] += int(won)
            for source, target in (
                ("kills", "击杀"),
                ("deaths", "死亡"),
                ("assists", "助攻"),
                ("gold_per_min", "GPM"),
                ("xp_per_min", "XPM"),
                ("hero_damage", "英雄伤害"),
                ("tower_damage", "推塔伤害"),
                ("last_hits", "正补"),
            ):
                row[target] += int(player.get(source) or 0)
            hero_id = int(player.get("hero_id") or 0)
            if hero_id:
                hero_stats[(account_id, hero_id)]["场次"] += 1
                hero_stats[(account_id, hero_id)]["胜场"] += int(won)

    overview = []
    for row in aggregates.values():
        games = row["场次"]
        deaths = row["死亡"]
        overview.append(
            {
                "Account ID": row["Account ID"],
                "选手": row["选手"],
                "场次": games,
                "胜场": row["胜场"],
                "胜率(%)": round(row["胜场"] / games * 100, 1) if games else 0,
                "平均击杀": round(row["击杀"] / games, 2),
                "平均死亡": round(row["死亡"] / games, 2),
                "平均助攻": round(row["助攻"] / games, 2),
                "KDA": round((row["击杀"] + row["助攻"]) / max(1, deaths), 2),
                "平均GPM": round(row["GPM"] / games, 1),
                "平均XPM": round(row["XPM"] / games, 1),
                "平均英雄伤害": round(row["英雄伤害"] / games, 1),
                "平均推塔伤害": round(row["推塔伤害"] / games, 1),
                "平均正补": round(row["正补"] / games, 1),
            }
        )
    overview.sort(key=lambda row: (row["场次"], row["胜率(%)"]), reverse=True)

    hero_rows = []
    names = {row["Account ID"]: row["选手"] for row in overview}
    for (account_id, hero_id), values in hero_stats.items():
        hero_rows.append(
            {
                "Account ID": account_id,
                "选手": names.get(account_id, f"ID_{account_id}"),
                "英雄": _hero_name(hero_map, hero_id),
                "场次": values["场次"],
                "胜场": values["胜场"],
                "胜率(%)": round(values["胜场"] / values["场次"] * 100, 1),
            }
        )
    hero_rows.sort(key=lambda row: (row["选手"], -row["场次"], -row["胜率(%)"]))
    return {"overview": overview, "recent_heroes": hero_rows}


def fetch_player_archives(
    service: OpenDotaService,
    player_overview: Sequence[Dict[str, Any]],
    hero_map: Dict[int, str],
    max_players: int = 10,
) -> Dict[str, Any]:
    account_ids = [int(row["Account ID"]) for row in player_overview[:max_players]]
    profiles: List[Dict[str, Any]] = []
    career_heroes: List[Dict[str, Any]] = []

    def fetch_one(account_id: int) -> tuple[int, Any, Any, Any]:
        profile = wl = heroes = None
        try:
            profile = service.get_player_profile(account_id)
        except Exception:
            pass
        try:
            wl = service.get_player_wl(account_id)
        except Exception:
            pass
        try:
            heroes = service.get_player_heroes(account_id)
        except Exception:
            pass
        return account_id, profile, wl, heroes

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fetch_one, account_id) for account_id in account_ids]
        for future in as_completed(futures):
            account_id, data, wl, heroes = future.result()
            profile = (data or {}).get("profile") or {}
            wins = int((wl or {}).get("win") or 0)
            losses = int((wl or {}).get("lose") or 0)
            total = wins + losses
            profiles.append(
                {
                    "Account ID": account_id,
                    "职业名称": profile.get("name") or "",
                    "昵称": profile.get("personaname") or "",
                    "Steam ID": profile.get("steamid") or "",
                    "段位代码": (data or {}).get("rank_tier"),
                    "MMR": (data or {}).get("solo_competitive_rank"),
                    "生涯胜场": wins,
                    "生涯负场": losses,
                    "生涯胜率(%)": round(wins / total * 100, 2) if total else None,
                    "上次登录": profile.get("last_login") or "",
                }
            )
            for item in sorted(
                heroes or [], key=lambda value: int(value.get("games") or 0), reverse=True
            )[:20]:
                games = int(item.get("games") or 0)
                wins_on_hero = int(item.get("win") or 0)
                career_heroes.append(
                    {
                        "Account ID": account_id,
                        "英雄": _hero_name(hero_map, item.get("hero_id")),
                        "使用场次": games,
                        "胜场": wins_on_hero,
                        "胜率(%)": round(wins_on_hero / games * 100, 1)
                        if games
                        else 0,
                    }
                )
    profiles.sort(key=lambda row: account_ids.index(row["Account ID"]))
    return {"profiles": profiles, "career_heroes": career_heroes}


def build_cooccurrence_analysis(
    details: Sequence[Dict[str, Any]], team_id: int, hero_map: Dict[int, str]
) -> Dict[str, Any]:
    records = []
    for detail in details:
        team_is_radiant = _target_is_radiant(detail, team_id)
        if team_is_radiant is None:
            continue
        players = detail.get("players") or []
        mine = [
            _hero_name(hero_map, player.get("hero_id"))
            for player in players
            if player.get("isRadiant") == team_is_radiant
        ]
        opponent = [
            _hero_name(hero_map, player.get("hero_id"))
            for player in players
            if player.get("isRadiant") != team_is_radiant
        ]
        if len(mine) != 5 or len(opponent) != 5:
            continue
        records.append(
            {
                "Match ID": detail.get("match_id"),
                "我方阵容": sorted(mine),
                "对手阵容": sorted(opponent),
                "对手队伍": _opponent_name(detail, team_is_radiant),
                "结果": "胜" if _won(detail, team_is_radiant) else "负",
            }
        )

    combo_stats: Dict[tuple[str, ...], Dict[str, int]] = defaultdict(
        lambda: {"场次": 0, "胜场": 0}
    )
    hero_heat: Counter[str] = Counter()
    weighted_degree: Counter[str] = Counter()
    for record in records:
        lineup = record["我方阵容"]
        hero_heat.update(lineup)
        for a, b in itertools.combinations(lineup, 2):
            weighted_degree[a] += 1
            weighted_degree[b] += 1
        for size in range(2, 6):
            for combo in itertools.combinations(lineup, size):
                combo_stats[combo]["场次"] += 1
                combo_stats[combo]["胜场"] += int(record["结果"] == "胜")

    combinations = []
    for combo, values in combo_stats.items():
        combinations.append(
            {
                "共现规模": len(combo),
                "组合": ", ".join(combo),
                "场次": values["场次"],
                "胜场": values["胜场"],
                "胜率(%)": round(values["胜场"] / values["场次"] * 100, 1),
            }
        )
    combinations.sort(
        key=lambda row: (row["场次"], row["共现规模"], row["胜率(%)"]),
        reverse=True,
    )
    total_picks = sum(hero_heat.values())
    heat_rows = [
        {
            "英雄": hero,
            "出现次数": count,
            "上场占比(%)": round(count / total_picks * 100, 1)
            if total_picks
            else 0,
            "共现加权度": weighted_degree[hero],
        }
        for hero, count in hero_heat.most_common()
    ]
    lineup_rows = [
        {
            **record,
            "我方阵容": ", ".join(record["我方阵容"]),
            "对手阵容": ", ".join(record["对手阵容"]),
        }
        for record in records
    ]
    return {
        "match_lineups": lineup_rows,
        "combinations": combinations,
        "hero_heat": heat_rows,
    }


def _phase(seconds: int) -> str:
    minutes = max(0, seconds // 60)
    if minutes <= 12:
        return "早期"
    if minutes <= 30:
        return "中期"
    return "后期"


def _index_left_events(player: Dict[str, Any], key: str) -> tuple[Dict[Any, Any], Dict[Any, Any]]:
    by_handle: Dict[Any, Any] = {}
    by_coord: Dict[tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    for event in player.get(key) or []:
        if event.get("ehandle") is not None:
            by_handle[event["ehandle"]] = event
        if event.get("x") is not None and event.get("y") is not None:
            by_coord[(int(event["x"]), int(event["y"]))].append(event)
    for events in by_coord.values():
        events.sort(key=lambda event: int(event.get("time") or 0))
    return by_handle, by_coord


def _match_left(
    start: Dict[str, Any],
    by_handle: Dict[Any, Any],
    by_coord: Dict[Any, List[Dict[str, Any]]],
) -> Dict[str, Any] | None:
    handle = start.get("ehandle")
    if handle is not None and handle in by_handle:
        return by_handle[handle]
    if start.get("x") is None or start.get("y") is None:
        return None
    start_time = int(start.get("time") or 0)
    candidates = by_coord.get((int(start["x"]), int(start["y"])), [])
    return next(
        (event for event in candidates if int(event.get("time") or 0) >= start_time),
        None,
    )


def _ward_end(ward_type: str, start_time: int, left: Dict[str, Any] | None) -> tuple[Any, Any, str]:
    if left:
        end_time = int(left.get("time") or start_time)
        duration = max(0, end_time - start_time)
        threshold = 355 if ward_type == "假眼" else 415
        return end_time, duration, "自然消失" if duration >= threshold else "被反掉"
    if ward_type == "假眼":
        return start_time + 360, 360, "自然消失"
    return None, None, "存活结束/未知"


def _collect_wards(
    detail: Dict[str, Any],
    team_is_radiant: bool,
    mine: bool,
) -> List[Dict[str, Any]]:
    rows = []
    for player in detail.get("players") or []:
        if (player.get("isRadiant") == team_is_radiant) != mine:
            continue
        indexes = {
            "假眼": _index_left_events(player, "obs_left_log"),
            "真眼": _index_left_events(player, "sen_left_log"),
        }
        for log_key, ward_type in (("obs_log", "假眼"), ("sen_log", "真眼")):
            by_handle, by_coord = indexes[ward_type]
            for event in player.get(log_key) or []:
                start_time = int(event.get("time") or 0)
                left = _match_left(event, by_handle, by_coord)
                end_time, duration, status = _ward_end(ward_type, start_time, left)
                rows.append(
                    {
                        "Match ID": detail.get("match_id"),
                        "我方阵营": "天辉" if team_is_radiant else "夜魇",
                        "对手队伍": _opponent_name(detail, team_is_radiant),
                        "结果": "胜" if _won(detail, team_is_radiant) else "负",
                        "时间(秒)": start_time,
                        "阶段": _phase(start_time),
                        "类型": ward_type,
                        "玩家": player.get("personaname")
                        or f"ID_{player.get('account_id') or ''}",
                        "x": event.get("x"),
                        "y": event.get("y"),
                        "消失时间(秒)": end_time,
                        "持续时间(秒)": duration,
                        "消失类型": status,
                    }
                )
    return rows


def _ward_quality(
    detail: Dict[str, Any],
    team_is_radiant: bool,
    mine_rows: Sequence[Dict[str, Any]],
    opponent_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    minutes = max(1 / 60, int(detail.get("duration") or 0) / 60)

    def phase_values(rows: Sequence[Dict[str, Any]], phase: str) -> tuple[Any, Any]:
        observer = [
            row for row in rows if row["阶段"] == phase and row["类型"] == "假眼"
        ]
        if not observer:
            return None, None
        dewarded = sum(row["消失类型"] == "被反掉" for row in observer)
        durations = [
            row["持续时间(秒)"]
            for row in observer
            if isinstance(row["持续时间(秒)"], (int, float))
        ]
        return (
            round(dewarded / len(observer) * 100, 1),
            round(sum(durations) / len(durations), 1) if durations else None,
        )

    row: Dict[str, Any] = {
        "Match ID": detail.get("match_id"),
        "我方阵营": "天辉" if team_is_radiant else "夜魇",
        "时长(分)": round(minutes, 1),
        "我方每分钟插眼": round(len(mine_rows) / minutes, 3),
        "我方每分钟拆眼(近似)": round(
            sum(item["消失类型"] == "被反掉" for item in opponent_rows) / minutes,
            3,
        ),
        "对手每分钟插眼": round(len(opponent_rows) / minutes, 3),
        "对手每分钟拆眼(近似)": round(
            sum(item["消失类型"] == "被反掉" for item in mine_rows) / minutes,
            3,
        ),
    }
    for prefix, rows in (("我方", mine_rows), ("对手", opponent_rows)):
        for phase in ("早期", "中期", "后期"):
            rate, average = phase_values(rows, phase)
            row[f"{prefix}{phase}假眼被反率(%)"] = rate
            row[f"{prefix}{phase}假眼平均存活(秒)"] = average
    return row


def build_ward_analysis(
    details: Sequence[Dict[str, Any]], team_id: int, grid: int = 64
) -> Dict[str, Any]:
    overview: List[Dict[str, Any]] = []
    mine_all: List[Dict[str, Any]] = []
    opponent_all: List[Dict[str, Any]] = []
    heat: List[Dict[str, Any]] = []
    quality: List[Dict[str, Any]] = []
    missing_logs = 0

    def grid_value(value: Any) -> int:
        try:
            number = min(127.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            number = 0
        return int(number / 127 * (grid - 1) + 0.5)

    for detail in details:
        team_is_radiant = _target_is_radiant(detail, team_id)
        if team_is_radiant is None:
            continue
        players = detail.get("players") or []
        if not any(
            any(key in player for key in ("obs_log", "sen_log")) for player in players
        ):
            missing_logs += 1
        mine_rows = _collect_wards(detail, team_is_radiant, True)
        opponent_rows = _collect_wards(detail, team_is_radiant, False)
        mine_all.extend(mine_rows)
        opponent_all.extend(opponent_rows)
        duration = int(detail.get("duration") or 0)
        overview.append(
            {
                "Match ID": detail.get("match_id"),
                "我方阵营": "天辉" if team_is_radiant else "夜魇",
                "对手队伍": _opponent_name(detail, team_is_radiant),
                "结果": "胜" if _won(detail, team_is_radiant) else "负",
                "时长(分)": round(duration / 60, 1),
                "我方假眼": sum(row["类型"] == "假眼" for row in mine_rows),
                "我方真眼": sum(row["类型"] == "真眼" for row in mine_rows),
                "对手假眼": sum(row["类型"] == "假眼" for row in opponent_rows),
                "对手真眼": sum(row["类型"] == "真眼" for row in opponent_rows),
            }
        )
        quality.append(
            _ward_quality(detail, team_is_radiant, mine_rows, opponent_rows)
        )
        cells: Dict[tuple[int, int], Counter[str]] = defaultdict(Counter)
        for side, rows in (("我方", mine_rows), ("对手", opponent_rows)):
            for row in rows:
                cells[(grid_value(row["x"]), grid_value(row["y"]))][
                    f"{side}{row['类型']}"
                ] += 1
        for (gx, gy), values in cells.items():
            heat.append(
                {
                    "Match ID": detail.get("match_id"),
                    "gx": gx,
                    "gy": gy,
                    "我方假眼": values["我方假眼"],
                    "我方真眼": values["我方真眼"],
                    "对手假眼": values["对手假眼"],
                    "对手真眼": values["对手真眼"],
                }
            )
    return {
        "overview": overview,
        "mine_details": mine_all,
        "opponent_details": opponent_all,
        "heat_grid": heat,
        "quality": quality,
        "data_quality": {"缺少眼位日志比赛": missing_logs},
    }


def _fetch_match_details(
    service: OpenDotaService, matches: Iterable[Dict[str, Any]]
) -> tuple[List[Dict[str, Any]], List[str]]:
    details: List[Dict[str, Any]] = []
    errors: List[str] = []
    match_ids = [int(match["match_id"]) for match in matches if match.get("match_id")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(service.get_match, match_id): match_id for match_id in match_ids}
        for future in as_completed(futures):
            match_id = futures[future]
            try:
                details.append(future.result())
            except Exception as exc:
                errors.append(f"{match_id}: {exc}")
    order = {match_id: index for index, match_id in enumerate(match_ids)}
    details.sort(key=lambda item: order.get(int(item.get("match_id") or 0), 10**9))
    return details, errors


def analyze_team_modules(
    service: OpenDotaService,
    team_id: int,
    limit: int,
    selected_modules: Sequence[str],
    patch_filter: Any = None,
) -> Dict[str, Any]:
    team = service.get_team(team_id)
    roster = service.get_players(team_id)
    matches = service.get_matches(team_id, limit)
    hero_map = service.get_heroes()
    details, errors = _fetch_match_details(service, matches)
    try:
        patches = service.get_patches()
    except Exception:
        patches = []
    patch_lookup = {int(row["id"]): str(row["name"]) for row in patches}
    available_patch_ids = sorted(
        {patch_id for detail in details if (patch_id := _patch_id(detail))},
        reverse=True,
    )
    requested_patch = patch_filter
    selected_patch_id: int | None = None
    if patch_filter == "latest":
        selected_patch_id = available_patch_ids[0] if available_patch_ids else None
    elif patch_filter not in (None, "", "all"):
        text = str(patch_filter).strip()
        if text.isdigit():
            selected_patch_id = int(text)
        else:
            lowered = text.lower()
            for patch in patches:
                if lowered in str(patch.get("name") or "").lower():
                    selected_patch_id = int(patch["id"])
                    break
    unfiltered_detail_count = len(details)
    if selected_patch_id is not None:
        details = [
            detail
            for detail in details
            if _patch_id(detail) == selected_patch_id
        ]
    modules: Dict[str, Any] = {}
    if "bp" in selected_modules:
        modules["bp"] = build_bp_analysis(
            details, team_id, hero_map, patch_lookup
        )
    if "players" in selected_modules:
        recent = build_recent_player_analysis(details, team_id, hero_map)
        recent.update(fetch_player_archives(service, recent["overview"], hero_map))
        modules["players"] = recent
    if "cooccurrence" in selected_modules:
        modules["cooccurrence"] = build_cooccurrence_analysis(
            details, team_id, hero_map
        )
    if "wards" in selected_modules:
        modules["wards"] = build_ward_analysis(details, team_id)

    wins = 0
    match_rows = []
    for detail in details:
        team_is_radiant = _target_is_radiant(detail, team_id)
        if team_is_radiant is None:
            continue
        won = _won(detail, team_is_radiant)
        wins += int(won)
        match_rows.append(
            {
                "match_id": detail.get("match_id"),
                "opponent": _opponent_name(detail, team_is_radiant),
                "result": "胜" if won else "负",
                "duration_minutes": round(int(detail.get("duration") or 0) / 60, 1),
                "patch": _patch_id(detail),
                "版本": _patch_label(_patch_id(detail), patch_lookup),
                "radiant_score": detail.get("radiant_score"),
                "dire_score": detail.get("dire_score"),
                "league_id": detail.get("leagueid"),
                "start_time": detail.get("start_time"),
            }
        )
    total = len(match_rows)
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
            for player in roster
            if player.get("is_current_team_member")
        ],
        "statistics": {
            "matches_requested": limit,
            "matches_fetched": unfiltered_detail_count,
            "matches_analyzed": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 2) if total else 0,
            "patch_filter": "全部版本"
            if selected_patch_id is None
            else _patch_label(selected_patch_id, patch_lookup),
        },
        "matches": match_rows,
        "modules": modules,
        "data_quality": {
            "match_fetch_errors": errors,
            "requested_patch": requested_patch or "all",
            "selected_patch": selected_patch_id,
            "available_patches": [
                {"id": patch_id, "name": _patch_label(patch_id, patch_lookup)}
                for patch_id in available_patch_ids
            ],
            "note": "仅分析 OpenDota 当前可公开返回的数据；缺失字段不进行推断。",
        },
    }
