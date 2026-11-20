#!/usr/bin/env python3
"""
nccaa_scraper.py - NCCAA (thenccaa.org, Sidearm) team list + team stats + player stats.

Site structure (verified 2026-11-12):
  Division is encoded in the sport "path":   mbball = Men's DI,  mbball2 = Men's DII
                                             wbball = Women's DI, wbball2 = Women's DII
  Hub   : /stats.aspx?path=<path>&year=<Y>   (year=2026 -> 2026-27)
          ~20 team leaderboard tables (one row per team, offense AND opponent/defense
          totals) + individual leaderboards.  The team slug is in each row's link.
  Team  : /teamstats.aspx?path=<path>&year=<Y>&school=<slug>
          player table is filled client-side from a JSON service; the page embeds the
          internal team code (team_id) that the service needs:
          /services/conf_stats.ashx?method=get_team_stats&team_id=<code>&sport=<path>&year=<Y>
          -> players (+starts, position, class, TO, PF), team totals (stats_stats),
             opponent totals, W-L records, and a game-by-game list.
  Members: /sports/2017/6/14/Member_Schools_Division_17-18.aspx  (DI / DII + '#' = dual)

Outputs (in --out-dir, default ./2026_27_data):
  nccaa_teams_<sport>_26_27.json         team list (both divisions)
  nccaa_team_stats_<sport>_26_27.json    one row per team: offense + opponent totals
  nccaa_player_stats_<sport>_26_27.json  one row per player per team
  nccaa_games_<sport>_26_27.json         team game log (bonus)

Usage:
  python nccaa_scraper.py                      # men's, 2026-27
  python nccaa_scraper.py --sport both
  python nccaa_scraper.py --year 2025          # 2025-26
"""
import argparse
import json
import os
import re
import sys
import time
import datetime as dt
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

BASE = "https://thenccaa.org"
MEMBERS_URL = BASE + "/sports/2017/6/14/Member_Schools_Division_17-18.aspx"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
# sport code -> {division: sidearm path}
SPORTS = {"MBKB": {"DI": "mbball", "DII": "mbball2"},
          "WBKB": {"DI": "wbball", "DII": "wbball2"}}
DELAY = 0.4

# hub table heading -> [(index among cells after the G column, output field)]
TEAM_SPEC = {
    "Offense": [(0, "record"), (1, "pts"), (2, "ppg")],
    "Defense": [(1, "opp_pts"), (2, "opp_ppg")],
    "Margin": [(2, "scoring_margin")],
    "Team FG Percentage": [(0, "fgm"), (1, "fga"), (2, "fg_pct")],
    "Opponent FG Percentage": [(0, "opp_fgm"), (1, "opp_fga"), (2, "opp_fg_pct")],
    "Team 3-Point FG Percentage": [(0, "fg3m"), (1, "fg3a"), (2, "fg3_pct")],
    "Opponent 3-Point FG Percentage": [(0, "opp_fg3m"), (1, "opp_fg3a"), (2, "opp_fg3_pct")],
    "Team FT Percentage": [(0, "ftm"), (1, "fta"), (2, "ft_pct")],
    "Opponent FT Percentage": [(0, "opp_ftm"), (1, "opp_fta"), (2, "opp_ft_pct")],
    "Combined Team Rebounds": [(0, "reb"), (1, "rpg")],
    "Team Offensive Rebounds": [(0, "oreb"), (1, "orpg")],
    "Team Defensive Rebounds": [(0, "dreb"), (1, "drpg")],
    "Combined Opponent Rebounds": [(0, "opp_reb"), (1, "opp_rpg")],
    "Rebounding Margin": [(4, "reb_margin")],
    "Turnover Margin": [(0, "to"), (1, "topg"), (2, "opp_to"), (3, "opp_topg"), (4, "to_margin")],
    "Assist/Turnover Ratio": [(4, "ast_to_ratio")],
    "Blocked Shots": [(0, "blk"), (1, "bpg")],
    "Steals": [(0, "stl"), (1, "spg")],
    "Assists": [(0, "ast"), (1, "apg")],
}
PLAYER_COLS = ["GP", "MP", "PTS", "PPG", "FGM", "FGA", "FG%", "3PM", "3PA", "3P%", "FTM",
               "FTA", "FT%", "OREB", "DREB", "REB/G", "AST", "AST/G", "STL", "BLK"]
INT_FIELDS = {"games", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "oreb", "dreb",
              "ast", "stl", "blk", "to", "opp_pts", "opp_fgm", "opp_fga", "opp_fg3m", "opp_fg3a",
              "opp_ftm", "opp_fta", "opp_reb", "opp_to"}

sess = requests.Session()
sess.headers.update(UA)


def get(url, tries=4):
    for i in range(tries):
        try:
            r = sess.get(url, timeout=40)
            if r.status_code == 200 and len(r.text) > 5000:
                return r.text
            err = f"HTTP {r.status_code} len={len(r.text)}"
        except requests.RequestException as e:
            err = str(e)
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed {url}: {err}")


