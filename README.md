# UltraUlta 💄

Every live Ulta deal on one screen — and which ones **stack**.

Pulls current Ulta promos from the places deal-hunters actually post them
(r/MUAontheCheap, r/Ulta, Slickdeals), tags each one by brand and offer type,
and flags brands with overlapping promos ("Stacking now"). Runs free on
GitHub Pages + GitHub Actions.

## One-time setup

1. Push this repo to GitHub.
2. **Settings → Pages** → Source: *Deploy from a branch* → Branch: `main`, folder `/ (root)` → Save.
3. **Actions tab** → *Refresh deals* → **Run workflow** (first real data pull).
   After that it re-runs itself every 6 hours.
4. Open `https://<username>.github.io/ultraulta` on the iPhone in Safari →
   Share → **Add to Home Screen**. Done — it's an app now.

If the Action's push step fails with a permissions error:
Settings → Actions → General → Workflow permissions → **Read and write** → Save.

## How it works

- `scripts/scrape.py` — stdlib-only scraper; writes `data/deals.json`.
  If a source is down it keeps that source's last-known items.
- `.github/workflows/refresh.yml` — cron (every 6h) + manual run button.
- `index.html` — the app. Also fetches Reddit live from the phone itself as a
  top-up, so it stays fresh even if a source blocks GitHub's servers.
- Favorites (★) are saved on the device.

## Unlocking the Reddit sources (one-time, ~3 min)

Reddit blocks GitHub's servers, so the Action needs free official API creds:

1. Log into reddit.com → https://www.reddit.com/prefs/apps → **create another app**.
2. Name: `ultraulta` · type: **script** · redirect uri: `http://localhost` → Create.
3. Copy the ID under the app name (client id) and the **secret**.
4. Repo → Settings → Secrets and variables → **Actions** → New repository secret:
   - `REDDIT_CLIENT_ID` = the id
   - `REDDIT_CLIENT_SECRET` = the secret
5. Re-run the Refresh deals workflow — the log should say "reddit: got API token".

## Tuning

- Add/remove brands: `BRANDS` list in `scripts/scrape.py` **and** `index.html`.
- Stack sensitivity: `MAX_AGE_DAYS` / `STACK_MIN_OFFERS` in `scripts/scrape.py`.

*It's a deal spotter, not a promise — Ulta's exclusion lists get the final word at checkout.*
