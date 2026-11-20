# NCCAA scraper (thenccaa.org) - 2026-27
Run: `D:\Apps\Python\python.exe nccaa_scraper.py --sport both` (or MBKB / WBKB; `--year 2025` for 2025-26).
No Cloudflare / browser needed; plain requests. ~70 teams per sport, ~1 min per run.

## Site structure
- Division is in the path: mbball=Men DI, mbball2=Men DII, wbball=Women DI, wbball2=Women DII. year=2026 is 2026-27.
- Hub /stats.aspx: team leaderboards -> gives the team list (slug) per division.
- /teamstats.aspx: the player table is empty HTML; it is filled from /services/conf_stats.ashx?method=get_team_stats using an internal team_id embedded in the page.
- That JSON has players, team totals, opponent totals, W-L records, game log.
- Members page used only to flag dual-division (#) schools.

## Output (2026_27_data/)
nccaa_teams_*, nccaa_team_stats_*, nccaa_player_stats_*, nccaa_games_* (suffix mbkb_26_27 / wbkb_26_27).

## Share outputs (written by nccaa_to_pinecone.py)
`26-27 College Data\NCCAA\Scraped Data\NCCAA_Stats_{MBB,WBB}_{DI,DII}_2026_2027.json` (4 files, NAIA record shape + `division`)
`26-27 College Data\NCCAA\Teams List\NCCAA_{Men,Women}_Teams_{DI,DII}_2026-27.json` (4 files)
Validate: `python nccaa_validate.py [--live]`. Run order: scraper -> to_pinecone -> validate (see run_nccaa.ps1).
