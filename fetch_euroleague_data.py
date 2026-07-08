"""
Euroleague Sport Lab — data pipeline
=====================================
Тянет статистику за последние 3 сезона через пакет euroleague_api
и пишет JSON-файлы ровно в том формате, который ждёт index.html.

Установка:
    pip install euroleague_api --break-system-packages

ПЕРВЫЙ ЗАПУСК — РАЗВЕДКА (важно, не пропускай):
    python fetch_euroleague_data.py --inspect
Это распечатает реальные названия колонок, которые вернёт API,
для каждого раздела. У меня нет сетевого доступа к api-live.euroleague.net,
поэтому имена колонок ниже (например "OffensiveRating", "TeamName")
— это ожидаемые/наиболее вероятные названия по документации пакета,
а не проверенные вживую. pick_col() ниже подстрахует и подскажет,
если угаданное имя не совпало с реальным — просто пришли мне вывод
--inspect, и я поправлю маппинг точно.

Обычный запуск (после того как маппинг подтверждён):
    python fetch_euroleague_data.py

Результат кладётся в ./data/ — просто скопируй файлы поверх
демо-данных в pet-projects/sport/data/.

Это не требует сервера/базы: скрипт можно гонять руками раз в неделю
или повесить на GitHub Actions по расписанию (cron), закоммитив
обновлённые data/*.json — сайт как был статикой, так и остаётся.
"""
import json
import os
import sys

from euroleague_api.standings import Standings
from euroleague_api.team_stats import TeamStats
from euroleague_api.player_stats import PlayerStats
from euroleague_api.shot_data import ShotData
from euroleague_api.game_metadata import GameMetadata

INSPECT = "--inspect" in sys.argv


def pick_col(df, candidates, default=0):
    """Возвращает первую колонку из candidates, которая реально есть
    в датафрейме. Если ни одна не нашлась — предупреждает в консоль
    вместо того, чтобы тихо писать нули."""
    for c in candidates:
        if c in df.columns:
            return df[c]
    print(f"  ⚠ ни одна из {candidates} не найдена. "
          f"Реальные колонки: {list(df.columns)}")
    import pandas as pd
    return pd.Series([default] * len(df))

COMPETITION = "E"  # E = Euroleague, U = Eurocup
SEASONS = [2023, 2024, 2025]  # season = год старта (2023 -> сезон 2023-24)
OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT_DIR, exist_ok=True)


def season_label(year: int) -> str:
    return f"{year}-{str(year + 1)[-2:]}"


