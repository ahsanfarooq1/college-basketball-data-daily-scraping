"""Build the shareable NJCAA team list for a season (run AFTER njcaa_ardor_stats.py).
Teams = every official school in the njcaa.org (Ardor) registry (schools with no
division go to d1). data_availablity: "partial" if the team appears in the
season's stats files, else "none" ("full" is never used while the season runs).
Output: 2026_2027_data/Njcaa_{men|Women}_Teams_{d}_{season}.json  (flat list)."""
import json, os, sys
from njcaa_ardor_stats import get_json, division_of, API, TENANT_ID, DATA_DIR

SEASON = sys.argv[1] if len(sys.argv) > 1 else "2026-27"
KEY = SEASON[:4] + "-20" + SEASON[-2:]
os.makedirs(DATA_DIR, exist_ok=True)
for sport, label in (("mbkb", "men"), ("wbkb", "Women")):
    j = get_json(f"{API}/tenant/stats?tenant_id={TENANT_ID}&season={SEASON}&sport={sport}"
                 f"&include_players=false&include_games=false")
    for d in ("d1", "d2", "d3"):
        names = sorted({s["school"] for s in j["schools"]
                        if division_of(s) == d})
        sf = os.path.join(DATA_DIR, f"Njcaa_Coach_Stats_{sport}_div{d[1]}_{SEASON}.json")
        have = {r["team_name"] for r in json.load(open(sf, encoding="utf-8"))
                if r["GP"] > 0 or r["Pts"] > 0 or r["FGA"] > 0}   # roster-only zero rows do not count as data
        out = [{"team_name": n, "data_availablity": "partial" if n in have else "none"} for n in names]
        fn = os.path.join(DATA_DIR, f"Njcaa_{label}_Teams_{d}_{KEY}.json")
        json.dump(out, open(fn, "w", encoding="utf-8"), indent=4, ensure_ascii=False)
        print(f"{label} {d}: {len(out)} teams, partial {sum(o['data_availablity']=='partial' for o in out)}, "
              f"none {sum(o['data_availablity']=='none' for o in out)} -> {os.path.basename(fn)}")
