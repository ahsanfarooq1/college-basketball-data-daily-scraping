###################################################################
# NJCAA Coach-View de-duplicator                                  #
#                                                                 #
# Cleans the Njcaa_Coach_Stats_*.json files so there are NO        #
# duplicate players or teams. Two kinds of source-data duplicates  #
# exist in PrestoSports' published stats:                          #
#                                                                 #
# 1. Duplicate PLAYER rows - the same person on the same team is   #
#    listed more than once (same team_id + Name). Seen as either:  #
#      a) a real line + an all-zero "phantom" roster line, or      #
#      b) a roster entered TWICE with the season SPLIT between two  #
#         profiles (e.g. 11 games + 16 games = 27, the real total). #
#    FIX: merge each (team_id, Name) group into ONE record by       #
#    SUMMING the counting stats and recomputing every rate / % /    #
#    per-game / per-40 / per-32 field. (Zero rows add nothing;      #
#    split rows combine to the correct full-season line.) Rows that #
#    are byte-for-byte identical are collapsed to one, not summed.  #
#    Single (non-duplicated) records are left untouched.           #
#                                                                 #
# 2. Duplicate TEAM rows across divisions - one team_id whose       #
#    team_total appears in two division files (a phantom cross-     #
#    listing with 0 players in one of them). FIX: keep the copy in  #
#    the division where the team actually has a roster; drop the    #
#    0-player phantom.                                             #
#                                                                 #
# Originals are backed up to _pre_dedupe_backup/ before overwrite.  #
# Run:  python njcaa_dedupe.py            (cleans all 12 files)     #
#       python njcaa_dedupe.py --check    (report only, no writes)  #
###################################################################

import argparse
import json
import os
import shutil
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SPORTS = ["mbkb", "wbkb"]
DIVS = ["div1", "div2", "div3"]
SEASONS = ["2026-27"]

# counting (additive) stats that get summed when merging duplicate rows
COUNTING = ["GP", "GS", "Min", "Pts", "FGM", "FGA", "3PM", "3PA", "FTM", "FTA",
            "2FGM", "2FGA", "OReb", "DReb", "Reb", "Ast", "Stl", "Blk", "TO", "PF", "DQ"]


def _pct(m, a):
    return round(m / a * 100, 1) if a else 0.0


def _ratio(n, d):
    return round(n / d, 2) if d else 0.0


def _pg(v, gp):
    return round(v / gp, 1) if gp else 0.0


def recompute(rec):
    """Recompute all derived fields from the (already summed) counting stats,
    using the exact formulas from njcaa_coach_stats.make_record."""
    gp = rec["GP"]
    fgm, fga = rec["FGM"], rec["FGA"]
    tpm, tpa = rec["3PM"], rec["3PA"]
    ftm, fta = rec["FTM"], rec["FTA"]
    pts, mn = rec["Pts"], rec["Min"]
    reb, ast, stl, blk = rec["Reb"], rec["Ast"], rec["Stl"], rec["Blk"]
    to, pf = rec["TO"], rec["PF"]

    # keep 2FG derived consistent with the summed FG / 3P
    rec["2FGM"], rec["2FGA"] = fgm - tpm, fga - tpa
    if "TotalGamesChecked" in rec:      # mirrors GP
        rec["TotalGamesChecked"] = gp

    rec["MPG"] = _pg(mn, gp)
    rec["PPG"] = _pg(pts, gp)
    rec["DEFR"] = _pg(rec["DReb"], gp)
    rec["OFFR"] = _pg(rec["OReb"], gp)
    rec["RPG"] = _pg(reb, gp)
    rec["APG"] = _pg(ast, gp)
    rec["SPG"] = _pg(stl, gp)
    rec["BPG"] = _pg(blk, gp)
    rec["TPG"] = _pg(to, gp)
    rec["PFPG"] = _pg(pf, gp)
    rec["FG%"] = _pct(fgm, fga)
    rec["PPS"] = round(pts / fga, 1) if fga else 0.0
    rec["AFG%"] = round((fgm + 0.5 * tpm) / fga * 100, 1) if fga else 0.0
    rec["3P%"] = _pct(tpm, tpa)
    rec["FT%"] = _pct(ftm, fta)
    rec["2FG%"] = _pct(rec["2FGM"], rec["2FGA"])
    rec["Ast:TO"] = _ratio(ast, to)
    rec["Stl:TO"] = _ratio(stl, to)
    rec["Stl:PF"] = _ratio(stl, pf)
    rec["Blk:PF"] = _ratio(blk, pf)
    if mn > 0:
        rec["Per_40"] = {k: round(v / mn * 40, 1) for k, v in
                         (("Pts", pts), ("Reb", reb), ("Ast", ast), ("Stl", stl), ("Blk", blk))}
        rec["Per_32"] = {k: round(v / mn * 32, 1) for k, v in
                         (("Pts", pts), ("Reb", reb), ("Ast", ast), ("Stl", stl), ("Blk", blk))}
    else:
        rec["Per_40"] = rec["Per_32"] = None
    return rec


def _sig(rec):
    """Byte-ish signature of a record's counting stats (to detect exact dups)."""
    return tuple(rec.get(k, 0) for k in COUNTING)