def num(s):
    s = (s or "").strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    try:
        f = float(s)
        return int(f) if re.fullmatch(r"-?\d+", s) else f
    except ValueError:
        return s


def norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower().replace("�", "").replace("\xa0", ""))


def slug_of(href):
    q = parse_qs(urlparse(href).query)
    return (q.get("school") or [""])[0]


# ---------------------------------------------------------------- members page
def parse_members():
    """-> {norm(name): {'member_name', 'member_division', 'dual_division'}}"""
    soup = BeautifulSoup(get(MEMBERS_URL), "html.parser")
    out = {}
    for t in soup.find_all("table"):
        rows = [r.get_text(" ", strip=True) for r in t.find_all("tr")]
        if not rows:
            continue
        m = re.match(r"^[A-Z] \((DI|DII)\)$", rows[0])
        if not m:
            continue
        for r in rows[1:]:
            r = r.replace("�", " ").replace("\xa0", " ").strip()
            if not r or r.startswith("[ Back"):
                continue
            dual = r.endswith("#")
            name = re.sub(r"\s*#\s*$", "", r).strip()
            out[norm(name)] = {"member_name": name, "member_division": m.group(1),
                               "dual_division": dual}
    return out


# ---------------------------------------------------------------- hub page
def parse_hub(html, path, year):
    """-> teams {slug: row}, plus list of unmapped headings"""
    soup = BeautifulSoup(html, "html.parser")
    teams, unmapped = {}, []
    for t in soup.find_all("table", class_="sidearm-table"):
        hdr = [c.get_text(strip=True) for c in t.find("tr").find_all("th")]
        if len(hdr) < 3 or hdr[1] != "Team":
            continue                                   # individual / results tables
        h = t.find_previous(re.compile("^h[2-5]$"))
        heading = h.get_text(strip=True) if h else ""
        spec = TEAM_SPEC.get(heading)
        if spec is None:
            unmapped.append(heading)
        for tr in t.find_all("tr")[1:]:
            cells = tr.find_all(["th", "td"])[1:]      # drop empty Index cell
            if len(cells) < 2:
                continue
            a = cells[0].find("a")
            if not a:
                continue
            slug = slug_of(a["href"])
            row = teams.setdefault(slug, {
                "team_id": slug, "team_name": cells[0].get_text(strip=True),
                "url": urljoin(BASE, a["href"]), "path": path})
            vals = [c.get_text(strip=True) for c in cells[1:]]   # G, c0, c1...
            row.setdefault("games", num(vals[0]))
            for idx, field in (spec or []):
                if idx + 1 < len(vals):
                    v = num(vals[idx + 1]) if field != "record" else vals[idx + 1]
                    row[field] = v
    return teams, sorted(set(unmapped))


# ---------------------------------------------------------------- team page / JSON
STAT_MAP = {  # JSON stats_stats key -> output field
    "games_played": "games", "minutes_played": "min", "points_scored": "pts",
    "field_goals_made": "fgm", "field_goals_attempted": "fga", "field_goals_pct": "fg_pct",
    "three_points_made": "fg3m", "three_points_attempted": "fg3a", "three_points_pct": "fg3_pct",
    "free_throws_made": "ftm", "free_throws_attempted": "fta", "free_throws_pct": "ft_pct",
    "offensive_rebounds": "oreb", "defensive_rebounds": "dreb", "total_rebounds": "reb",
    "assists": "ast", "steals": "stl", "blocked_shots": "blk", "turnovers": "to",
    "personal_fouls": "pf", "technical_fouls": "tf", "disqualifications": "dq",
    "deadballs": "deadball_reb", "points_per_game": "ppg", "rebounds_per_game": "rpg",
    "assists_per_game": "apg", "steals_per_game": "spg", "blocked_shots_per_game": "bpg"}


def mapstats(d, prefix=""):
    return {prefix + out: num(d.get(k)) for k, out in STAT_MAP.items() if k in d}


def fetch_team(path, year, slug):
    """Team page -> internal team code -> /services/conf_stats.ashx JSON."""
    page = get(f"{BASE}/teamstats.aspx?path={path}&year={year}&school={slug}")
    m = re.search(r"team_id: '([^']*)'", page)
    code = m.group(1) if m else "0"
    r = sess.get(f"{BASE}/services/conf_stats.ashx", timeout=40, params=dict(
        method="get_team_stats", team_id=code, sport=path, year=year,
        conf="False", postseason="False"))
    r.raise_for_status()
    return code, r.json()


