"""After scraping+accumulating 2026-27: rebuild all NCAA team lists.
  - Share_2026-27/NCAA_<Men|Women>_Teams_D<n>_2026-27.json   (partial/none)  (+ copy in ../Updated_NCAA_Scraper/Share_2026-27)
  - 2024_2025_2026_data/Ncaa_<men|Women>_Teams_d<n>.json     (multi-season)
Usage: python ncaa_refresh_lists.py [season_year=2026]"""
import os, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "pipeline")]
from ncaa_paths import data_dir
from share_lists import build_all, build_history

year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
ddir = data_dir(year)
label = f"{year}-{(year + 1) % 100:02d}"
share = os.path.join(HERE, f"Share_{label}")
os.makedirs(share, exist_ok=True)
combos = [(s, d) for s in ("MBB", "WBB") for d in (1, 2, 3)]
info = build_all(year, ddir, share, combos)
if not info:
    sys.exit("[ERR] share lists not built")

build_history(year, ddir, share)          # writes into ddir (+ copies into share, removed below)
for f in os.listdir(share):               # multi-season files belong in the data folder only
    if f.startswith("Ncaa_"):
        os.remove(os.path.join(share, f))
mirror = os.path.join(os.path.dirname(HERE), "Updated_NCAA_Scraper", f"Share_{label}")
os.makedirs(mirror, exist_ok=True)
for f in os.listdir(share):
    shutil.copy2(os.path.join(share, f), os.path.join(mirror, f))
# keep the legacy folder's multi-season lists (the documented location) in sync too
from ncaa_paths import LEGACY_DIR
for f in os.listdir(ddir):
    if f.startswith("Ncaa_") and f.endswith(".json") and "_Teams_d" in f and ddir != LEGACY_DIR:
        shutil.copy2(os.path.join(ddir, f), os.path.join(LEGACY_DIR, f))
print(f"[OK] team lists updated -> {share} (+ mirror {mirror})")
