###################################################################
# NJCAA official team + player season stats  (NEW njcaa.org site)  #
#                                                                 #
# njcaa.org moved from PrestoSports to Ardor Sports Hub. Its pages #
# render from a PUBLIC JSON API (no Cloudflare / no browser):      #
#   GET {API}/tenant/stats?tenant_id=..&season=2026-27&sport=mbkb  #
#       &include_players=true&player_offset=N&player_limit=M       #
# One call returns every school (division, region, team totals) and #
# a page of players. We page through all players, merge them under  #
# their school, split by division, and write the SAME 47-key        #
# coach-view schema the old scraper produced:                       #
#   Njcaa_Coach_Stats_{sport}_{div}_{season}.json                   #
#                                                                 #
# USAGE:                                                          #
#   python njcaa_ardor_stats.py          (both sports, all divisions)#
#   python njcaa_ardor_stats.py --sport mbkb --division div1        #
###################################################################

import argparse
import gzip
import json
import os
import time
import urllib.request

API = "https://0wtr7r0cl7.execute-api.us-east-2.amazonaws.com/prod"
TENANT_ID = "404933495157163011"      # NJCAA's tenant on Ardor Sports Hub
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
           "Accept-Encoding": "gzip",
           "Origin": "https://www.njcaa.org", "Referer": "https://www.njcaa.org/"}
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "2026_2027_data")   # all 2026-27 output goes here

SCHEMA = ["team_id", "team_name", "record_type", "Name", "GP", "TotalGamesChecked", "GS", "MPG", "PPG",
          "DEFR", "OFFR", "RPG", "APG", "SPG", "BPG", "TPG", "PFPG", "Min", "Pts",
          "FGM", "FGA", "FG%", "PPS", "AFG%", "3PM", "3PA", "3P%", "FTM", "FTA", "FT%",
          "2FGM", "2FGA", "2FG%", "OReb", "DReb", "Reb", "Ast", "Stl", "Blk", "TO",
          "PF", "DQ", "Ast:TO", "Stl:TO", "Stl:PF", "Blk:PF", "Per_40", "Per_32"]
DIVISIONS = ["div1", "div2", "div3"]
SPORTS = ["mbkb", "wbkb"]


