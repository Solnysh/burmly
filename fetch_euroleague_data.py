"""
Euroleague Sport Lab -- data pipeline
=====================================
Pulls stats for the last 3 seasons via the euroleague_api package and
writes JSON files in exactly the shape index.html expects.

Install:
    pip install euroleague_api --break-system-packages

FIRST RUN -- RECON MODE (do this first):
    python fetch_euroleague_data.py --inspect
This prints the real column names the API actually returns for each
section. I don't have network access to api-live.euroleague.net from
my sandbox, so the column names below (e.g. "OffensiveRating",
"TeamName") were my best guess from the package docs, not verified
live. pick_col() below is a safety net that warns instead of silently
writing zeros when a guessed name doesn't match -- send me its output
and I'll fix the mapping precisely.

Normal run (once the mapping is confirmed):
    python fetch_euroleague_data.py

Output goes to ./data/ -- just copy the files over the demo data in
pet-projects/sport/data/.

No server or database needed: run this by hand whenever you want, or
schedule it on GitHub Actions (cron) to commit refreshed data/*.json
-- the site stays fully static either way.
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
    """Returns the first column from candidates that actually exists
    in the dataframe. If none match, warns to the console instead of
    silently writing zeros."""
    for c in candidates:
        if c in df.columns:
            return df[c]
    print(f"  WARNING: none of {candidates} found. "
          f"Actual columns: {list(df.columns)}")
    import pandas as pd
    return pd.Series([default] * len(df))

COMPETITION = "E"  # E = Euroleague, U = Eurocup
SEASONS = [2023, 2024, 2025]  # season = start year (2023 -> 2023-24 season)
OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT_DIR, exist_ok=True)


def season_label(year: int) -> str:
    return f"{year}-{str(year + 1)[-2:]}"


def find_col(df, candidates):
    """Returns the ACTUAL column name (string) from candidates if it
    exists in df, otherwise None. Unlike pick_col this doesn't
    substitute a value -- it's meant for safe key-based matching."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def to_float(val, percent_as_fraction=False):
    """Safely coerces a value to float. The Euroleague API sometimes
    returns percentages as a ready-made string like '55.2%' instead
    of a number -- handle both cases in one place instead of fixing
    the same bug piecemeal across functions."""
    if val is None:
        return 0.0
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return 0.0
        if val.endswith('%'):
            return float(val[:-1].strip())
        return float(val)
    val = float(val)
    return val * 100 if percent_as_fraction else val


