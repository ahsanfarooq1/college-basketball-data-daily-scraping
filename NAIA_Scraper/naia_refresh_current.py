"""Refresh the CURRENT NAIA season (default 2026-27) for MBKB + WBKB.

1. Attach to the debug Chrome (port 9222, Cloudflare already solved) and load a
   stats page + a team page per sport, capturing the two league-wide S3 URLs
   (teamsData / playersData) the page itself requests.
2. Build Naia_Coach_Stats_{SPORT}_{SEASON}.json via naia_coach_build.py.
3. Write naia_{mens,womens}_teams_2026_27.json
   ([{"team_name","data_availablity": "partial"|"none"}], never "full").
4. Regenerate naia_mens_teams.json / naia_womens_teams.json (extract_naia_teams.py).

Run with the Python that has playwright (C:\\Python314\\python.exe).
"""
import collections
import json
import os
import subprocess
import sys
import socket
import time
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SEASON = sys.argv[1] if len(sys.argv) > 1 else "2026-27"
SUF = SEASON.replace("-", "_20") if len(SEASON) == 7 else SEASON.replace("-", "_")
BASE = "https://naiastats.prestosports.com/sports"
TEAM_SLUG = "northwestern"  # any team page; only used to expose playersData URL


URLS_FILE = os.path.join(HERE, "naia_s3_urls.json")   # the 4 public S3 data files (hash names never change between days)


