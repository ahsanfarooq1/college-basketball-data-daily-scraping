#!/usr/bin/env python3
"""
run_all.py - ONE command: run the 5 college scrapers (2026-27) in parallel,
refresh their data files + team lists, and print an old-vs-new report.

    NAIA   -> NAIA_Scraper            (official coach-view stats; needs debug Chrome :9222)
    NCAA   -> NCAA_Scraper            (JSON API: discover + boxscores + accumulate + team lists)
    NWAC   -> NWAC_Scraper            (official coach-view stats; needs debug Chrome :9222)
    NJCAA  -> Updated_NJCAA_Scraper   (njcaa.org API: stats + team lists)
    NCCAA  -> NCCAA_Scraper           (thenccaa.org, plain requests: stats + team lists)

Usage (from this folder):
    python run_all.py                    # run all 5 in parallel, then report
    python run_all.py --only njcaa,naia  # just some of them
    python run_all.py --skip ncaa
    python run_all.py --no-chrome        # don't auto-start the debug Chrome
    python run_all.py --report-only      # no scraping: compare the latest snapshot vs current files

Every run keeps:  _daily_runs/<timestamp>/before/  (copy of the files BEFORE the run)
                  _daily_runs/<timestamp>/logs/    (one log per scraper)
                  _daily_runs/<timestamp>/report.txt  (+ report.json)
"""
import argparse
import datetime as dt
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = r"D:\Apps\Python\python.exe"          # has curl_cffi + playwright + bs4
if not os.path.exists(PY):
    PY = sys.executable
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
CHROME_PROFILE = r"D:\chrome-debug-profile"
RUNS_DIR = os.path.join(ROOT, "_daily_runs")

NAIA_D = os.path.join(ROOT, "NAIA_Scraper")
NCAA_D = os.path.join(ROOT, "NCAA_Scraper")
NWAC_D = os.path.join(ROOT, "NWAC_Scraper")
NJCAA_D = os.path.join(ROOT, "Updated_NJCAA_Scraper")
NCCAA_D = os.path.join(ROOT, "NCCAA_Scraper")
NCAA_DATA = os.path.join(NCAA_D, "2026_27_data")
NJCAA_DATA = os.path.join(NJCAA_D, "2026_2027_data")
NCAA_SHARE = os.path.join(NCAA_D, "Share_2026-27")


# ------------------------------------------------------------------ what each scraper writes
def _g(pattern):
    return sorted(glob.glob(pattern))


def outputs(name):
    """(stats_files, list_files) for a scraper, resolved at call time."""
    if name == "naia":
        return ([os.path.join(NAIA_D, f"Naia_Coach_Stats_{s}_2026_2027.json") for s in ("MBKB", "WBKB")],
                [os.path.join(NAIA_D, f) for f in ("naia_mens_teams_2026_27.json", "naia_womens_teams_2026_27.json")])
    if name == "nwac":
        return ([os.path.join(NWAC_D, f"nwac_team_stats_{s}_26_27.json") for s in ("mbkb", "wbkb")],
                [os.path.join(NWAC_D, f"nwac_team_availability_{s}_26_27.json") for s in ("mbkb", "wbkb")])
    if name == "ncaa":
        return ([os.path.join(NCAA_DATA, f"NCAA_Accumulated_Stats_{s}_D{d}_2026_2027.json")
                 for s in ("MBB", "WBB") for d in (1, 2, 3)],
                _g(os.path.join(NCAA_SHARE, "NCAA_*_Teams_D*_2026-27.json")))
    if name == "njcaa":
        return (_g(os.path.join(NJCAA_DATA, "Njcaa_Coach_Stats_*_2026-27.json")),
                _g(os.path.join(NJCAA_DATA, "Njcaa_*_Teams_d*_2026-2027.json")))
    if name == "nccaa":
        # nccaa_to_pinecone.py writes straight into 26-27 College Data/NCCAA (4 data + 4 list files)
        nc = os.path.join(ROOT, "26-27 College Data", "NCCAA")
        return ([os.path.join(nc, "Scraped Data", f"NCCAA_Stats_{s}_{d}_2026_2027.json") for s in ("MBB", "WBB") for d in ("DI", "DII")],
                [os.path.join(nc, "Teams List", f"NCCAA_{g}_Teams_{d}_2026-27.json") for g in ("Men", "Women") for d in ("DI", "DII")])
    raise KeyError(name)


