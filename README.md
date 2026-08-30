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

## Sources

Active with zero setup: **Slickdeals** (search RSS) and **Hip2Save** (search RSS)
across Ulta, Sephora, Costco, Target and more.

**Reddit (optional, dormant):** reddit.com blocks GitHub's servers and gates its
API behind a registration request. If that request is ever approved, create a
"script" app at reddit.com/prefs/apps and add repo secrets `REDDIT_CLIENT_ID`
and `REDDIT_CLIENT_SECRET` — r/MUAontheCheap and r/Ulta light up on the next
run. Until then those two sources fail quietly and cost nothing.

## Tuning

- Add/remove brands: `BRANDS` list in `scripts/scrape.py` **and** `index.html`.
- Stack sensitivity: `MAX_AGE_DAYS` / `STACK_MIN_OFFERS` in `scripts/scrape.py`.

*It's a deal spotter, not a promise — Ulta's exclusion lists get the final word at checkout.*
