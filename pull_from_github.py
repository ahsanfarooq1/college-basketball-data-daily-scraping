"""pull_from_github.py - pull the latest NAIA / NCAA / NJCAA / NCCAA data from GitHub and OVERWRITE the local files.

The GitHub Action (repo ahsanfarooq1/college-basketball-data-daily-scraping) scrapes the 4 sites every day and commits
"26-27 College Data/<COLLEGE>/{Scraped Data,Teams List}/". This downloads the repo as one zip (public repo, no login)
and replaces the same files in the local "26-27 College Data" folder.

    python pull_from_github.py                # pull all 4 and overwrite
    python pull_from_github.py --dry-run      # only show what would change
    python pull_from_github.py --only naia,ncaa
    python pull_from_github.py --backup       # keep the old files in _pull_backups/<timestamp>/

NWAC is NOT touched (it is not scraped on GitHub). Nothing is ever deleted. A file is only replaced after the
downloaded copy was checked to be valid JSON, and the pull is refused if a scraper's files look incomplete.
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile

REPO = "ahsanfarooq1/college-basketball-data-daily-scraping"
BRANCH = "main"
ZIP_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
ROOT = os.path.dirname(os.path.abspath(__file__))
TIDY = os.path.join(ROOT, "26-27 College Data")
SCRAPERS = ("NAIA", "NCAA", "NJCAA", "NCCAA")
EXPECT = {"NAIA": (2, 2), "NCAA": (6, 6), "NJCAA": (6, 6), "NCCAA": (4, 4)}   # (stats files, team-list files)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def download():
    print(f"[pull] downloading {ZIP_URL}")
    req = urllib.request.Request(ZIP_URL, headers={"User-Agent": "college-data-pull"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    print(f"[pull] got {len(data) / 1e6:.1f} MB")
    return zipfile.ZipFile(io.BytesIO(data))


def n_records(raw):
    try:
        d = json.loads(raw)
        return len(d) if isinstance(d, list) else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma list: naia,ncaa,njcaa,nccaa (default all 4)")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    ap.add_argument("--backup", action="store_true", help="copy the files being replaced to _pull_backups/<timestamp>/")
    a = ap.parse_args()

    names = [n.strip().upper() for n in a.only.split(",")] if a.only else list(SCRAPERS)
    bad = [n for n in names if n not in SCRAPERS]
    if bad:
        sys.exit(f"unknown scraper(s): {bad} (valid: {', '.join(s.lower() for s in SCRAPERS)})")

    z = download()
    top = z.namelist()[0].split("/")[0]                    # e.g. "college-basketball-data-daily-scraping-main"
    prefix = f"{top}/26-27 College Data/"
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = os.path.join(ROOT, "_pull_backups", stamp)

    # --- repo status files (read first, so you see problems before trusting the data)
    for fn in ("LATEST_ALERTS.txt", "LAST_RUN_UTC.txt"):
        try:
            txt = z.read(f"{top}/{fn}").decode("utf-8", "replace").strip().splitlines()
            line = next((l for l in txt if l.startswith("STATUS")), txt[0] if txt else "")
            print(f"[repo] {fn}: {line}")
        except KeyError:
            pass

    grand = {"updated": 0, "unchanged": 0, "new": 0}
    problems = []
    for n in names:
        files = [m for m in z.namelist() if m.startswith(prefix + n + "/") and m.endswith(".json")]
        stats = [m for m in files if "/Scraped Data/" in m]
        lists = [m for m in files if "/Teams List/" in m]
        exp = EXPECT[n]
        if (len(stats), len(lists)) != exp:
            problems.append(f"{n}: repo has {len(stats)} data + {len(lists)} list files, expected {exp} - NOT pulled")
            continue
        # validate everything BEFORE touching local files
        payload, invalid = [], []
        for m in files:
            raw = z.read(m)
            cnt = n_records(raw)
            if not cnt:
                invalid.append(os.path.basename(m))
            payload.append((m, raw, cnt))
        if invalid:
            problems.append(f"{n}: empty/invalid JSON in repo copy {invalid} - NOT pulled")
            continue

        print(f"\n[{n}]")
        c = {"updated": 0, "unchanged": 0, "new": 0}
        for m, raw, cnt in payload:
            rel = m[len(prefix):]
            dst = os.path.join(TIDY, *rel.split("/"))
            label = f"{rel.split('/', 1)[1]}"
            if os.path.exists(dst):
                with open(dst, "rb") as f:
                    old = f.read()
                # Windows-written files use CRLF, git stores LF: ignore line endings when deciding "changed"
                same = lambda b: hashlib.sha256(b.replace(b"\r\n", b"\n")).digest()
                if same(old) == same(raw):
                    c["unchanged"] += 1
                    continue
                oc = n_records(old)
                kind, note = "updated", f"{oc} -> {cnt} records"
            else:
                kind, note = "new", f"{cnt} records"
            c[kind] += 1
            print(f"   {kind:9} {label}   ({note})")
            if a.dry_run:
                continue
            if a.backup and kind == "updated":
                bk = os.path.join(backup_dir, *rel.split("/"))
                os.makedirs(os.path.dirname(bk), exist_ok=True)
                shutil.copy2(dst, bk)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".tmp"
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, dst)                      # atomic overwrite
        print(f"   -> {c['updated']} updated, {c['new']} new, {c['unchanged']} unchanged")
        for k in grand:
            grand[k] += c[k]

    # --- keep the repo's latest reports next to the data
    if not a.dry_run:
        for fn in ("LATEST_ALERTS.txt", "LATEST_REPORT.txt", "VALIDATION.txt"):
            try:
                with open(os.path.join(TIDY, "GITHUB_" + fn), "wb") as f:
                    f.write(z.read(f"{top}/{fn}"))
            except KeyError:
                pass

    print(f"\n[pull] {'DRY RUN - nothing written. ' if a.dry_run else ''}"
          f"total: {grand['updated']} updated, {grand['new']} new, {grand['unchanged']} unchanged")
    if a.backup and not a.dry_run and os.path.isdir(backup_dir):
        print(f"[pull] old versions saved in {backup_dir}")
    if not a.dry_run:
        print(f"[pull] reports saved as {os.path.join(TIDY, 'GITHUB_LATEST_REPORT.txt')} (and _ALERTS / _VALIDATION)")
    for p in problems:
        print(f"[PROBLEM] {p}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
