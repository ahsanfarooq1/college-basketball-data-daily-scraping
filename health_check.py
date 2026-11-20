"""health_check.py - alerts when a website changes its structure (or a scrape silently returns nothing).

Used by run_all.py after every run. Produces a plain-text ALERTS.txt:
    CRITICAL = data is NOT trustworthy -> that scraper's files are NOT copied into '26-27 College Data'
    WARNING  = look at it, but the data is probably usable

What it catches:
  * scraper crashed / could not find what it expects (e.g. NAIA renamed teamsData -> teamsPageData)
  * output file missing, empty, or records missing the required fields
  * data collapsed vs the previous run (teams with games / total GP / record count fell > 25%)
  * every team's points are 0/null although the previous run had data
  * scraper said OK but never rewrote its file (stale data)
  * team list missing/empty/shrank
  * [WARN]/[ERR]/Traceback lines printed in the scraper log even though it finished
"""
import datetime as dt
import os

STRUCT_PATTERNS = ("could not capture", "not captured", "keyerror", "jsondecodeerror", "indexerror",
                   "attributeerror", "typeerror", "nonetype", "not rolled over", "expecting value",
                   "list index out of range", "[structure", "empty page", "unexpected")
LOG_WARN_PATTERNS = ("[WARN]", "[ERR]", "Traceback", "[STRUCTURE")
REQ_KEYS = {"team_id", "team_name", "record_type", "GP"}
DROP_FRAC = 0.25      # a fall of more than 25% is treated as a broken scrape


def _log_lines(job, n=400):
    try:
        with open(job.logpath, encoding="utf-8", errors="replace") as f:
            return [l.rstrip() for l in f.readlines()[-n:] if l.strip()]
    except Exception:
        return []