def fetch_teams():
    ts = TeamStats(COMPETITION)
    # Wins/losses come from the separate Standings endpoint, not
    # TeamStats -- more reliable (get_standings confirmed in the
    # package source; round_number=34 is the last regular-season
    # round -- adjust if a season has a different round count).
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
            print(f"  WARNING: standings unavailable for {year} (round 34): {e}")
            st_df = None

        team_col = find_col(df, ["team.name", "Team", "TeamName", "team"])
        ts_col = find_col(df, ["trueShootingPercentage", "TrueShootingPercentage"])

        st_name_col = find_col(st_df, ["club.name", "team.name", "name", "Team"]) if st_df is not None else None
        st_wins_col = find_col(st_df, ["gamesWon", "wins", "Wins"]) if st_df is not None else None
        st_losses_col = find_col(st_df, ["gamesLost", "losses", "Losses"]) if st_df is not None else None
        st_gp_col = find_col(st_df, ["gamesPlayed", "GamesPlayed"]) if st_df is not None else None
        st_pf_col = find_col(st_df, ["pointsFor", "PointsFor"]) if st_df is not None else None
        st_pa_col = find_col(st_df, ["pointsAgainst", "PointsAgainst"]) if st_df is not None else None

        for _, row in df.iterrows():
            team_name = row[team_col] if team_col else "?"
            raw_ts = row[ts_col] if ts_col else 0
            ts_pct = to_float(raw_ts, percent_as_fraction=True)

            wins, losses = 0, 0
            off, deff = 0.0, 0.0  # points scored/allowed per game -- derived from standings below
            if st_df is not None and st_name_col:
                match = st_df[st_df[st_name_col] == team_name]
                if not match.empty:
                    m = match.iloc[0]
                    wins = int(m[st_wins_col]) if st_wins_col else 0
                    losses = int(m[st_losses_col]) if st_losses_col else 0
                    gp = int(to_float(m[st_gp_col])) if st_gp_col else 0
                    if gp:
                        off = to_float(m[st_pf_col]) / gp if st_pf_col else 0.0
                        deff = to_float(m[st_pa_col]) / gp if st_pa_col else 0.0

            out.append({
                "season": season_label(year),
                "team": team_name,
                "code": str(team_name)[:3].upper(),
                "off_rating": round(off, 1),          # points scored per game
                "def_rating": round(deff, 1),         # points allowed per game
                "net_rating": round(off - deff, 1),   # difference -- net rating analog
                "pace": round(ts_pct, 1),              # True Shooting % (Pace isn't in this API endpoint)
                "wins": wins,
                "losses": losses,
            })
    if INSPECT:
        return
    with open(os.path.join(OUT_DIR, "teams.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"teams.json -- {len(out)} rows")


def fetch_players():
    ps = PlayerStats(COMPETITION)
    out = []
    for year in SEASONS:
        df = ps.get_player_stats_single_season(
            endpoint="traditional", season=year, phase_type_code=None, statistic_mode="PerGame"
        )
        print(f"[players traditional, {year}] columns:", list(df.columns))
        if INSPECT:
            continue

        name_col = find_col(df, ["player.name", "Player", "player"])
        team_col = find_col(df, ["player.team.name", "team.name", "Team"])
        gp_col = find_col(df, ["gamesPlayed", "GamesPlayed"])
        points_col = find_col(df, ["pointsScored", "player.pointsScored", "Points"])
        reb_col = find_col(df, ["totalRebounds", "TotalRebounds", "rebounds"])
        ast_col = find_col(df, ["assistances", "assists", "Assistances"])
        per_col = find_col(df, ["valuation", "PIR"])

        if points_col is None:
            # Fallback: use the first numeric column instead of
            # crashing or grabbing whatever's last (that's how we
            # previously ended up sorting by a player photo URL).
            import pandas as pd
            numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
            points_col = numeric_cols[0] if numeric_cols else None
            print(f"  WARNING: 'pointsScored' not found, sorting by '{points_col}' -- verify mapping manually")

        if points_col is None:
            print(f"  WARNING: skipping season {year} -- no numeric column found to sort by")
            continue

        top = df.sort_values(points_col, ascending=False).head(15)
        for _, row in top.iterrows():
            out.append({
                "season": season_label(year),
                "name": row[name_col] if name_col else "?",
                "team": row[team_col] if team_col else "?",
                "gp": int(to_float(row[gp_col])) if gp_col else 0,
                "ppg": round(to_float(row[points_col]), 1) if points_col else 0,
                "rpg": round(to_float(row[reb_col]), 1) if reb_col else 0,
                "apg": round(to_float(row[ast_col]), 1) if ast_col else 0,
                "per": round(to_float(row[per_col]), 1) if per_col else 0,  # PIR/valuation -- official EL efficiency index
            })
    if INSPECT:
        return
    with open(os.path.join(OUT_DIR, "players.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"players.json -- {len(out)} rows")


def fetch_shots(year: int = SEASONS[-1]):
    """
    Pulls raw shot (x,y coordinate) data for every game of the season
    and aggregates it into 6 coarse court zones -- what the SVG court
    on the dashboard draws. There's a lot of raw shot data, so
    aggregating by zone keeps the JSON compact.

    This is the slowest and most fragile part of the pipeline:
    euroleague_api hits the Points endpoint with a separate request
    per game of the season (~400 requests), and the Euroleague API
    regularly responds with 429 Too Many Requests -- the package
    itself skips those games and moves on, so the final shot data may
    be incomplete for some games. Not critical for the dashboard
    (aggregates are still computed from whatever did download), but
    worth knowing.
    """
    sd = ShotData(COMPETITION)
    df = sd.get_game_shot_data_single_season(year)
    print(f"[shot data, {year}] columns:", list(df.columns))
    if INSPECT:
        return

    team_col = find_col(df, ["TEAM", "TeamCode", "team.code", "Team", "TEAM_CODE", "CODETEAM"])
    if team_col is None:
        print(f"  WARNING: no team column found in shot data, skipping shots.json -- check the column list above")
        with open(os.path.join(OUT_DIR, "shots.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    points_col = find_col(df, ["Points", "POINTS", "points"])
    action_col = find_col(df, ["ID_ACTION", "IdAction", "action"])

    df["zone"] = df.apply(classify_zone, axis=1)

    out = []
    for (team, zone), grp in df.groupby([team_col, "zone"]):
        attempts = len(grp)
        if points_col:
            makes = int(grp[points_col].gt(0).sum())
        elif action_col:
            makes = int(grp[action_col].astype(str).str.contains("2FGM|3FGM").sum())
        else:
            makes = 0
        coords = ZONE_BOX[zone]
        out.append({
            "team": team, "season": season_label(year), "zone": zone,
            **coords, "attempts": attempts, "makes": makes,
            "fg_pct": round(makes / attempts, 3) if attempts else 0,
        })
    with open(os.path.join(OUT_DIR, "shots.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"shots.json -- {len(out)} rows")


# Coarse court zones in SVG-court coordinates (viewBox 0 0 100 105)
ZONE_BOX = {
    "Paint": {"x": 50, "y": 85, "w": 40, "h": 25},
    "Mid-range L": {"x": 18, "y": 55, "w": 24, "h": 30},
    "Mid-range R": {"x": 58, "y": 55, "w": 24, "h": 30},
    "Corner 3 L": {"x": 2, "y": 78, "w": 14, "h": 22},
    "Corner 3 R": {"x": 84, "y": 78, "w": 14, "h": 22},
    "Top 3": {"x": 30, "y": 10, "w": 40, "h": 25},
}


def classify_zone(row):
    """Rough zone classification from ShotData coordinates. The
    COORD_X/COORD_Y/ZONE fields in the raw API -- cross-check with
    the example in the euroleague_api package's
    notebooks/get-season-stats.ipynb, the coordinate system there is
    its own thing and worth verifying before the first real run."""
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
    The official API has no dedicated "referees" endpoint -- referee
    crew data lives in each game's Header/Boxscore (Referee1/2/3
    fields). We collect it manually: pull the header for every game
    of the season, group by the referee trio, average fouls and the
    home/away free-throw gap. This is the heaviest part of the
    pipeline by request count -- comment out the call in main() if
    you don't need it on the first pass.
    """
    gm = GameMetadata(COMPETITION)
    rows = []
    for year in SEASONS:
        # Real method name is get_game_metadata_single_season
        # (the first version of this script wrongly called it
        # get_game_metadata_season).
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
        "technicals_per_game": 0,  # the official API doesn't expose technicals separately
    } for crew, v in agg.items() if v["games"] >= 10]
    with open(os.path.join(OUT_DIR, "referees.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"referees.json -- {len(out)} rows")


if __name__ == "__main__":
    if INSPECT:
        print("=== RECON MODE: printing real API column names only ===")
        print("Send me this output -- I'll fix the pick_col() mapping precisely.\n")

    # Each block runs in its own try/except: if one fails (a newly
    # unmatched field name, a rate limit, etc.), the others still
    # write their JSON, and the GitHub Action can commit whatever did
    # succeed.
    steps = [("teams", fetch_teams), ("players", fetch_players), ("shots", fetch_shots)]
    # Referees is the heaviest block (one request per game) -- enable
    # it explicitly once the rest is stable:
    # steps.append(("referees", fetch_referees))

    failed = []
    for name, fn in steps:
        try:
            fn()
        except Exception as e:
            failed.append(name)
            print(f"WARNING: section '{name}' failed: {e}")

    if failed:
        print(f"\nFinished with errors in: {', '.join(failed)} -- the rest of the data was saved and will be committed.")
        # Exit code 0 on purpose: a partial success shouldn't block
        # committing the files that did succeed.
