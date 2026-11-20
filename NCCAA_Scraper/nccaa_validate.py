#!/usr/bin/env python3
"""
nccaa_validate.py - validate the NCCAA outputs. Exit code 1 if any ERROR.

Checks (ERROR = must fix, WARN = informational):
  1 format   : team lists, Pinecone-format files vs NAIA key set/order, unique identity keys
  2 counts   : teams list == team stats == pinecone team_total records
  3 logic    : made<=att, pts == 2*FG2M + 3*3PM + FTM, reb == oreb+dreb, GS<=GP<=team GP,
               no negatives, percentages 0-100, derived fields recompute from totals
  4 games    : game log count == team GP, sum of game scores == team pts
  5 hub      : team pts / games equal the stats-hub leaderboards (independent page)
  6 live     : (--live) re-fetch backend JSON for every team and compare to saved data
  7 info     : player-sum vs team-total gaps (expected: team rebounds, etc.)
"""
import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nccaa_scraper as sc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NAIA = os.path.join(HERE, "..", "26-27 College Data", "NAIA", "Scraped Data",
                    "Naia_Coach_Stats_MBKB_2026_2027.json")
NCCAA = os.path.join(HERE, "..", "26-27 College Data", "NCCAA")
SHORT = {"MBKB": "MBB", "WBKB": "WBB"}
errors, warns = [], []


def E(msg): errors.append(msg)
def W(msg): warns.append(msg)
def load(p): return json.load(open(p, encoding="utf-8"))


