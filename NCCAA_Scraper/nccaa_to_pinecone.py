#!/usr/bin/env python3
"""
nccaa_to_pinecone.py - convert the raw NCCAA scrape into the same record shape as
NAIA's Naia_Coach_Stats_<SPORT>_2026_2027.json, ready for the Pinecone upsert app.

One flat JSON list per sport AND division (4 files: MBB/WBB x DI/DII); record_type is
"player" or "team_total".
Pinecone identity key (upsert app default) = team_id + record_type + Name.
Extra field vs NAIA: "division" (DI / DII).

    python nccaa_to_pinecone.py                # MBKB + WBKB, 2026-27
    python nccaa_to_pinecone.py --sport MBKB
Output (default): ../26-27 College Data/NCCAA/Scraped Data/NCCAA_Stats_<MBB|WBB>_<DI|DII>_2026_2027.json
        and       ../26-27 College Data/NCCAA/Teams List/NCCAA_<Men|Women>_Teams_<DI|DII>_2026-27.json
"""
import argparse
import json
import os
from decimal import Decimal, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
NCCAA_ROOT = os.path.join(HERE, "..", "26-27 College Data", "NCCAA")
SHORT = {"MBKB": "MBB", "WBKB": "WBB"}


def r(x, nd=1):
    """Round half-up (matches NAIA's numbers, e.g. 0.25 -> 0.3)."""
    q = Decimal(1).scaleb(-nd)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


def div(a, b, nd=1):
    return r(a / b, nd) if b else 0.0


def per(total, minutes, base):
    return r(total / minutes * base, 1)


def build(src, rtype, name, gp, gs):
    g = lambda k: src.get(k) or 0
    mn, pts, fgm, fga, tpm, tpa = g("min"), g("pts"), g("fgm"), g("fga"), g("fg3m"), g("fg3a")
    ftm, fta, oreb, dreb, reb = g("ftm"), g("fta"), g("oreb"), g("dreb"), g("reb")
    ast, stl, blk, to, pf = g("ast"), g("stl"), g("blk"), g("to"), g("pf")
    rec = {
        "team_id": src["team_id"], "team_name": src["team_name"], "division": src["division"],
        "record_type": rtype, "Name": name, "GP": gp,
    }
    if rtype == "team_total":
        rec["TotalGamesChecked"] = gp
    rec.update({
        "GS": gs,
        "MPG": int(r(mn / gp, 0)) if (rtype == "team_total" and gp) else div(mn, gp),
        "PPG": div(pts, gp), "DEFR": div(dreb, gp), "OFFR": div(oreb, gp), "RPG": div(reb, gp),
        "APG": div(ast, gp), "SPG": div(stl, gp), "BPG": div(blk, gp), "TPG": div(to, gp),
        "PFPG": div(pf, gp),
        "Min": mn, "Pts": pts, "FGM": fgm, "FGA": fga, "FG%": div(fgm * 100, fga),
        "PPS": div(pts, fga), "AFG%": div((fgm + 0.5 * tpm) * 100, fga),
        "3PM": tpm, "3PA": tpa, "3P%": div(tpm * 100, tpa),
        "FTM": ftm, "FTA": fta, "FT%": div(ftm * 100, fta),
        "2FGM": fgm - tpm, "2FGA": fga - tpa, "2FG%": div((fgm - tpm) * 100, fga - tpa),
        "OReb": oreb, "DReb": dreb, "Reb": reb, "Ast": ast, "Stl": stl, "Blk": blk,
        "TO": to, "PF": pf, "DQ": g("dq"),
        "Ast:TO": div(ast, to, 1), "Stl:TO": div(stl, to, 2),
        "Stl:PF": div(stl, pf, 2), "Blk:PF": div(blk, pf, 2),
    })
    for base in (40, 32):   # null when no minutes were recorded (same as NAIA)
        rec[f"Per_{base}"] = ({"Pts": per(pts, mn, base), "Reb": per(reb, mn, base),
                               "Ast": per(ast, mn, base), "Stl": per(stl, mn, base),
                               "Blk": per(blk, mn, base)} if mn else None)
    return rec