def build_players(data, base):
    out = []
    for p in data.get("players") or []:
        if (p.get("name") or "").upper() == "TEAM":
            continue                              # TEAM row = team rebounds, not a player
        name = " ".join((p["name"] or "").split())   # strip stray/double spaces
        last, _, first = name.partition(",")
        rec = {**base, "player_name": name,
               "first_name": first.strip() if first else name.strip(),
               "last_name": last.strip() if first else "",
               "uniform": p.get("uniform"), "position": p.get("position"),
               "class_year": p.get("year"), "games_started": num(p.get("games_started")),
               "player_id": p.get("sdrm_id")}
        rec.update(mapstats(p.get("stats_stats") or {}))
        if any(isinstance(v, (int, float)) and v < 0 for v in rec.values()):
            # source-side correction artifact (negative stats) -> invalid, skip + log
            print(f"  SKIP invalid row (negative stats): {base['team_id']} / {name}")
            continue
        out.append(rec)
    return out


def build_games(data, base):
    out = []
    for g in data.get("games") or []:
        out.append({**base, "game_number": num(g.get("number")), "game_id": g.get("game_id"),
                    "date": g.get("date"), "opponent": g.get("opp_name"),
                    "opponent_code": g.get("opp_id"), "home_away": g.get("home_or_away"),
                    "neutral": g.get("neutral_game") == "Y", "site": g.get("site"),
                    "result": g.get("result"), "score": num(g.get("own_score")),
                    "opp_score": num(g.get("opp_score")), "attendance": num(g.get("attendance")),
                    "fgm": num(g.get("field_goals_made")), "fga": num(g.get("field_goals_a")),
                    "fg3m": num(g.get("three_point_made")), "fg3a": num(g.get("three_pts_a")),
                    "ftm": num(g.get("free_throws_made")), "fta": num(g.get("free_throws_a")),
                    "reb": num(g.get("rebounds")), "ast": num(g.get("assists")),
                    "stl": num(g.get("steals")), "blk": num(g.get("blocked_shots")),
                    "to": num(g.get("turnovers")), "pf": num(g.get("personal_fouls"))})
    return out


# ---------------------------------------------------------------- main
def run(sport, year, out_dir):
    season = f"{year}-{str(year + 1)[2:]}"
    tag = f"{sport.lower()}_{str(year)[2:]}_{str(year + 1)[2:]}"
    members = parse_members()
    print(f"[{sport}] member schools parsed: {len(members)}")
    team_list, team_stats, player_stats, games = [], [], [], []
    for division, path in SPORTS[sport].items():
        teams, unmapped = parse_hub(get(f"{BASE}/stats.aspx?path={path}&year={year}"), path, year)
        if unmapped:
            print(f"  note: hub tables not used for team stats ({division}): {unmapped}")
        print(f"[{sport} {division}] teams on hub: {len(teams)}")
        for slug, row in teams.items():
            m = members.get(norm(row["team_name"]), {})
            base = {"team_id": slug, "team_name": row["team_name"], "division": division,
                    "sport": sport.lower(), "season": season}
            team_list.append({**base, "dual_division": m.get("dual_division"), "url": row["url"]})
            time.sleep(DELAY)
            try:
                code, data = fetch_team(path, year, slug)
            except Exception as e:
                print("  FAIL", slug, e)
                continue
            st = data.get("stats_stats") or {}
            ts = {**base, "record_type": "team_total", "nccaa_team_code": code,
                  "overall_record": data.get("overall_record"),
                  "home_record": data.get("home_record"), "away_record": data.get("away_record"),
                  "neutral_record": data.get("neutral_record"),
                  "conference_record": data.get("conference_record")}
            ts.update(mapstats(st))
            ts.update(mapstats(data.get("opponent_stats") or {}, "opp_"))
            ts["scraped_at"] = dt.datetime.now().isoformat(timespec="seconds")
            team_stats.append(ts)
            pl = build_players(data, base)
            player_stats.extend(pl)
            games.extend(build_games(data, base))
            flag = ""
            if ts.get("pts") != row.get("pts") or ts.get("games") != row.get("games"):
                flag = (f"  MISMATCH vs hub (json pts={ts.get('pts')} g={ts.get('games')}; "
                        f"hub pts={row.get('pts')} g={row.get('games')})")
            print(f"  {slug:<28} code={code:<7} G={ts.get('games')} players={len(pl)}{flag}")
    unmatched = [t["team_name"] for t in team_list if t["dual_division"] is None]
    if unmatched:
        print(f"  note: not matched to member page (name mismatch): {unmatched}")
    os.makedirs(out_dir, exist_ok=True)
    for kind, data in (("teams", team_list), ("team_stats", team_stats),
                       ("player_stats", player_stats), ("games", games)):
        fp = os.path.join(out_dir, f"nccaa_{kind}_{tag}.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  wrote {fp} ({len(data)} rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="MBKB", choices=["MBKB", "WBKB", "both"])
    ap.add_argument("--year", type=int, default=2026, help="start year; 2026 = 2026-27")
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                      "2026_27_data"))
    a = ap.parse_args()
    for sp in (["MBKB", "WBKB"] if a.sport == "both" else [a.sport]):
        run(sp, a.year, a.out_dir)


if __name__ == "__main__":
    main()