def _load_urls():
    try:
        return json.load(open(URLS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_urls(d):
    json.dump(d, open(URLS_FILE, "w", encoding="utf-8"), indent=2)


def _port_open(port=9222):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _s3_check(url, label):
    """True if the public S3 file answers 200. Prints a [WARN] if the file looks stale (not rewritten for days)."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            lm = r.headers.get("Last-Modified")
        if lm:
            age = (datetime.now(timezone.utc) - parsedate_to_datetime(lm)).total_seconds() / 86400
            if age > 3:
                print(f"[WARN] NAIA S3 file {label} was last modified {age:.1f} days ago ({lm}) - the site may have stopped updating it")
        return True
    except Exception as e:
        print(f"[warn] S3 check failed for {label}: {type(e).__name__} {str(e)[:80]}")
        return False


LAST_SEEN = []    # every prestosports-downloads URL the last page requested (for the alert message)
CF_HITS = []      # evidence that Cloudflare (not a site redesign) is what stopped us
CF_WAIT_S = 300   # how long we wait for a human to solve the "verify you are human" box


def _is_cf_title(t):
    t = (t or "").lower()
    return "just a moment" in t or "attention required" in t or "verify you are human" in t


def capture(pg, url, want):
    seen = LAST_SEEN
    seen.clear()
    pg.on("request", lambda r: seen.append(r.url) if "prestosports-downloads" in r.url else None)
    try:
        resp = pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        if resp is not None and resp.status in (403, 429, 503):
            CF_HITS.append(f"HTTP {resp.status} on {url}")
    except Exception as e:
        msg = str(e)
        if "ERR_CONNECTION_CLOSED" in msg or "ERR_CONNECTION_RESET" in msg or "Timeout" in type(e).__name__:
            CF_HITS.append(f"{type(e).__name__}: {msg.splitlines()[0][:120]}")
        raise
    # Cloudflare interstitial: wait for a human to solve it in the Chrome window, then carry on
    waited = 0
    while _is_cf_title(pg.title()) and waited < CF_WAIT_S:
        if waited == 0:
            CF_HITS.append(f"Cloudflare 'Just a moment' page on {url}")
            print(f"  [CF] Cloudflare check on NAIA - SOLVE THE BOX in the debug Chrome window (waiting up to {CF_WAIT_S // 60} min)...")
        pg.wait_for_timeout(5000)
        waited += 5
    if waited and not _is_cf_title(pg.title()):
        print(f"  [CF] cleared after {waited}s")
    for _ in range(30):
        # PrestoSports renamed the files (teamsData -> teamsPageData/v1/<site>/, playersData -> playersPageData/v1/<site>/)
        names = {"teamsData": ("teamsData", "teamsPageData"), "playersData": ("playersData", "playersPageData")}[want]
        hit = [u for u in seen if any(f"/{n}/" in u for n in names)]
        if hit:
            return hit[-1]
        pg.wait_for_timeout(1000)
    return None


def browser_capture(sps):
    """Fallback: open the NAIA pages in the (Cloudflare-solved) debug Chrome and read the S3 URLs they request."""
    from playwright.sync_api import sync_playwright
    urls = {}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        c = b.contexts[0]
        for sp in sps:
            res = []
            for url, want in ((f"{BASE}/{sp}/{SEASON}/teams", "teamsData"),
                              (f"{BASE}/{sp}/{SEASON}/teams/{TEAM_SLUG}", "playersData")):
                got = None
                for attempt in range(3):   # the page occasionally doesn't request the file in time
                    pg = c.new_page()   # fresh tab each time: no stale requests, no clash with NWAC
                    try:
                        got = capture(pg, url, want)
                    except Exception as e:   # slow page / goto timeout -> just retry
                        print(f"  [warn] {want} {sp}: {type(e).__name__}")
                        got = None
                    finally:
                        pg.close()
                    if got:
                        break
                    print(f"  [retry {attempt + 1}/3] {want} not captured for {sp}")
                    time.sleep(5)
                res.append(got)
            t, pl = res
            if urls and t in [u[0] for u in urls.values()]:
                sys.exit(f"[ERR] {sp} teamsData URL is identical to the other sport's - stale capture, aborting")
            print(sp, "teams:", t, "\n    players:", pl)
            if not t or not pl:
                if CF_HITS:
                    sys.exit(
                        f"[CLOUDFLARE BLOCK] NAIA site refused us ({sp}) - this is NOT a site change. "
                        f"Evidence: {CF_HITS[-3:]}. "
                        f"Fix: in the debug Chrome window open https://naiastats.prestosports.com/sports/mbkb/2026-27/teams, "
                        f"solve the 'verify you are human' box once, then rerun: python run_all.py --only naia")
                sys.exit(
                    f"[STRUCTURE CHANGE?] NAIA site no longer exposes the data files we look for ({sp}). "
                    f"Expected a URL containing /teamsData/ or /teamsPageData/ and /playersData/ or /playersPageData/. "
                    f"Data URLs the page DID request: {sorted(set(LAST_SEEN))[:8] or 'none'}. "
                    f"Fix: update the names in naia_refresh_current.py capture().")
            urls[sp] = (t, pl)
            time.sleep(2)
    return urls


def main():
    urls = {}
    saved = _load_urls()
    need = []
    for sp in ("mbkb", "wbkb"):
        s_ = saved.get(SEASON, {}).get(sp)
        ok = bool(s_) and all(_s3_check(s_[k], f"{sp} {k}") for k in ("teams", "players"))
        if ok:
            urls[sp] = (s_["teams"], s_["players"])
            print(f"{sp}: direct S3 (no browser, no Cloudflare)\n    teams:   {s_['teams']}\n    players: {s_['players']}")
        else:
            need.append(sp)
    if need:
        if not _port_open():
            sys.exit(
                f"[STRUCTURE CHANGE?] NAIA's saved S3 data URLs no longer work for {need} and no debug Chrome (:9222) is "
                f"available to discover the new ones. PrestoSports probably renamed/moved the data files. "
                f"Fix: run NAIA once on a machine with the debug Chrome (python run_all.py --only naia) - it re-learns "
                f"the URLs and saves them to naia_s3_urls.json - then commit that file.")
        print(f"[info] direct S3 failed for {need} -> discovering the URLs through the browser")
        found = browser_capture(need)
        urls.update(found)
        for sp, (t, pl) in found.items():
            saved.setdefault(SEASON, {})[sp] = {"teams": t, "players": pl}
        _save_urls(saved)
        print("[info] new S3 URLs saved to naia_s3_urls.json")

    for sp, (t, pl) in urls.items():
        subprocess.run([sys.executable, os.path.join(HERE, "naia_coach_build.py"), "--sport", sp,
                        "--season", SEASON, "--teams-url", t, "--players-url", pl], check=True, cwd=HERE)

    for S, n in (("MBKB", "mens"), ("WBKB", "womens")):
        d = json.load(open(os.path.join(HERE, f"Naia_Coach_Stats_{S}_{SUF}.json"), encoding="utf-8"))
        out = []
        for r in sorted((r for r in d if r["record_type"] == "team_total"), key=lambda r: r["team_name"]):
            has = r["GP"] > 0 or r["Pts"] > 0
            out.append({"team_name": r["team_name"], "data_availablity": "partial" if has else "none"})
        f = os.path.join(HERE, f"naia_{n}_teams_{SEASON.replace('-', '_')}.json")
        json.dump(out, open(f, "w", encoding="utf-8"), indent=4)
        print(os.path.basename(f), len(out), dict(collections.Counter(o["data_availablity"] for o in out)))

    subprocess.run([sys.executable, os.path.join(HERE, "extract_naia_teams.py")], check=True, cwd=HERE)


if __name__ == "__main__":
    main()