def find_col(df, candidates):
    """Возвращает РЕАЛЬНОЕ имя колонки (строку) из candidates, если оно
    есть в df, иначе None. В отличие от pick_col не подставляет
    значение — нужен именно для безопасного матчинга по ключу."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def fetch_teams():
    ts = TeamStats(COMPETITION)
    # W-L идёт из отдельного эндпоинта Standings, а не из TeamStats —
    # так надёжнее (get_standings подтверждён в исходниках пакета,
    # round_number=34 это последний тур регулярки; для сезона с другим
    # числом туров поправь).
    standings = Standings(COMPETITION)
    out = []
    for year in SEASONS:
        df = ts.get_team_stats_single_season(
            endpoint="advanced", season=year, phase_type_code=None, statistic_mode="PerGame"
        )
        print(f"[teams advanced, {year}] columns:", list(df.columns))
        if INSPECT:
            continue

        try:
            st_df = standings.get_standings(season=year, round_number=34)
            print(f"[standings, {year}] columns:", list(st_df.columns))
        except Exception as e:
            print(f"  ⚠ standings недоступны для {year} (round 34): {e}")
            st_df = None

        team_col = find_col(df, ["team.name", "Team", "TeamName", "team"])
        off_col = find_col(df, ["stats.OffensiveRating", "OffensiveRating", "offensiveRating"])
        def_col = find_col(df, ["stats.DefensiveRating", "DefensiveRating", "defensiveRating"])
        pace_col = find_col(df, ["stats.Pace", "Pace", "pace"])

        st_name_col = find_col(st_df, ["team.name", "name", "Team"]) if st_df is not None else None
        st_wins_col = find_col(st_df, ["gamesWon", "wins", "Wins"]) if st_df is not None else None
        st_losses_col = find_col(st_df, ["gamesLost", "losses", "Losses"]) if st_df is not None else None

        for _, row in df.iterrows():
            team_name = row[team_col] if team_col else "?"
            off = float(row[off_col]) if off_col else 0.0
            deff = float(row[def_col]) if def_col else 0.0
            pace = float(row[pace_col]) if pace_col else 0.0

            wins, losses = 0, 0
            if st_df is not None and st_name_col:
                match = st_df[st_df[st_name_col] == team_name]
                if not match.empty:
                    wins = int(match.iloc[0][st_wins_col]) if st_wins_col else 0
                    losses = int(match.iloc[0][st_losses_col]) if st_losses_col else 0

            out.append({
                "season": season_label(year),
                "team": team_name,
                "code": str(team_name)[:3].upper(),
                "off_rating": round(off, 1),
                "def_rating": round(deff, 1),
                "net_rating": round(off - deff, 1),
                "pace": round(pace, 1),
                "wins": wins,
                "losses": losses,
            })
    if INSPECT:
        return
    with open(os.path.join(OUT_DIR, "teams.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"teams.json — {len(out)} rows")


def fetch_players():
    ps = PlayerStats(COMPETITION)
    out = []
    for year in SEASONS:
        df = ps.get_player_stats_single_season(
            endpoint="traditional", season=year, phase_type_code=None, statistic_mode="PerGame"
        )
        if INSPECT:
            print(f"\n[players traditional, {year}] columns:", list(df.columns))
            continue

        points_col = "player.pointsScored" if "player.pointsScored" in df.columns else \
            ("Points" if "Points" in df.columns else df.columns[-1])
        top = df.sort_values(points_col, ascending=False).head(15)
        for _, row in top.iterrows():
            out.append({
                "season": season_label(year),
                "name": row.get("player.name", row.get("Player", "")),
                "team": row.get("player.team.name", row.get("Team", "")),
                "gp": int(row.get("gamesPlayed", row.get("GamesPlayed", 0))),
                "ppg": round(float(row.get(points_col, 0)), 1),
                "rpg": round(float(row.get("totalRebounds", row.get("TotalRebounds", 0))), 1),
                "apg": round(float(row.get("assistances", row.get("Assistances", 0))), 1),
                "per": round(float(row.get("valuation", row.get("PIR", 0))), 1),  # PIR/valuation — official EL efficiency index
            })
    if INSPECT:
        return
    with open(os.path.join(OUT_DIR, "players.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"players.json — {len(out)} rows")


def fetch_shots(year: int = SEASONS[-1]):
    """
    Тянет сырые броски (x,y координаты) по каждой игре сезона и
    агрегирует их по 6 укрупнённым зонам площадки — то, что
    рисует SVG-корт на дашборде. Сырых бросков очень много,
    поэтому агрегация по зонам держит JSON компактным.
    """
    sd = ShotData(COMPETITION)
    df = sd.get_game_shot_data_single_season(year)
    if INSPECT:
        print(f"\n[shot data, {year}] columns:", list(df.columns))
        return
    df["zone"] = df.apply(classify_zone, axis=1)

    out = []
    for (team, zone), grp in df.groupby(["TeamCode", "zone"]):
        attempts = len(grp)
        makes = int(grp["Points"].gt(0).sum()) if "Points" in grp else int(grp["ID_ACTION"].str.contains("2FGM|3FGM").sum())
        coords = ZONE_BOX[zone]
        out.append({
            "team": team, "season": season_label(year), "zone": zone,
            **coords, "attempts": attempts, "makes": makes,
            "fg_pct": round(makes / attempts, 3) if attempts else 0,
        })
    with open(os.path.join(OUT_DIR, "shots.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"shots.json — {len(out)} rows")


# Укрупнённые зоны в координатах SVG-корта (viewBox 0 0 100 105)
ZONE_BOX = {
    "Paint": {"x": 50, "y": 85, "w": 40, "h": 25},
    "Mid-range L": {"x": 18, "y": 55, "w": 24, "h": 30},
    "Mid-range R": {"x": 58, "y": 55, "w": 24, "h": 30},
    "Corner 3 L": {"x": 2, "y": 78, "w": 14, "h": 22},
    "Corner 3 R": {"x": 84, "y": 78, "w": 14, "h": 22},
    "Top 3": {"x": 30, "y": 10, "w": 40, "h": 25},
}


def classify_zone(row):
    """Грубая классификация зоны по координатам броска из ShotData.
    Значения COORD_X/COORD_Y и ZONE в сыром API — сверься с примером
    в notebooks/get-season-stats.ipynb пакета euroleague_api, система
    координат там своя и её стоит проверить перед первым запуском."""
    zone_raw = str(row.get("ZONE", ""))
    if "A" in zone_raw or "B" in zone_raw:
        return "Paint"
    if row.get("POINTS", 2) == 3:
        x = row.get("COORD_X", 0)
        if x < -60:
            return "Corner 3 L"
        if x > 60:
            return "Corner 3 R"
        return "Top 3"
    x = row.get("COORD_X", 0)
    return "Mid-range L" if x < 0 else "Mid-range R"


def fetch_referees():
    """
    В официальном API нет отдельного эндпоинта "судьи" — данные о
    бригаде судей есть в Header/Boxscore каждой игры (поля Referee1/2/3).
    Собираем вручную: тянем header по каждой игре сезона, группируем
    по тройке судей, считаем средние фолы и разницу штрафных дом/выезд.
    Это самый тяжёлый по числу запросов кусок пайплайна — если не
    нужен на первом проходе, можно закомментировать вызов в main().
    """
    gm = GameMetadata(COMPETITION)
    rows = []
    for year in SEASONS:
        # Реальное имя метода — get_game_metadata_single_season
        # (в первой версии скрипта было ошибочно get_game_metadata_season)
        games = gm.get_game_metadata_single_season(year)
        if INSPECT:
            print(f"\n[game metadata, {year}] columns:", list(games.columns))
            continue
        for _, g in games.iterrows():
            crew = " / ".join(filter(None, [g.get("Referee1"), g.get("Referee2"), g.get("Referee3")]))
            if not crew:
                continue
            rows.append({
                "crew": crew,
                "total_fouls": g.get("HomeTeamFouls", 0) + g.get("AwayTeamFouls", 0),
                "home_ft": g.get("HomeFreeThrowsAttempted", 0),
                "away_ft": g.get("AwayFreeThrowsAttempted", 0),
            })
    if INSPECT:
        return
    agg = {}
    for r in rows:
        a = agg.setdefault(r["crew"], {"games": 0, "fouls": 0, "ft_diff": 0})
        a["games"] += 1
        a["fouls"] += r["total_fouls"]
        a["ft_diff"] += (r["home_ft"] - r["away_ft"])
    out = [{
        "crew": crew, "games": v["games"],
        "fouls_per_game": round(v["fouls"] / v["games"], 1),
        "home_ft_diff": round(v["ft_diff"] / v["games"], 1),
        "technicals_per_game": 0,  # официальный API не отдаёт технические отдельно
    } for crew, v in agg.items() if v["games"] >= 10]
    with open(os.path.join(OUT_DIR, "referees.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"referees.json — {len(out)} rows")


if __name__ == "__main__":
    if INSPECT:
        print("=== РЕЖИМ РАЗВЕДКИ: только печатаю реальные колонки API ===")
        print("Пришли этот вывод мне — поправлю pick_col()-маппинг точно.\n")
    fetch_teams()
    fetch_players()
    fetch_shots()
    # Судейский блок дольше всех (запрос на каждую игру — 800+ штук за
    # 3 сезона), запускай отдельно и не каждый раз:
    # fetch_referees()