# ------------------------------------------------------------------ job definitions
# job = list of stages; stage = list of sequences run IN PARALLEL; sequence = list of commands run in order.
def ps1(script, *args):
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, *map(str, args)]


def job_steps(name):
    if name == "njcaa":
        return [(NJCAA_D, [[[PY, "njcaa_ardor_stats.py"], [PY, "njcaa_team_lists.py"]]])]
    if name == "nccaa":
        return [(NCCAA_D, [[[PY, "nccaa_scraper.py", "--sport", "both"], [PY, "nccaa_to_pinecone.py"]]])]
    if name == "naia":
        return [(NAIA_D, [[[PY, "naia_refresh_current.py", "2026-27"]]])]
    if name == "nwac":
        seq = []
        for sp in ("mbkb", "wbkb"):
            seq.append([PY, "nwac_team_scraper.py", "--sport", sp, "--season", "2026-27",
                        "--output", f"nwac_teams_{sp}_26_27.json", "--attach", "--cdp-url", CDP_URL])
            seq.append([PY, "nwac_team_stats.py", "--sport", sp, "--season", "2026-27",
                        "--attach", "--cdp-url", CDP_URL, "--delay", "2.5"])
        seq.append([PY, os.path.join(ROOT, "run_all.py"), "--_nwac-availability"])
        return [(NWAC_D, [[seq[i] for i in range(len(seq))]])]
    if name == "ncaa":
        run = os.path.join(NCAA_D, "run_ncaa.ps1")
        if os.name == "nt":
            lanes = [[ps1(run, "-Sport", sp, "-Division", d, "-SeasonYear", 2026) for d in (1, 2, 3)]
                     for sp in ("MBB", "WBB")]
        else:   # Linux / GitHub Actions: the same two steps run_ncaa.ps1 does (discover contests -> scrape boxscores)
            def lane(sp, d):
                season = os.path.join("2026_27_data", f"season_{sp}_d{d}_2026-2027.json")
                final = os.path.join("2026_27_data", f"Final_All_Games_Data_{sp}_D{d}_2026_2027.json")
                return [[PY, "fetch_season_contests.py", sp, str(d), "2026", "--cf"],
                        [PY, "ncaa_game_scraper2.py", season, final, "--cf", "--delay", "1.0"]]
            lanes = [[c for d in (1, 2, 3) for c in lane(sp, d)] for sp in ("MBB", "WBB")]
        return [(NCAA_D, lanes),
                (NCAA_D, [[[PY, "ncaa_accumulation.py", "2026_2027"]]]),
                (NCAA_D, [[[PY, "ncaa_refresh_lists.py", "2026"]]])]
    raise KeyError(name)