def merge_players(recs, stats):
    """Return player records with each (team_id, Name) collapsed to one.
    team_total rows pass through unchanged and keep their original order."""
    out = []
    groups = defaultdict(list)
    order = []
    for r in recs:
        if r["record_type"] != "player":
            out.append(r)
            continue
        key = (r["team_id"], r["Name"])
        if key not in groups:
            order.append(key)
        groups[key].append(r)

    for key in order:
        rows = groups[key]
        if len(rows) == 1:
            out.append(rows[0])
            continue
        # de-duplicate exact copies first
        uniq = []
        seen = set()
        for r in rows:
            s = _sig(r)
            if s not in seen:
                seen.add(s)
                uniq.append(r)
        if len(uniq) == 1:
            out.append(uniq[0])
            stats["exact_dupes_dropped"] += len(rows) - 1
            continue
        nonzero = [r for r in uniq if r["GP"] > 0]
        if len(nonzero) <= 1:
            # one real line + phantom zero line(s): keep the real one as-is
            keep = nonzero[0] if nonzero else uniq[0]
            out.append(keep)
            stats["zero_rows_dropped"] += len(rows) - 1
        else:
            # split season across profiles: SUM + recompute
            base = dict(nonzero[0])
            for k in COUNTING:
                base[k] = sum(r.get(k, 0) for r in nonzero)
            recompute(base)
            out.append(base)
            stats["merged_groups"] += 1
            stats["merged_rows"] += len(rows)
            stats["merge_detail"].append(
                (rows[0]["team_name"], key[1], [r["GP"] for r in nonzero], base["GP"]))
    return out


def new_stats():
    return {"exact_dupes_dropped": 0, "zero_rows_dropped": 0,
            "merged_groups": 0, "merged_rows": 0, "merge_detail": [],
            "phantom_teams_dropped": 0, "phantom_detail": []}


def drop_phantom_teams(files, stats):
    """files: {(season, sport, div): (fn, recs)}. Drop cross-division phantom
    team_totals - a team_id whose team_total shows up in >1 division for a
    sport+season; keep the copy that has a roster, drop the 0-player phantom.
    Only compares within the (sport, season) groups actually present in `files`."""
    keys = list(files.keys())
    combos = {(season, sport) for (season, sport, _div) in keys}
    for season, sport in combos:
        present = {d: files.get((season, sport, d)) for d in DIVS}
        pcount = {}
        for d in DIVS:
            if not present[d]:
                continue
            c = defaultdict(int)
            for r in present[d][1]:
                if r["record_type"] == "player":
                    c[r["team_id"]] += 1
            pcount[d] = c
        tid_divs = defaultdict(list)
        tid_name = {}
        for d in DIVS:
            if not present[d]:
                continue
            for r in present[d][1]:
                if r["record_type"] == "team_total":
                    tid_divs[r["team_id"]].append(d)
                    tid_name[r["team_id"]] = r["team_name"]
        for tid, divs in tid_divs.items():
            if len(divs) < 2:
                continue
            keep_div = max(divs, key=lambda d: pcount.get(d, {}).get(tid, 0))
            for d in divs:
                if d == keep_div:
                    continue
                fn, recs = files[(season, sport, d)]
                new = [r for r in recs
                       if not (r["record_type"] == "team_total" and r["team_id"] == tid)]
                files[(season, sport, d)] = (fn, new)
                stats["phantom_teams_dropped"] += 1
                stats["phantom_detail"].append(
                    (season, sport, tid_name[tid], f"dropped from {d}, kept in {keep_div}"))
    return files


def dedupe_fileset(files, stats):
    """Run both passes over an in-memory {(season,sport,div):(fn,recs)} mapping."""
    for key, (fn, recs) in list(files.items()):
        files[key] = (fn, merge_players(recs, stats))
    return drop_phantom_teams(files, stats)


def clean_all(check=False):
    stats = new_stats()

    # load every file
    files = {}
    for season in SEASONS:
        for sport in SPORTS:
            for div in DIVS:
                fn = f"Njcaa_Coach_Stats_{sport}_{div}_{season}.json"
                fp = os.path.join(HERE, fn)
                if os.path.exists(fp):
                    files[(season, sport, div)] = (fn, json.load(open(fp, encoding="utf-8")))

    dedupe_fileset(files, stats)

    # ---- report ----
    print("========== DE-DUP REPORT ==========")
    print(f"  phantom zero player-rows dropped : {stats['zero_rows_dropped']}")
    print(f"  exact-duplicate rows dropped     : {stats['exact_dupes_dropped']}")
    print(f"  split-profile groups merged      : {stats['merged_groups']} "
          f"(from {stats['merged_rows']} rows)")
    print(f"  cross-division phantom teams     : {stats['phantom_teams_dropped']}")
    if stats["merge_detail"]:
        print("\n  merged (summed) players:")
        for tn, nm, gps, tot in stats["merge_detail"]:
            print(f"    {tn} | {nm}: {gps} -> GP {tot}")
    if stats["phantom_detail"]:
        print("\n  cross-division team fixes:")
        for season, sport, nm, msg in stats["phantom_detail"]:
            print(f"    {season} {sport} | {nm}: {msg}")

    if check:
        print("\n[CHECK] no files written (--check).")
        return

    # ---- write (with backup) ----
    backup = os.path.join(HERE, "_pre_dedupe_backup")
    os.makedirs(backup, exist_ok=True)
    for key, (fn, recs) in files.items():
        src = os.path.join(HERE, fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup, fn))
        json.dump(recs, open(src, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\n[WROTE] {len(files)} cleaned files (originals backed up in {backup}).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="De-duplicate NJCAA coach-view stat files")
    ap.add_argument("--check", action="store_true", help="Report duplicates without writing")
    args = ap.parse_args()
    clean_all(check=args.check)
