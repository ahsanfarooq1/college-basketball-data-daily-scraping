"""Quality check of '26-27 College Data' (all 4 colleges). Usage: python validate_all.py"""
import collections, glob, json, os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(ROOT, "26-27 College Data")
errs, warns = [], []
def E(m): errs.append(m)
def W(m): warns.append(m)
def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception as e: E(f"{os.path.basename(p)}: unreadable JSON ({e})"); return None

NUM_SKIP = {"team_id", "team_name", "record_type", "Name", "player_id", "Player", "Pos", "Class", "Yr", "No", "Jersey"}
all_list_names = {}
for col in ("NAIA", "NCAA", "NJCAA", "NWAC", "NCCAA"):
    sd, tl = glob.glob(os.path.join(D, col, "Scraped Data", "*.json")), glob.glob(os.path.join(D, col, "Teams List", "*.json"))
    exp = {"NAIA": (2, 2), "NCAA": (6, 6), "NJCAA": (6, 6), "NWAC": (2, 2), "NCCAA": (4, 4)}[col]
    if (len(sd), len(tl)) != exp: E(f"{col}: expected {exp} files (data,lists) got {(len(sd), len(tl))}")
    stat_teams = {}
    for p in sd:
        n = os.path.basename(p); d = load(p)
        if d is None: continue
        if not isinstance(d, list) or not d: E(f"{n}: empty / not a list"); continue
        tt = {}; seen = collections.Counter(); neg = 0; miss = 0; nonnum = 0
        for r in d:
            if not {"team_id", "team_name", "record_type", "GP"} <= set(r): miss += 1; continue
            seen[(r["team_id"], r["record_type"], r.get("Name"), r.get("player_id"), r.get("Jersey"))] += 1
            for k, v in r.items():
                if k in NUM_SKIP or v is None: continue
                if isinstance(v, (int, float)) and v < 0: neg += 1
            if r["record_type"] == "team_total": tt[r["team_id"]] = r
        if miss: E(f"{n}: {miss} records missing required keys")
        if neg: E(f"{n}: {neg} negative numeric values")
        dups = sum(c - 1 for c in seen.values() if c > 1)
        if dups: E(f"{n}: {dups} duplicate records")
        # player GP vs team GP, players without a team_total
        bad = noteam = 0; ex = []
        for r in d:
            if r.get("record_type") != "player": continue
            t = tt.get(r["team_id"])
            if t is None: noteam += 1; continue
            if (r.get("GP") or 0) > (t.get("GP") or 0):
                bad += 1; ex.append(f"{r['team_name']}/{r.get('Name')} {r['GP']}>{t['GP']}")
        if noteam: E(f"{n}: {noteam} player rows with no team_total")
        if bad: W(f"{n}: {bad} players with more GP than their team (e.g. {', '.join(ex[:3])})")
        none_ppg = [t["team_name"] for t in tt.values() if (t.get("GP") or 0) > 0 and not t.get("Pts")]
        if none_ppg: W(f"{n}: {len(none_ppg)} teams have GP but 0 points (e.g. {', '.join(none_ppg[:3])})")
        for t in tt.values(): stat_teams[t["team_name"]] = max(stat_teams.get(t["team_name"], 0), t.get("GP") or 0)
    for p in tl:
        n = os.path.basename(p); d = load(p)
        if d is None: continue
        if not isinstance(d, list) or not d: E(f"{n}: empty / not a list"); continue
        names = [t.get("team_name") for t in d]
        if any(not x for x in names): E(f"{n}: team with no name")
        c = [k for k, v in collections.Counter(names).items() if v > 1]
        if c: E(f"{n}: duplicate teams {c[:3]}")
        badf = [t for t in d if set(t) != {"team_name", "data_availablity"} and not {"team_name", "data_availablity"} <= set(t)]
        if badf: E(f"{n}: {len(badf)} entries with wrong fields")
        vals = {t.get("data_availablity") for t in d}
        if not vals <= {"partial", "none"}: E(f"{n}: bad data_availablity values {vals - {'partial', 'none'}}")
        for x in names: all_list_names.setdefault((col, "men" if "men" in n.lower() and "women" not in n.lower() else "women", x), []).append(n)
        # partial teams must have stats
        nostat = [t["team_name"] for t in d if t["data_availablity"] == "partial" and stat_teams.get(t["team_name"], 0) == 0]
        if nostat and col != "NCAA": W(f"{n}: {len(nostat)} 'partial' teams not found with games in the stats files (e.g. {', '.join(nostat[:3])})")
        if nostat and col == "NCAA": W(f"{n}: {len(nostat)} 'partial' teams not matched by name to stats (e.g. {', '.join(nostat[:3])}) [NCAA names differ short/full]")
# a team in two division lists of the same college/sport
for (col, sp, name), files in all_list_names.items():
    if len(files) > 1 and col in ("NCAA", "NJCAA", "NCCAA"): E(f"{col} {sp}: '{name}' is in several division lists: {files}")
print("ERRORS:", len(errs)); [print("  X", e) for e in errs]
print("WARNINGS:", len(warns)); [print("  !", w) for w in warns]
print("RESULT:", "FAIL" if errs else "PASS (no blocking errors)")
sys.exit(1 if errs else 0)
