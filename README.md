# College basketball 2026-27 - daily scrape

Runs **every day on GitHub Actions** (no laptop needed): NJCAA, NAIA, NCAA, NCCAA.

| Where | What |
|---|---|
| `26-27 College Data/<COLLEGE>/Scraped Data/` | stats files (team_total + player records) |
| `26-27 College Data/<COLLEGE>/Teams List/` | team lists (`partial` / `none`) |
| `LATEST_ALERTS.txt` | `ALL CLEAR`, or CRITICAL/WARNING if a site changed structure / returned nothing |
| `LATEST_REPORT.txt` | old-vs-new comparison, incl. how many teams moved none -> partial per scraper |
| `VALIDATION.txt` | output of `validate_all.py` |
| `LAST_RUN_UTC.txt` | when the last run finished |

* Schedule: `.github/workflows/daily.yml` (08:00 UTC = 13:00 PKT). Run it by hand: Actions -> daily-scrape -> *Run workflow*.
* A CRITICAL alert makes the run fail, so GitHub e-mails the owner. The data of a CRITICAL scraper is **not** copied into `26-27 College Data`.
* **NAIA** reads PrestoSports' public S3 JSON files directly (URLs in `NAIA_Scraper/naia_s3_urls.json`) - no browser, no Cloudflare.
* **NWAC** is *not* run here: its pages are behind a Cloudflare Turnstile that needs a human-verified browser. Its files in `26-27 College Data/NWAC` are whatever was last produced locally.
* NCAA raw working files are kept gzipped in `NCAA_Scraper/state/` so each day only fetches new games.
