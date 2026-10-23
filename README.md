# GetoutofDallas.com

Flight + hotel package deals from DFW and Love Field, plus drive-to cabin and hotel deals, re-checked every morning.

Live at **https://getoutofdallas.com** (GitHub Pages).

## How it works

- `site/` is the whole website: `index.html`, the deal list `board.json`, and the destination `photos/`.
- Two Claude scheduled tasks keep the deals current: a daily price check (Tue–Sun, about 6:47 AM CT) and a Monday full refresh (about 7:57 AM CT). After each run they save the board to the shared Google Drive folder **GetoutofDallas.com board data** as `board-YYYY-MM-DD-HHMM-<sha8>.json`, built with `scripts/build_board.py`.
- `.github/workflows/deploy.yml` runs at 15:17 and 20:17 UTC (and on every push). It pulls the newest board from Drive with `scripts/pull_board.py`, commits it if it changed, and deploys `site/` to GitHub Pages. A bad, missing or tampered file is skipped and the current board stays up.

## Editing

- To change the page, edit `site/index.html` and push to `main`. The site redeploys in about a minute.
- To refresh deals right away, open **Actions → Sync deals and deploy → Run workflow**.
- Deals past their book-by or travel dates hide themselves in the browser, so an old board never shows expired trips.

Photos are Creative Commons or public-domain shots from Flickr; credits are listed on the page.
