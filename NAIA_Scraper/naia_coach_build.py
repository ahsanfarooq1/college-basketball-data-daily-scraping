###################################################################
# NAIA Coach-View stats builder (from PrestoSports S3 JSON)       #
#                                                                 #
# NAIA's "Coach View" is JavaScript-rendered: the HTML shell loads #
# static JSON from prestosports-downloads.s3.us-west-2.amazonaws   #
# .com (NOT behind Cloudflare). Two league-wide files hold         #
# everything:                                                     #
#   teamsData/{hash}.json    -> every team's season totals         #
#   playersData/{hash}.json  -> every player's season totals       #
# Both hashes live in any coach-view page's HTML shell (the        #
# ps.rendering.team.initialize(...) call). Pass a saved shell with #
# --shell, or the two URLs directly, or local files with --infile. #
#                                                                 #
# Output = EXACTLY the NWAC coach-view schema (nwac_team_stats):    #
# a flat list of team_total + player records, grouped by team.     #
###################################################################

import argparse
import json
import re
import time
import urllib.request

# ---- NWAC output schema (exact key order) ----
SCHEMA = ["team_id", "team_name", "record_type", "Name", "GP", "TotalGamesChecked", "GS", "MPG", "PPG",
          "DEFR", "OFFR", "RPG", "APG", "SPG", "BPG", "TPG", "PFPG", "Min", "Pts",
          "FGM", "FGA", "FG%", "PPS", "AFG%", "3PM", "3PA", "3P%", "FTM", "FTA", "FT%",
          "2FGM", "2FGA", "2FG%", "OReb", "DReb", "Reb", "Ast", "Stl", "Blk", "TO",
          "PF", "DQ", "Ast:TO", "Stl:TO", "Stl:PF", "Blk:PF", "Per_40", "Per_32"]


def _num(v):
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "None"):
        return 0
    try:
        return int(s) if s.lstrip("-").isdigit() else float(s)
    except ValueError:
        return 0


def _pct(m, a):
    return round(m / a * 100, 1) if a else 0.0


def _ratio(n, d):
    return round(n / d, 2) if d else 0.0


def make_record(stats, team_id, team_name, name, record_type):
    """Map a PrestoSports stat dict -> one NWAC-schema record."""
    g = stats.get
    gp = _num(g("gp"))
    fgm, fga = _num(g("fgm")), _num(g("fga"))
    tpm, tpa = _num(g("fgm3")), _num(g("fga3"))
    ftm, fta = _num(g("ftm")), _num(g("fta"))
    twoM, twoA = fgm - tpm, fga - tpa
    pts, mn = _num(g("pts")), _num(g("min"))
    oreb, dreb, reb = _num(g("oreb")), _num(g("dreb")), _num(g("treb"))
    ast, stl, blk = _num(g("ast")), _num(g("stl")), _num(g("blk"))
    to, pf = _num(g("to")), _num(g("pf"))

    rec = {
        "team_id": team_id,
        "team_name": team_name,
        "record_type": record_type,
        "Name": name,
        "GP": gp,
        "GS": _num(g("gs")),
        "MPG": _num(g("minpg")),
        "PPG": _num(g("ptspg")),
        "DEFR": _num(g("drebpg")),
        "OFFR": _num(g("orebpg")),
        "RPG": _num(g("trebpg")),
        "APG": _num(g("astpg")),
        "SPG": _num(g("stlpg")),
        "BPG": _num(g("blkpg")),
        "TPG": _num(g("topg")),
        "PFPG": _num(g("pfpg")),
        "Min": mn,
        "Pts": pts,
        "FGM": fgm, "FGA": fga, "FG%": _num(g("fgpt")),
        "PPS": round(pts / fga, 1) if fga else 0.0,
        "AFG%": round((fgm + 0.5 * tpm) / fga * 100, 1) if fga else 0.0,
        "3PM": tpm, "3PA": tpa, "3P%": _num(g("fgpt3")),
        "FTM": ftm, "FTA": fta, "FT%": _num(g("ftpt")),
        "2FGM": twoM, "2FGA": twoA, "2FG%": _pct(twoM, twoA),
        "OReb": oreb, "DReb": dreb, "Reb": reb,
        "Ast": ast, "Stl": stl, "Blk": blk, "TO": to, "PF": pf,
        "DQ": _num(g("dq")),
        "Ast:TO": _num(g("ato")) or _ratio(ast, to),
        "Stl:TO": _ratio(stl, to), "Stl:PF": _ratio(stl, pf), "Blk:PF": _ratio(blk, pf),
    }
    if mn > 0:
        rec["Per_40"] = {k: round(v / mn * 40, 1) for k, v in
                         (("Pts", pts), ("Reb", reb), ("Ast", ast), ("Stl", stl), ("Blk", blk))}
        rec["Per_32"] = {k: round(v / mn * 32, 1) for k, v in
                         (("Pts", pts), ("Reb", reb), ("Ast", ast), ("Stl", stl), ("Blk", blk))}
    else:
        rec["Per_40"] = rec["Per_32"] = None
    # TotalGamesChecked mirrors GP, but only on the team's "Season Totals" row
    if record_type == "team_total":
        rec["TotalGamesChecked"] = gp
    return {k: rec[k] for k in SCHEMA if k in rec}