def check_sport(sport, live):
    tag = f"{sport.lower()}_26_27"
    D = os.path.join(HERE, "2026_27_data")
    teams = load(f"{D}/nccaa_teams_{tag}.json")
    ts = load(f"{D}/nccaa_team_stats_{tag}.json")
    ps = load(f"{D}/nccaa_player_stats_{tag}.json")
    gm = load(f"{D}/nccaa_games_{tag}.json")
    pc = []
    for dv in ("DI", "DII"):
        pc += load(os.path.join(NCCAA, "Scraped Data", f"NCCAA_Stats_{SHORT[sport]}_{dv}_2026_2027.json"))
    print(f"\n=== {sport}: teams={len(teams)} team_stats={len(ts)} players={len(ps)} "
          f"games={len(gm)} pinecone={len(pc)}")

    # 1 format ------------------------------------------------------------
    naia = load(NAIA)
    nkeys = {rt: list(next(r for r in naia if r["record_type"] == rt))
             for rt in ("player", "team_total")}
    for r in pc:
        got = [k for k in r if k != "division"]
        if got != nkeys[r["record_type"]]:
            E(f"{sport} pinecone key mismatch: {r['team_id']}/{r['Name']}")
            break
    keys = [(r["team_id"], r["record_type"], r["Name"]) for r in pc]
    if len(keys) != len(set(keys)):
        E(f"{sport} duplicate pinecone identity keys")
    for r in pc:
        if not r["Name"] or r["Name"] != r["Name"].strip():
            E(f"{sport} bad Name {r['Name']!r} ({r['team_id']})")
    for fp in glob.glob(os.path.join(NCCAA, "Teams List", f"NCCAA_{'Men' if sport == 'MBKB' else 'Women'}_Teams_*.json")):
        for t in load(fp):
            if set(t) != {"team_name", "data_availablity"} or t["data_availablity"] not in ("partial", "none"):
                E(f"{sport} bad team-list entry in {os.path.basename(fp)}: {t}")

    # 2 counts ------------------------------------------------------------
    pct = [r for r in pc if r["record_type"] == "team_total"]
    ids = lambda L: {(t["team_id"], t["division"]) for t in L}
    if ids(ts) != ids(teams):
        E(f"{sport} team list vs team stats differ: {sorted(ids(ts) ^ ids(teams))[:5]}")
    if {t["team_id"] for t in ts} != {r["team_id"] for r in pct}:
        E(f"{sport} team_stats vs pinecone team_total differ")
    if len(pct) != len({t["team_id"] for t in ts}):
        E(f"{sport} pinecone team count {len(pct)}")

    # 3 logic -------------------------------------------------------------
    tgp = {(t["team_id"], t["division"]): t.get("games") or 0 for t in ts}
    for src, kind in [(ts, "team"), (ps, "player")]:
        for r in src:
            who = f"{sport} {kind} {r['team_id']}/{r.get('player_name', 'TOTAL')}"
            g = lambda k: r.get(k) or 0
            for k in ("pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "oreb", "dreb",
                      "ast", "stl", "blk", "to", "pf", "min", "games"):
                if g(k) < 0:
                    E(f"{who}: negative {k}")
            if g("fgm") > g("fga") or g("fg3m") > g("fg3a") or g("ftm") > g("fta"):
                E(f"{who}: made > attempted")
            if g("fg3m") > g("fgm") or g("fg3a") > g("fga"):
                E(f"{who}: 3PT > FG")
            exp = 2 * (g("fgm") - g("fg3m")) + 3 * g("fg3m") + g("ftm")
            if exp != g("pts"):
                W(f"{who}: SOURCE inconsistency, pts {g('pts')} != 2FG+3FG+FT {exp}")
            if kind == "player":
                if g("oreb") + g("dreb") != g("reb"):
                    W(f"{who}: reb {g('reb')} != oreb+dreb")
                if g("games_started") > g("games"):
                    E(f"{who}: GS > GP")
                if g("games") > tgp.get((r["team_id"], r["division"]), 0):
                    E(f"{who}: GP {g('games')} > team GP")
                if g("games") == 0 and (g("min") or g("pts") or g("fga")):
                    E(f"{who}: GP 0 but has stats")
                if g("games") and g("min") == 0 and g("pts"):
                    W(f"{who}: points but no minutes recorded")
    for r in pc:   # recompute derived fields from totals
        who = f"{sport} pinecone {r['team_id']}/{r['Name']}"
        gp = r["GP"]
        if r["Reb"] != r["OReb"] + r["DReb"]:
            W(f"{who}: Reb != OReb+DReb")
        if r["2FGM"] != r["FGM"] - r["3PM"]:
            E(f"{who}: 2FGM wrong")
        for k in ("FG%", "3P%", "FT%", "2FG%", "AFG%"):
            if not 0 <= r[k] <= (150 if k == "AFG%" else 100):   # eFG% can reach 150
                E(f"{who}: {k}={r[k]} out of range")
        if gp and abs(r["PPG"] - r["Pts"] / gp) > 0.051:
            E(f"{who}: PPG {r['PPG']} vs {r['Pts']}/{gp}")
        if r["FGA"] and abs(r["FG%"] - 100 * r["FGM"] / r["FGA"]) > 0.051:
            E(f"{who}: FG% wrong")
        if r["Min"] == 0 and r["Per_40"] is not None:
            E(f"{who}: Per_40 should be null")
        if r["Min"] and r["Per_40"] is None:
            E(f"{who}: Per_40 missing")

    # 4 games -------------------------------------------------------------
    for t in ts:
        gl = [g for g in gm if g["team_id"] == t["team_id"] and g["division"] == t["division"]]
        if len(gl) != (t.get("games") or 0):
            W(f"{sport} {t['team_id']}: game log {len(gl)} rows vs GP {t.get('games')}")
        s = sum(g["score"] or 0 for g in gl)
        if gl and s != (t.get("pts") or 0):
            W(f"{sport} {t['team_id']}: sum of game scores {s} != team pts {t.get('pts')}")

    # 5 hub ---------------------------------------------------------------
    for div, path in sc.SPORTS[sport].items():
        hub, _ = sc.parse_hub(sc.get(f"{sc.BASE}/stats.aspx?path={path}&year=2026"), path, 2026)
        for slug, h in hub.items():
            t = next((x for x in ts if x["team_id"] == slug and x["division"] == div), None)
            if not t:
                if h.get("games"):
                    E(f"{sport} {div} {slug}: on hub with {h.get('games')} G but not in team stats")
                continue
            for hk, k in (("games", "games"), ("pts", "pts"), ("fgm", "fgm"), ("fg3m", "fg3m"),
                          ("ftm", "ftm"), ("reb", "reb"), ("ast", "ast"), ("stl", "stl"),
                          ("blk", "blk"), ("opp_pts", "opp_pts")):
                if h.get(hk) is not None and h[hk] != t.get(k):
                    E(f"{sport} {div} {slug}: hub {hk}={h[hk]} vs saved {t.get(k)}")

    # 6 live --------------------------------------------------------------
    if live:
        n = 0
        for t in ts:
            path = sc.SPORTS[sport][t["division"]]
            time.sleep(0.3)
            _, d = sc.fetch_team(path, 2026, t["team_id"])
            fresh = sc.mapstats(d.get("stats_stats") or {})
            drift = {k: (t.get(k), v) for k, v in fresh.items() if t.get(k) != v}
            if drift:
                n += 1
                W(f"{sport} {t['team_id']}: backend changed since scrape (games played since?): "
                  f"{dict(list(drift.items())[:3])}")
        print(f"  live re-fetch: {len(ts)} teams, {n} changed since scrape")

    # 7 info --------------------------------------------------------------
    gap = sum(1 for t in ts if sum(p.get("reb") or 0 for p in ps if p["team_id"] == t["team_id"]
                                    and p["division"] == t["division"]) != (t.get("reb") or 0))
    print(f"  info: {gap}/{len(ts)} teams where team reb != sum of players (team-rebound row)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="re-fetch backend JSON for every team")
    a = ap.parse_args()
    for sp in ("MBKB", "WBKB"):
        check_sport(sp, a.live)
    print(f"\nERRORS: {len(errors)}   WARNINGS: {len(warns)}")
    for m in errors:
        print("  ERROR", m)
    for m in warns[:60]:
        print("  warn ", m)
    if len(warns) > 60:
        print(f"  ... {len(warns) - 60} more warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