def write_team_lists(sport, teams, players, tl_dir):
    """Team lists in the NAIA/NCAA share format: [{"team_name", "data_availablity"}],
    "partial" if the team has player stats with games played, else "none". One file per
    division (like NCAA): NCCAA_<Men|Women>_Teams_<DI|DII>_2026-27.json"""
    gender = "Men" if sport == "MBKB" else "Women"
    has = {(p["team_id"], p["division"]) for p in players if (p.get("games") or 0) > 0}
    os.makedirs(tl_dir, exist_ok=True)
    for div in ("DI", "DII"):
        lst = sorted(({"team_name": t["team_name"],
                       "data_availablity": "partial" if (t["team_id"], div) in has else "none"}
                      for t in teams if t["division"] == div), key=lambda x: x["team_name"])
        fp = os.path.join(tl_dir, f"NCCAA_{gender}_Teams_{div}_2026-27.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(lst, f, indent=4, ensure_ascii=False)
        print(f"{fp}: {len(lst)} teams ({sum(x['data_availablity'] == 'partial' for x in lst)} partial)")


def convert(sport, tag, data_dir, out_dir, tl_dir):
    ld = lambda k: json.load(open(os.path.join(data_dir, f"nccaa_{k}_{tag}.json"), encoding="utf-8"))
    teams, players = ld("team_stats"), ld("player_stats")
    # A school listed in both divisions (e.g. women's Randall: DI placeholder with 0 games,
    # DII with real games) would collide on team_id -> keep the listing with the most games.
    best = {}
    for t in teams:
        if t["team_id"] not in best or (t.get("games") or 0) > (best[t["team_id"]].get("games") or 0):
            best[t["team_id"]] = t
    for t in teams:
        if best[t["team_id"]] is not t:
            print(f"  dropped duplicate listing: {t['team_name']} ({t['division']}, G={t.get('games')})")
    teams = [t for t in teams if best[t["team_id"]] is t]
    out = []
    for t in teams:
        gp = t.get("games") or 0
        out.append(build(t, "team_total", "Season Totals", gp, gp))
        for p in (x for x in players if x["team_id"] == t["team_id"]
                  and x["division"] == t["division"]):
            out.append(build(p, "player", p["player_name"], p.get("games") or 0,
                             p.get("games_started") or 0))
    write_team_lists(sport, teams, players, tl_dir)
    os.makedirs(out_dir, exist_ok=True)
    keys = [(o["team_id"], o["record_type"], o["Name"]) for o in out]
    assert len(keys) == len(set(keys)), "duplicate Pinecone identity keys"
    for division in ("DI", "DII"):
        part = [o for o in out if o["division"] == division]
        fp = os.path.join(out_dir, f"NCCAA_Stats_{SHORT[sport]}_{division}_2026_2027.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(part, f, indent=2, ensure_ascii=False)
        print(f"{fp}: {len(part)} records "
              f"({sum(o['record_type'] == 'player' for o in part)} players, "
              f"{sum(o['record_type'] == 'team_total' for o in part)} teams)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="both", choices=["MBKB", "WBKB", "both"])
    ap.add_argument("--data-dir", default=os.path.join(HERE, "2026_27_data"))
    ap.add_argument("--out-dir", default=os.path.join(NCCAA_ROOT, "Scraped Data"))
    ap.add_argument("--teams-dir", default=os.path.join(NCCAA_ROOT, "Teams List"))
    a = ap.parse_args()
    for sp in (["MBKB", "WBKB"] if a.sport == "both" else [a.sport]):
        convert(sp, f"{sp.lower()}_26_27", a.data_dir, a.out_dir, a.teams_dir)


if __name__ == "__main__":
    main()