def build(teams_data, players_data):
    """Flat list: for each team, its team_total then its players (by pts desc)."""
    teams = teams_data["teams"] if isinstance(teams_data, dict) else teams_data
    individuals = players_data["individuals"] if isinstance(players_data, dict) else players_data

    # group players by teamId
    by_team = {}
    for p in individuals:
        by_team.setdefault(p.get("teamId"), []).append(p)

    out = []
    for t in teams:
        tid, tname = t.get("teamId"), t.get("name")
        out.append(make_record(t.get("stats", {}), tid, tname, "Season Totals", "team_total"))
        players = by_team.get(tid, [])
        players.sort(key=lambda p: _num(p.get("stats", {}).get("pts")), reverse=True)
        for p in players:
            nm = p.get("fullName") or f"{p.get('firstName','')} {p.get('lastName','')}".strip()
            out.append(make_record(p.get("stats", {}), tid, tname, nm, "player"))
    return out


def fetch_json(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last = None
    for attempt in range(4):          # big S3 files occasionally time out: retry
        try:
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def urls_from_shell(html):
    td = re.search(r'https://[^"\']*/teams(?:Page)?Data/(?:v1/[A-Za-z0-9]+/)?[A-Za-z0-9]+\.json', html)
    pd = re.search(r'https://[^"\']*/players(?:Page)?Data/(?:v1/[A-Za-z0-9]+/)?[A-Za-z0-9]+\.json', html)
    return (td.group(0) if td else None), (pd.group(0) if pd else None)


def season_suffix(season):
    a, b = (season.split("-") + [""])[:2]
    if len(b) == 2:
        b = a[:2] + b
    return f"{a}_{b}"


def main():
    ap = argparse.ArgumentParser(description="Build NAIA coach-view stats from PrestoSports S3 JSON")
    ap.add_argument("--sport", default="mbkb")
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--teams-infile")     # local teamsData json
    ap.add_argument("--players-infile")   # local playersData json
    ap.add_argument("--teams-url")        # S3 teamsData url
    ap.add_argument("--players-url")      # S3 playersData url
    ap.add_argument("--shell")            # coach-view HTML shell to extract both urls
    ap.add_argument("--output")
    args = ap.parse_args()

    teams_url, players_url = args.teams_url, args.players_url
    if args.shell:
        t, p = urls_from_shell(open(args.shell, encoding="utf-8").read())
        teams_url = teams_url or t
        players_url = players_url or p
        print(f"[INFO] from shell: teamsData={teams_url}\n            playersData={players_url}")

    teams_data = (json.load(open(args.teams_infile, encoding="utf-8")) if args.teams_infile
                  else fetch_json(teams_url))
    players_data = (json.load(open(args.players_infile, encoding="utf-8")) if args.players_infile
                    else fetch_json(players_url))

    recs = build(teams_data, players_data)
    n_team = sum(1 for r in recs if r["record_type"] == "team_total")
    n_play = sum(1 for r in recs if r["record_type"] == "player")
    out = args.output or f"Naia_Coach_Stats_{args.sport.upper()}_{season_suffix(args.season)}.json"
    json.dump(recs, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[DONE] {n_team} team_total + {n_play} player = {len(recs)} records -> {out}")


if __name__ == "__main__":
    main()