def health_alerts(names, before_dir, jobs, outputs, snapshot_path, stats_summary, load):
    """Return [(severity, scraper, title, [detail lines])]."""
    alerts = []
    for n in names:
        j = jobs.get(n)
        stats, lists = outputs(n)

        # 1. the scraper itself failed
        if j and j.status == "FAILED":
            tail = _log_lines(j)
            cf = [l for l in tail if "[cloudflare block]" in l.lower() or "just a moment" in l.lower()
                  or "err_connection_closed" in l.lower()]
            hits = [l for l in tail if any(p in l.lower() for p in STRUCT_PATTERNS)]
            if cf:
                hits = cf
                title = ("BLOCKED BY CLOUDFLARE (not a site change) - open the debug Chrome, solve the "
                         "'verify you are human' box, then rerun this scraper")
            else:
                title = ("WEBSITE STRUCTURE CHANGE SUSPECTED - the scraper could not find the data it expects"
                         if hits else "SCRAPER FAILED (reason not recognised - read the log)")
            det = [f"error    : {j.err}"] + [f"evidence : {l[:200]}" for l in (hits or tail)[-4:]]
            det.append(f"full log : {j.logpath}")
            alerts.append(("CRITICAL", n, title, det))

        # 2. stats files
        for p in stats:
            fn = os.path.basename(p)
            if not os.path.exists(p):
                alerts.append(("CRITICAL", n, f"OUTPUT FILE MISSING: {fn}", ["the scraper did not create this file"]))
                continue
            d = load(p)
            if not isinstance(d, list) or len(d) == 0:
                alerts.append(("CRITICAL", n, f"EMPTY OUTPUT: {fn} has 0 records",
                               ["the site probably changed its layout/API, or the scrape was blocked"]))
                continue
            bad = sum(1 for r in d if not isinstance(r, dict) or not REQ_KEYS <= set(r))
            if bad / len(d) > 0.05:
                alerts.append(("CRITICAL", n, f"RECORD FORMAT CHANGED: {fn}",
                               [f"{bad} of {len(d)} records are missing one of {sorted(REQ_KEYS)}"]))
            if j and j.status == "OK" and os.path.getmtime(p) < j.t0:
                alerts.append(("WARNING", n, f"FILE NOT REWRITTEN: {fn}",
                               ["the scraper said OK but did not write this file during the run (data may be stale)"]))

            old_p = snapshot_path(before_dir, n, p)
            if not os.path.exists(old_p):
                continue
            old, o, nw = (load(old_p) or []), stats_summary(old_p), stats_summary(p)
            if not (o and nw):
                continue
            ot = {k: v for k, v in o["teams"].items() if v[0] > 0}
            nt = {k: v for k, v in nw["teams"].items() if v[0] > 0}
            gpo, gpn = sum(v[0] for v in ot.values()), sum(v[0] for v in nt.values())
            if len(ot) >= 5 and len(nt) < len(ot) * (1 - DROP_FRAC):
                alerts.append(("CRITICAL", n, f"DATA COLLAPSED: {fn}",
                               [f"teams with games {len(ot)} -> {len(nt)} (more than {int(DROP_FRAC * 100)}% lost)"]))
            elif gpo >= 20 and gpn < gpo * (1 - DROP_FRAC):
                alerts.append(("CRITICAL", n, f"DATA COLLAPSED: {fn}", [f"total team GP {gpo} -> {gpn}"]))
            if len(old) >= 50 and len(d) < len(old) * (1 - DROP_FRAC):
                alerts.append(("CRITICAL", n, f"RECORD COUNT COLLAPSED: {fn}", [f"records {len(old)} -> {len(d)}"]))
            pts_o = sum(r.get("Pts") or 0 for r in old if r.get("record_type") == "team_total")
            pts_n = sum(r.get("Pts") or 0 for r in d if r.get("record_type") == "team_total")
            if pts_o > 0 and pts_n == 0:
                alerts.append(("CRITICAL", n, f"ALL STATS ZERO/NULL: {fn}",
                               ["every team's points are 0/null although the previous run had data"]))

        # 3. team lists
        for p in lists:
            fn = os.path.basename(p)
            d = load(p)
            if not isinstance(d, list) or len(d) == 0:
                alerts.append(("CRITICAL", n, f"TEAM LIST EMPTY/MISSING: {fn}", []))
                continue
            old = load(snapshot_path(before_dir, n, p))
            if isinstance(old, list) and len(old) >= 10 and len(d) < len(old) * (1 - DROP_FRAC):
                alerts.append(("WARNING", n, f"TEAM LIST SHRANK: {fn}", [f"teams {len(old)} -> {len(d)}"]))

        # 4. warnings the scraper printed even though it finished
        if j and j.status == "OK":
            warn = [l for l in _log_lines(j, 1500) if any(w in l for w in LOG_WARN_PATTERNS)]
            if warn:
                alerts.append(("WARNING", n, f"{len(warn)} warning line(s) in the scraper log",
                               [f"  {l[:180]}" for l in warn[:4]] + [f"full log: {j.logpath}"]))
    return alerts


def alert_text(alerts, names):
    crit = [a for a in alerts if a[0] == "CRITICAL"]
    warn = [a for a in alerts if a[0] == "WARNING"]
    out = [f"ALERT REPORT  {dt.datetime.now():%Y-%m-%d %H:%M}", "=" * 70]
    if not alerts:
        out.append("STATUS: ALL CLEAR - no website structure change or bad output detected "
                   f"({', '.join(x.upper() for x in names)})")
        return "\n".join(out)
    out.append(f"STATUS: {len(crit)} CRITICAL, {len(warn)} WARNING")
    if crit:
        out.append("CRITICAL = data is NOT trustworthy. Those scrapers' files were NOT copied into '26-27 College Data'.")
    for sev, n, title, det in sorted(alerts, key=lambda a: (a[0] != "CRITICAL", a[1])):
        out.append(f"\n[{sev}] {n.upper()} - {title}")
        out += [f"    {x}" for x in det]
    return "\n".join(out)


def write_alerts(alerts, names, run_dir, root):
    txt = alert_text(alerts, names)
    for p in (os.path.join(run_dir, "ALERTS.txt"), os.path.join(root, "LATEST_ALERTS.txt")):
        with open(p, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
    return txt