# ------------------------------------------------------------------ small helpers
def port_open(port=CDP_PORT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def ensure_chrome():
    if port_open():
        print(f"[chrome] debug Chrome already on :{CDP_PORT} - reusing it")
        return True
    exe = next((p for p in (os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"))
                if os.path.exists(p)), None)
    if not exe:
        print("[chrome] chrome.exe not found")
        return False
    for f in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(CHROME_PROFILE, f))
        except OSError:
            pass
    os.makedirs(CHROME_PROFILE, exist_ok=True)
    print("[chrome] starting shared debug Chrome (profile D:\\chrome-debug-profile) ...")
    subprocess.Popen([exe, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={CHROME_PROFILE}",
                      "--new-window", "https://nwac.prestosports.com/sports/mbkb/2026-27/schedule"],
                     creationflags=0x00000008 | 0x00000200, close_fds=True)   # DETACHED | NEW_PROCESS_GROUP
    for _ in range(40):
        if port_open():
            break
        time.sleep(0.5)
    else:
        return False
    time.sleep(15)
    print("[chrome] >>> if a 'verify you are human' box shows in Chrome, solve it once. <<<")
    return True


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ------------------------------------------------------------------ running
class Job(threading.Thread):
    def __init__(self, name, logdir):
        super().__init__(daemon=True)
        self.name, self.logpath = name, os.path.join(logdir, f"{name}.log")
        self.status, self.t0, self.t1, self.err = "RUNNING", time.time(), None, ""
        self.last = ""

    def _run_seq(self, seq, cwd, log):
        for cmd in seq:
            log.write(f"\n$ {' '.join(cmd)}\n"); log.flush()
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace")
            for line in p.stdout:
                log.write(line); log.flush()
                if line.strip():
                    self.last = line.strip()[:110]
            if p.wait() != 0:
                return f"exit {p.returncode}: {' '.join(cmd[-4:])}"
        return ""

    def run(self):
        try:
            with open(self.logpath, "w", encoding="utf-8") as log:
                for cwd, seqs in job_steps(self.name):
                    errs = [None] * len(seqs)

                    def go(i, s):
                        errs[i] = self._run_seq(s, cwd, log)
                    ts = [threading.Thread(target=go, args=(i, s)) for i, s in enumerate(seqs)]
                    [t.start() for t in ts]
                    [t.join() for t in ts]
                    bad = [e for e in errs if e]
                    if bad:
                        self.err = "; ".join(bad)
                        self.status = "FAILED"
                        break
                else:
                    self.status = "OK"
        except Exception as e:
            self.status, self.err = "FAILED", repr(e)
        self.t1 = time.time()


# ------------------------------------------------------------------ comparison
def stats_summary(path):
    d = load(path)
    if not isinstance(d, list):
        return None
    teams, players = {}, 0
    for r in d:
        if r.get("record_type") == "team_total":
            teams[r["team_name"]] = (r.get("GP") or 0, r.get("PPG") or 0)
        elif r.get("record_type") == "player" and (r.get("GP") or 0) > 0:
            players += 1
    return {"teams": teams, "players": players}


def list_summary(path):
    d = load(path)
    if not isinstance(d, list):
        return None
    return {t["team_name"]: t.get("data_availablity", "?") for t in d if isinstance(t, dict) and "team_name" in t}


def compare_stats(label, old_p, new_p, out):
    o, n = stats_summary(old_p) if old_p and os.path.exists(old_p) else None, stats_summary(new_p)
    if n is None:
        out.append(f"  {label}: (no output file)")
        return
    nt = {k: v for k, v in n["teams"].items() if v[0] > 0}
    if o is None:
        out.append(f"  {label}: NEW file - {len(nt)} teams with games, {n['players']} players with games")
        return
    ot = {k: v for k, v in o["teams"].items() if v[0] > 0}
    gp_o, gp_n = sum(v[0] for v in ot.values()), sum(v[0] for v in nt.values())
    new_teams = sorted(set(nt) - set(ot))
    lost = sorted(set(ot) - set(nt))
    changed = [(k, ot[k][0], nt[k][0]) for k in nt if k in ot and nt[k][0] != ot[k][0]]
    out.append(f"  {label}")
    out.append(f"      teams with games : {len(ot)} -> {len(nt)}  ({len(nt) - len(ot):+d})")
    out.append(f"      team-games (GP)  : {gp_o} -> {gp_n}  ({gp_n - gp_o:+d})")
    out.append(f"      players w/ games : {o['players']} -> {n['players']}  ({n['players'] - o['players']:+d})")
    out.append(f"      teams whose GP changed: {len(changed)}")
    if new_teams:
        out.append(f"      NEW teams ({len(new_teams)}): " + ", ".join(new_teams[:12]) + (" ..." if len(new_teams) > 12 else ""))
    if lost:
        out.append(f"      !! teams that LOST data ({len(lost)}): " + ", ".join(lost[:12]))
    drops = [c for c in changed if c[2] < c[1]]
    if drops:
        out.append(f"      !! GP went DOWN for {len(drops)} team(s): " + ", ".join(f"{k} {a}->{b}" for k, a, b in drops[:6]))
    if gp_n < gp_o:
        out.append("      !! total GP decreased - check this output before using it")


def compare_lists(label, old_p, new_p, out):
    o = list_summary(old_p) if old_p and os.path.exists(old_p) else None
    n = list_summary(new_p)
    if n is None:
        out.append(f"  {label}: (no list file)")
        return
    cnt = lambda d: (len(d), sum(v == "partial" for v in d.values()), sum(v == "none" for v in d.values()))
    if o is None:
        t, p, z = cnt(n)
        out.append(f"  {label}: NEW list - {t} teams (partial {p}, none {z})")
        return
    (t0, p0, z0), (t1, p1, z1) = cnt(o), cnt(n)
    add, rem = sorted(set(n) - set(o)), sorted(set(o) - set(n))
    flipped = sorted(k for k in n if k in o and o[k] != n[k])
    line = f"  {label}: {t0} -> {t1} teams | partial {p0}->{p1} | none {z0}->{z1}"
    if add:
        line += f" | +{len(add)} added"
    if rem:
        line += f" | -{len(rem)} removed"
    n2p = sorted(k for k in flipped if o[k] == "none" and n[k] == "partial")
    p2n = sorted(k for k in flipped if o[k] == "partial" and n[k] == "none")
    if flipped:
        line += f" | {len(flipped)} status changed"
    out.append(line)
    # always show the none<->partial movement (even when 0) so it is never missed
    out.append(f"        none -> partial: {len(n2p)} team(s)" + (": " + ", ".join(n2p[:15]) + (" ..." if len(n2p) > 15 else "") if n2p else ""))
    if p2n:
        out.append(f"        !! partial -> none: {len(p2n)} team(s): " + ", ".join(p2n[:15]) + (" ..." if len(p2n) > 15 else ""))
    if add:
        out.append("        added: " + ", ".join(add[:8]) + (" ..." if len(add) > 8 else ""))
    if rem:
        out.append("        removed: " + ", ".join(rem[:8]) + (" ..." if len(rem) > 8 else ""))


def snapshot_path(before_dir, name, path):
    return os.path.join(before_dir, name, os.path.basename(path))


def take_snapshot(names, before_dir):
    for n in names:
        s, l = outputs(n)
        os.makedirs(os.path.join(before_dir, n), exist_ok=True)
        for p in s + l:
            if os.path.exists(p):
                shutil.copy2(p, snapshot_path(before_dir, n, p))


def build_report(names, before_dir, jobs, run_dir):
    out = [f"DAILY SCRAPE REPORT  {dt.datetime.now():%Y-%m-%d %H:%M}   (season 2026-27)", "=" * 70]
    summary = {}
    for n in names:
        j = jobs.get(n)
        head = f"\n[{n.upper()}]"
        if j:
            head += f"  {j.status}  ({(j.t1 - j.t0) / 60:.1f} min)"
            if j.err:
                head += f"\n  !! {j.err}   (see logs/{n}.log)"
        out.append(head)
        s, l = outputs(n)
        for p in s:
            compare_stats(os.path.basename(p), snapshot_path(before_dir, n, p), p, out)
        for p in l:
            compare_lists(os.path.basename(p), snapshot_path(before_dir, n, p), p, out)
        summary[n] = {"status": j.status if j else "report-only"}
    txt = "\n".join(out)
    with open(os.path.join(run_dir, "report.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    with open(os.path.join(run_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return txt


# ------------------------------------------------------------------ exact-duplicate cleanup
def dedupe_exact(names):
    """Drop records that are 100% identical to an earlier record in the same stats file."""
    for n in names:
        for p in outputs(n)[0]:
            d = load(p)
            if not isinstance(d, list):
                continue
            seen, out = set(), []
            for r in d:
                k = json.dumps(r, sort_keys=True, ensure_ascii=False)
                if k not in seen:
                    seen.add(k)
                    out.append(r)
            if len(out) != len(d):
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(out, f, indent=2, ensure_ascii=False)
                print(f"[dedupe] {os.path.basename(p)}: removed {len(d) - len(out)} identical duplicate record(s)")


# ------------------------------------------------------------------ tidy 26-27 folder
TIDY_DIR = os.path.join(ROOT, "26-27 College Data")


def sync_tidy(names, ok_only=None):
    """Copy the fresh 2026-27 files into  26-27 College Data/<COLLEGE>/{Scraped Data, Teams List}/ ."""
    for n in names:
        if ok_only is not None and n not in ok_only:
            print(f"[tidy] {n.upper()}: scraper did not finish OK - folder left as it was")
            continue
        stats, lists = outputs(n)
        for sub, files in (("Scraped Data", stats), ("Teams List", lists)):
            d = os.path.join(TIDY_DIR, n.upper(), sub)
            os.makedirs(d, exist_ok=True)
            for f in files:
                dst = os.path.join(d, os.path.basename(f))
                if os.path.exists(f) and os.path.abspath(f) != os.path.abspath(dst):
                    shutil.copy2(f, dst)
        print(f"[tidy] {n.upper()}: {len(stats)} data + {len(lists)} team-list files -> {os.path.join(TIDY_DIR, n.upper())}")


# ------------------------------------------------------------------ NWAC availability list
def nwac_availability():
    """nwac_team_availability_{sport}_26_27.json from the fresh stats: partial = team has games."""
    for sp in ("mbkb", "wbkb"):
        teams = load(os.path.join(NWAC_D, f"nwac_teams_{sp}_26_27.json")) or []
        stats = load(os.path.join(NWAC_D, f"nwac_team_stats_{sp}_26_27.json")) or []
        have = {r["team_name"] for r in stats if r.get("record_type") == "team_total" and (r.get("GP") or 0) > 0}
        names = sorted({t["team_name"] for t in teams} | {r["team_name"] for r in stats if r.get("record_type") == "team_total"})
        out = [{"team_name": n, "data_availablity": "partial" if n in have else "none"} for n in names]
        with open(os.path.join(NWAC_D, f"nwac_team_availability_{sp}_26_27.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"nwac availability {sp}: {len(out)} teams, partial {len(have)}")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Run all 5 college scrapers in parallel + old-vs-new report")
    ap.add_argument("--only", help="comma list: naia,ncaa,nwac,njcaa,nccaa")
    ap.add_argument("--skip", help="comma list to skip")
    ap.add_argument("--no-chrome", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--sync-only", action="store_true", help="just copy current files into '26-27 College Data'")
    ap.add_argument("--_nwac-availability", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a._nwac_availability:
        return nwac_availability()

    default = ["njcaa", "naia", "nwac", "ncaa", "nccaa"]
    if os.environ.get("GITHUB_ACTIONS"):
        default.remove("nwac")      # NWAC sits behind a Cloudflare Turnstile that needs a real, human-verified browser
    names = [n.strip() for n in a.only.split(",")] if a.only else default
    names = [n for n in names if n not in (a.skip or "").split(",")]
    bad = [n for n in names if n not in ("naia", "ncaa", "nwac", "njcaa", "nccaa")]
    if bad:
        sys.exit(f"unknown scraper(s): {bad}")

    if a.sync_only:
        dedupe_exact(names)
        return sync_tidy(names)

    if a.report_only:
        runs = sorted(glob.glob(os.path.join(RUNS_DIR, "*", "before")))
        if not runs:
            sys.exit("no previous snapshot in _daily_runs/")
        run_dir = os.path.dirname(runs[-1])
        print(build_report(names, runs[-1], {}, run_dir))
        return

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = os.path.join(RUNS_DIR, stamp)
    before, logs = os.path.join(run_dir, "before"), os.path.join(run_dir, "logs")
    os.makedirs(logs, exist_ok=True)
    print(f"[run] {stamp}  scrapers: {', '.join(names)}")
    take_snapshot(names, before)
    print("[run] snapshot of current files saved (for old-vs-new comparison)")

    if any(n in names for n in ("naia", "nwac")) and not a.no_chrome:
        if not ensure_chrome():
            print("[chrome] could not start debug Chrome - NAIA/NWAC will probably fail")
    elif any(n in names for n in ("naia", "nwac")) and not port_open():
        print(f"[chrome] WARNING: nothing on :{CDP_PORT}; NAIA/NWAC need the debug Chrome")

    jobs = {n: Job(n, logs) for n in names}
    for j in jobs.values():
        j.start()
    print(f"[run] all {len(jobs)} scrapers started in parallel (logs: {logs})")
    reported = {}
    while any(j.is_alive() for j in jobs.values()):
        time.sleep(30)
        for n, j in jobs.items():
            if j.is_alive():
                print(f"  .. {n:<5} {j.last}")
            elif n not in reported:
                reported[n] = 1
                print(f"  == {n:<5} {j.status} in {(j.t1 - j.t0) / 60:.1f} min")
    dedupe_exact([n for n, j in jobs.items() if j.status == "OK"])
    import health_check as hc
    alerts = hc.health_alerts(names, before, jobs, outputs, snapshot_path, stats_summary, load)
    atxt = hc.write_alerts(alerts, names, run_dir, ROOT)
    report = build_report(names, before, jobs, run_dir)
    with open(os.path.join(run_dir, "report.txt"), "w", encoding="utf-8") as f:
        f.write(atxt + "\n\n" + report + "\n")      # alerts first, so they cannot be missed
    print("\n" + atxt + "\n\n" + report)
    critical = {a[1] for a in alerts if a[0] == "CRITICAL"}
    sync_tidy(names, ok_only={n for n, j in jobs.items() if j.status == "OK" and n not in critical})
    print(f"\n[run] report saved: {os.path.join(run_dir, 'report.txt')}")
    print(f"[run] alerts saved: {os.path.join(ROOT, 'LATEST_ALERTS.txt')}")


if __name__ == "__main__":
    main()