def _n(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0
    return int(x) if x == int(x) else x


def _pct(m, a):
    return round(m / a * 100, 1) if a else 0.0


def _ratio(n, d):
    return round(n / d, 2) if d else 0.0


def _pg(v, gp):
    return round(v / gp, 1) if gp else 0.0


def make_record(s, team_id, team_name, name, record_type):
    """Map an Ardor stat dict -> one coach-view schema record."""
    gp = _n(s.get("gp"))
    fgm, fga = _n(s.get("fgm")), _n(s.get("fga"))
    tpm, tpa = _n(s.get("fg3m")), _n(s.get("fg3a"))
    ftm, fta = _n(s.get("ftm")), _n(s.get("fta"))
    twoM = _n(s.get("fg2m")) if "fg2m" in s else fgm - tpm
    twoA = _n(s.get("fg2a")) if "fg2a" in s else fga - tpa
    # never negative (site sends fg2a=-1 for some players; partial rows make fgm - fg3m < 0)
    twoM = max(0, twoM)
    twoA = max(0, twoA, twoM)
    pts, mn = _n(s.get("pts")), _n(s.get("min"))
    oreb, dreb = _n(s.get("oreb")), _n(s.get("dreb"))
    reb = _n(s.get("treb")) if "treb" in s else oreb + dreb
    ast, stl, blk = _n(s.get("ast")), _n(s.get("stl")), _n(s.get("blk"))
    to, pf = _n(s.get("to")), _n(s.get("pf"))

    rec = {
        "team_id": team_id, "team_name": team_name,
        "record_type": record_type, "Name": name,
        "GP": gp, "GS": _n(s.get("gs")),
        "MPG": _pg(mn, gp), "PPG": _pg(pts, gp),
        "DEFR": _pg(dreb, gp), "OFFR": _pg(oreb, gp), "RPG": _pg(reb, gp),
        "APG": _pg(ast, gp), "SPG": _pg(stl, gp), "BPG": _pg(blk, gp),
        "TPG": _pg(to, gp), "PFPG": _pg(pf, gp),
        "Min": mn, "Pts": pts,
        "FGM": fgm, "FGA": fga, "FG%": _pct(fgm, fga),
        "PPS": round(pts / fga, 1) if fga else 0.0,
        "AFG%": round((fgm + 0.5 * tpm) / fga * 100, 1) if fga else 0.0,
        "3PM": tpm, "3PA": tpa, "3P%": _pct(tpm, tpa),
        "FTM": ftm, "FTA": fta, "FT%": _pct(ftm, fta),
        "2FGM": twoM, "2FGA": twoA, "2FG%": _pct(twoM, twoA),
        "OReb": oreb, "DReb": dreb, "Reb": reb,
        "Ast": ast, "Stl": stl, "Blk": blk, "TO": to, "PF": pf,
        "DQ": _n(s.get("dq")),
        "Ast:TO": _ratio(ast, to), "Stl:TO": _ratio(stl, to),
        "Stl:PF": _ratio(stl, pf), "Blk:PF": _ratio(blk, pf),
    }
    if mn > 0:
        for key, f in (("Per_40", 40), ("Per_32", 32)):
            rec[key] = {k: round(v / mn * f, 1) for k, v in
                        (("Pts", pts), ("Reb", reb), ("Ast", ast), ("Stl", stl), ("Blk", blk))}
    else:
        rec["Per_40"] = rec["Per_32"] = None
    if record_type == "team_total":
        rec["TotalGamesChecked"] = gp
    return {k: rec[k] for k in SCHEMA if k in rec}


def get_json(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"    [retry {i + 1}] {e}")
            time.sleep(3 * (i + 1))


def fetch_sport(sport, season, page, delay):
    """Return {teamId: school_dict_with_merged_players} for a whole sport."""
    schools, offset, total = {}, 0, None
    while total is None or offset < total:
        url = (f"{API}/tenant/stats?tenant_id={TENANT_ID}&season={season}&sport={sport}"
               f"&include_players=true&include_games=false&player_offset={offset}"
               f"&player_limit={page}&player_scope=overall")
        j = get_json(url)
        total = j.get("playerTotal", 0)
        page = j.get("playerLimit") or page   # server may cap our requested size
        got = 0
        for s in j.get("schools", []):
            tid = s["teamId"]
            if tid not in schools:
                schools[tid] = dict(s, players={})
            for p in s.get("players", []):
                schools[tid]["players"][p["playerId"]] = p
                got += 1
        print(f"  {sport}: players offset {offset}/{total} (+{got})")
        if got == 0 and offset + page < total:
            print("  [WARN] empty page before total reached")
        offset += page
        time.sleep(delay)
    return schools


def _incomplete(s):
    """League-wide endpoint sometimes returns only gp/pts/ptspg for a team's totals."""
    t = s.get("totals") or {}
    return _n(t.get("gp")) > 0 and "fga" not in t


def backfill_incomplete(schools, sport, season, delay):
    """Re-fetch teams with partial totals from the per-school endpoint (the same one
    the team's Stats page uses), which returns the full totals row + players."""
    todo = [s for s in schools.values() if _incomplete(s)]
    print(f"  {sport}: {len(todo)} team(s) with incomplete league totals -> per-team fetch")
    fixed = 0
    for s in todo:
        url = (f"{API}/tenant/stats/?tenant_id={s['tenantId']}&season={season}&sport={sport}"
               f"&include_players=true&include_games=false&team_season_id={s['teamId']}")
        try:
            j = get_json(url)
        except Exception as e:
            print(f"    [WARN] {s['school']}: {e}")
            continue
        for full in j.get("schools", []):
            if full.get("teamId") != s["teamId"]:
                continue
            if "fga" in (full.get("totals") or {}):
                s["totals"] = full["totals"]
                fixed += 1
            for p in full.get("players", []):
                s["players"][p["playerId"]] = p   # keep roster players with no stats too
        time.sleep(delay)
    print(f"  {sport}: backfilled {fixed}/{len(todo)}")


def division_of(s):
    """The site's Division I filter also includes schools with no division
    assigned (API gives None / 'open'), so treat those as d1."""
    d = s.get("division")
    return d if d in ("d1", "d2", "d3") else "d1"


def build(schools, division, include_empty=False):
    out = []
    code = "d" + division[-1]
    for s in sorted(schools.values(), key=lambda x: x["school"]):
        if division_of(s) != code:
            continue
        tstats = s.get("totals") or {}
        players = list(s["players"].values())          # whole roster, incl. players with no stats
        has_data = _n(tstats.get("gp")) > 0 or any(p.get("stats") for p in players)
        if not include_empty and not has_data:
            continue                                   # team with no data at all
        tid = s["teamId"]
        name = s["school"]   # display name used on njcaa.org
        out.append(make_record(tstats, tid, name, "Season Totals", "team_total"))
        players.sort(key=lambda p: _n((p.get("stats") or {}).get("pts")), reverse=True)
        for p in players:
            out.append(make_record(p.get("stats") or {}, tid, name, p.get("name", ""), "player"))
    return out


def main():
    ap = argparse.ArgumentParser(description="NJCAA stats from the new njcaa.org (Ardor) API")
    ap.add_argument("--sport", default="both", choices=SPORTS + ["both"])
    ap.add_argument("--division", default="all", choices=DIVISIONS + ["all"])
    ap.add_argument("--season", default="2026-27")
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--include-empty", action="store_true",
                    help="also keep teams/players with no games played")
    ap.add_argument("--keep-duplicates", action="store_true")
    ap.add_argument("--outdir", default=DATA_DIR)
    args = ap.parse_args()

    sports = SPORTS if args.sport == "both" else [args.sport]
    divs = DIVISIONS if args.division == "all" else [args.division]

    files = {}
    for sport in sports:
        print(f"\n=== {sport} {args.season} ===")
        schools = fetch_sport(sport, args.season, args.page_size, args.delay)
        backfill_incomplete(schools, sport, args.season, args.delay)
        for d in divs:
            recs = build(schools, d, args.include_empty)
            fn = f"Njcaa_Coach_Stats_{sport}_{d}_{args.season}.json"
            files[(args.season, sport, d)] = (fn, recs)

    if not args.keep_duplicates:
        from njcaa_dedupe import dedupe_fileset, new_stats
        st = new_stats()
        dedupe_fileset(files, st)
        print(f"\n[DEDUPE] zero rows {st['zero_rows_dropped']}, exact dupes {st['exact_dupes_dropped']}, "
              f"merged groups {st['merged_groups']}, phantom teams {st['phantom_teams_dropped']}")

    os.makedirs(args.outdir, exist_ok=True)
    print("\n===================== SUMMARY =====================")
    for (season, sport, d), (fn, recs) in files.items():
        json.dump(recs, open(os.path.join(args.outdir, fn), "w", encoding="utf-8"),
                  indent=2, ensure_ascii=False)
        nt = sum(r["record_type"] == "team_total" for r in recs)
        npl = sum(r["record_type"] == "player" for r in recs)
        print(f"  {sport}/{d}: {nt:>3} teams / {npl:>4} players -> {fn}")
    print("[ALL DONE]")


if __name__ == "__main__":
    main()
