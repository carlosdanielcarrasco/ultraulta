#!/usr/bin/env python3
"""UltraUlta deal scraper (v2 — multi-store).

Pulls current beauty deals from community sources (Reddit, Slickdeals),
tags them by brand, offer type, and RETAILER, builds a direct "shop it"
link into each store, finds overlapping ("stackable") promos per brand,
and writes data/deals.json for the web app.

Stdlib only. Designed for GitHub Actions. Every source is best-effort:
if one fails, its last-known items are kept from the previous run.

Reddit: GitHub's servers are blocked by reddit.com, so this uses Reddit's
official free API when credentials are provided via env vars
REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (GitHub repo secrets). Without
them it still tries the public endpoints (works from residential IPs).

Usage:
  python3 scripts/scrape.py            # normal run
  python3 scripts/scrape.py --sample   # generate sample data (no network)
"""

import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "deals.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15")
API_UA = "web:ultraulta:v2 (personal deal tracker)"

MAX_AGE_DAYS = 21
STACK_MIN_OFFERS = 2

# ---- retailers: label, detection regex, product-search URL template ----
RETAILERS = [
    ("Ulta",      re.compile(r"\bulta\b", re.I),               "https://www.ulta.com/search?query={q}"),
    ("Sephora",   re.compile(r"\bsephora\b", re.I),            "https://www.sephora.com/search?keyword={q}"),
    ("Costco",    re.compile(r"\bcostco\b", re.I),             "https://www.costco.com/CatalogSearch?dept=All&keyword={q}"),
    ("Target",    re.compile(r"\btarget\b", re.I),             "https://www.target.com/s?searchTerm={q}"),
    ("Walmart",   re.compile(r"\bwalmart\b", re.I),            "https://www.walmart.com/search?q={q}"),
    ("Amazon",    re.compile(r"\bamazon\b", re.I),             "https://www.amazon.com/s?k={q}"),
    ("Walgreens", re.compile(r"\bwalgreens\b", re.I),          "https://www.walgreens.com/search/results.jsp?Ntt={q}"),
    ("CVS",       re.compile(r"\bcvs\b", re.I),                "https://www.cvs.com/search?searchTerm={q}"),
    ("Sally Beauty", re.compile(r"\bsally('s| beauty)?\b", re.I), "https://www.sallybeauty.com/search?q={q}"),
    ("TJ Maxx",   re.compile(r"\btj ?maxx\b", re.I),           "https://tjmaxx.tjx.com/store/shop/_/N-0?q={q}"),
    ("Marshalls", re.compile(r"\bmarshalls\b", re.I),          "https://www.marshalls.com/us/store/shop/_/N-0?q={q}"),
]
SEARCH_URL = {name: tpl for name, _, tpl in RETAILERS}

BRANDS = [
    "anastasia beverly hills", "abh", "bare minerals", "bareminerals",
    "benefit", "billie eilish", "biolage", "black girl sunscreen", "bubble",
    "charlotte tilbury", "cerave", "cetaphil", "clinique", "cosrx",
    "dermalogica", "drybar", "dyson", "e.l.f.", "elf cosmetics", "elemis",
    "essence", "estee lauder", "estée lauder", "fenty", "first aid beauty",
    "florence by mills", "good molecules", "grande cosmetics", "hempz",
    "igk", "it cosmetics", "joico", "juvia's place", "kenra", "kvd",
    "lancome", "lancôme", "laneige", "la roche-posay", "l'oreal", "l'oréal",
    "mac cosmetics", " mac ", "mario badescu", "maybelline", "mielle",
    "morphe", "murad", "nars", "native", "naturium", "not your mother's",
    "nyx", "ogx", "olaplex", "opi", "the ordinary", "pacifica",
    "paula's choice", "peach & lily", "philosophy", "pixi", "redken",
    "revlon", "sol de janeiro", "strivectin", "sun bum", "supergoop",
    "tarte", "tatcha", "too faced", "tree hut", "truly",
    "ulta beauty collection", "urban decay", "wet n wild", "wet n' wild",
    "shark", "t3 micro", " t3 ", "hot tools", "chi ", "bio ionic", "conair",
    "kylie", "rare beauty", "r.e.m. beauty", "about-face", "half magic",
    "polite society", "beautyblender", "real techniques", "eos", "batiste",
    "clairol", "garnier", "neutrogena", "no7", "physicians formula",
    "covergirl", "milani", "colourpop", "profusion", "kiss", "ardell",
]

OFFER_TYPES = {
    "percent_off": re.compile(r"\b\d{1,2}\s?%\s?off\b", re.I),
    "dollar_off":  re.compile(r"\$\s?\d+(\.\d+)?\s?off\b|\boff\s\$\d+", re.I),
    "gwp":         re.compile(r"\bgwp\b|gift with purchase|free\s.{0,25}?\b(gift|bag|sample|pc|piece|mini)\b", re.I),
    "points":      re.compile(r"\b\d+\s?x\s?points\b|\bpoints?\s(multiplier|event)\b|\bbonus points\b", re.I),
    "bogo":        re.compile(r"\bbogo\b|buy one,?\s?get one|\bb1g1\b|\bb2g\d\b", re.I),
    "coupon":      re.compile(r"\bcoupon\b|promo code|\bcode\s[A-Z0-9]{4,}\b", re.I),
    "sale":        re.compile(r"\bsale\b|\bclearance\b|\bdaily deals?\b|21 days of beauty|gorgeous hair event|jumbo|liter", re.I),
}
TYPE_LABELS = {
    "percent_off": "% off", "dollar_off": "$ off", "gwp": "Free gift",
    "points": "Points", "bogo": "BOGO", "coupon": "Coupon", "sale": "Sale",
}

STOPWORDS = set("the a an off free with for and or at on of in to buy get one new only today deal deals sale up".split())


def log(msg):
    print(f"[ultraulta] {msg}", flush=True)


def fetch(url, timeout=25, headers=None, data=None):
    h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
         "Accept": "application/json,text/xml,application/xml,text/html;q=0.9,*/*;q=0.8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def tag_brands(text):
    t = " " + text.lower() + " "
    found = []
    for b in BRANDS:
        if b in t:
            name = b.strip().title().replace("'S", "'s")
            name = {"Abh": "Anastasia Beverly Hills", "Elf Cosmetics": "e.l.f.",
                    "E.L.F.": "e.l.f.", "Bareminerals": "Bare Minerals",
                    "Estée Lauder": "Estee Lauder", "Lancôme": "Lancome",
                    "L'Oréal": "L'Oreal", "Wet N' Wild": "Wet N Wild",
                    "Mac Cosmetics": "MAC", "Mac": "MAC", "T3 Micro": "T3",
                    "T3": "T3", "Chi": "CHI", "Opi": "OPI", "Nyx": "NYX",
                    "Ogx": "OGX", "Nars": "NARS", "Igk": "IGK", "Kvd": "KVD",
                    "Cosrx": "COSRX", "Eos": "eos"}.get(name, name)
            if name not in found:
                found.append(name)
    return found


def tag_types(text):
    return [k for k, rx in OFFER_TYPES.items() if rx.search(text)]


def detect_retailer(text, default=None):
    # Earliest mention wins: "Costco: ... under Ulta price" is a Costco deal.
    best, pos = default, len(text) + 1
    for name, rx, _ in RETAILERS:
        m = rx.search(text)
        if m and m.start() < pos:
            best, pos = name, m.start()
    return best


def store_query(title, brands):
    if brands:
        return brands[0]
    words = [w for w in re.sub(r"[^\w\s']", " ", title).split()
             if w.lower() not in STOPWORDS and not w.replace("$", "").replace("%", "").isdigit()]
    return " ".join(words[:4]) or title[:40]


def store_url(retailer, title, brands):
    tpl = SEARCH_URL.get(retailer)
    if not tpl:
        return None
    return tpl.format(q=urllib.parse.quote_plus(store_query(title, brands)))


def item(source, title, url, created_utc, score=0, default_retailer=None):
    types = tag_types(title)
    brands = tag_brands(title)
    retailer = detect_retailer(title, default=default_retailer)
    return {
        "id": f"{source}:{abs(hash(url)) % 10**10}",
        "source": source,
        "title": title.strip(),
        "url": url,
        "created": int(created_utc),
        "score": score,
        "brands": brands,
        "types": types,
        "type_labels": [TYPE_LABELS[t] for t in types],
        "retailer": retailer,
        "store_url": store_url(retailer, title, brands),
    }


def keep(it):
    """A post earns its place if we know the store or the brand."""
    return bool(it["retailer"] or it["brands"])


# ---------------- sources ----------------

_reddit_token = None

def reddit_token():
    global _reddit_token
    if _reddit_token is not None:
        return _reddit_token
    cid = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    sec = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    if not cid or not sec:
        _reddit_token = ""
        return ""
    try:
        auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
        body = fetch("https://www.reddit.com/api/v1/access_token",
                     headers={"Authorization": f"Basic {auth}",
                              "User-Agent": API_UA,
                              "Content-Type": "application/x-www-form-urlencoded"},
                     data=b"grant_type=client_credentials")
        _reddit_token = json.loads(body).get("access_token", "")
        log("  reddit: got API token" if _reddit_token else "  reddit: token response missing access_token")
    except Exception as e:  # noqa: BLE001
        log(f"  reddit: token fetch failed: {e}")
        _reddit_token = ""
    return _reddit_token


def src_reddit(sub, listing="new", limit=75, default_retailer=None):
    tok = reddit_token()
    attempts = []
    if tok:
        attempts.append((f"https://oauth.reddit.com/r/{sub}/{listing}?limit={limit}&raw_json=1",
                         {"Authorization": f"Bearer {tok}", "User-Agent": API_UA}))
    attempts += [
        (f"https://www.reddit.com/r/{sub}/{listing}.json?limit={limit}&raw_json=1", None),
        (f"https://old.reddit.com/r/{sub}/{listing}.json?limit={limit}&raw_json=1", None),
    ]
    last_err = None
    for url, hdrs in attempts:
        try:
            data = json.loads(fetch(url, headers=hdrs))
            out = []
            for child in data.get("data", {}).get("children", []):
                d = child.get("data", {})
                title = d.get("title", "")
                if not title:
                    continue
                it = item(f"r/{sub}", title,
                          "https://www.reddit.com" + d.get("permalink", ""),
                          d.get("created_utc", 0), d.get("score", 0),
                          default_retailer=default_retailer)
                if keep(it):
                    out.append(it)
            log(f"  r/{sub}: {len(out)} kept via {url.split('/')[2]}")
            return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  failed {url}: {e}")
            time.sleep(2)
    raise RuntimeError(f"all reddit endpoints failed: {last_err}")


def src_slickdeals():
    queries = ["ulta", "sephora", "beauty", "costco beauty"]
    out, seen, ok = [], set(), 0
    last_err = None
    for q in queries:
        url = ("https://slickdeals.net/newsearch.php?src=SearchBarV2&searcharea=deals"
               f"&searchin=first&rss=1&q={urllib.parse.quote_plus(q)}")
        try:
            root = ET.fromstring(fetch(url))
            for el in root.iter("item"):
                title = (el.findtext("title") or "").strip()
                link = (el.findtext("link") or "").strip()
                pub = (el.findtext("pubDate") or "").strip()
                if not title or link in seen:
                    continue
                seen.add(link)
                try:
                    created = time.mktime(time.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S"))
                except Exception:
                    created = time.time()
                it = item("Slickdeals", title, link, created)
                if keep(it):
                    out.append(it)
            ok += 1
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  slickdeals '{q}' failed: {e}")
        time.sleep(1)
    if not ok:
        raise RuntimeError(f"all slickdeals queries failed: {last_err}")
    log(f"  slickdeals: {len(out)} kept across {ok} queries")
    return out


def src_hip2save():
    """Hip2Save (WordPress) exposes search results as RSS — no API needed."""
    queries = ["ulta", "sephora", "beauty deal"]
    out, seen, ok = [], set(), 0
    last_err = None
    for q in queries:
        url = f"https://hip2save.com/?s={urllib.parse.quote_plus(q)}&feed=rss2"
        try:
            root = ET.fromstring(fetch(url))
            for el in root.iter("item"):
                title = (el.findtext("title") or "").strip()
                link = (el.findtext("link") or "").strip()
                pub = (el.findtext("pubDate") or "").strip()
                if not title or link in seen:
                    continue
                seen.add(link)
                try:
                    created = time.mktime(time.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S"))
                except Exception:
                    created = time.time()
                it = item("Hip2Save", title, link, created)
                if keep(it):
                    out.append(it)
            ok += 1
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  hip2save '{q}' failed: {e}")
        time.sleep(1)
    if not ok:
        raise RuntimeError(f"all hip2save queries failed: {last_err}")
    log(f"  hip2save: {len(out)} kept across {ok} queries")
    return out


SOURCES = {
    # Reddit is optional: it only works if REDDIT_CLIENT_ID/SECRET secrets are
    # ever added. Without them it fails quietly and costs nothing.
    "reddit_muaonthecheap": lambda: src_reddit("MUAontheCheap"),
    "reddit_ulta":          lambda: src_reddit("Ulta", listing="hot", default_retailer="Ulta"),
    "slickdeals":           src_slickdeals,
    "hip2save":             src_hip2save,
}


# ---------------- processing ----------------

def find_stacks(items):
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    by_brand = {}
    for it in items:
        if it["created"] < cutoff:
            continue
        for b in it["brands"]:
            by_brand.setdefault(b, []).append(it)
    stacks = []
    for brand, its in by_brand.items():
        types = sorted({t for i in its for t in i["types"]})
        if len(types) >= 2 or len(its) >= STACK_MIN_OFFERS:
            retailers = sorted({i["retailer"] for i in its if i["retailer"]}) or ["Ulta"]
            stacks.append({
                "brand": brand,
                "count": len(its),
                "types": types,
                "type_labels": [TYPE_LABELS[t] for t in types],
                "retailers": retailers,
                "shop": [{"retailer": r,
                          "url": SEARCH_URL[r].format(q=urllib.parse.quote_plus(brand))}
                         for r in retailers],
                "item_ids": [i["id"] for i in its],
                "latest": max(i["created"] for i in its),
            })
    stacks.sort(key=lambda s: (len(s["types"]), s["count"], s["latest"]), reverse=True)
    return stacks


def load_previous():
    try:
        return json.loads(OUT.read_text())
    except Exception:
        return {}


def build(results, statuses, sample=False):
    items, seen = [], set()
    for lst in results.values():
        for it in lst:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            items.append(it)
    items.sort(key=lambda i: i["created"], reverse=True)
    return {
        "app": "UltraUlta",
        "sample": sample,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": statuses,
        "items": items[:250],
        "stacks": find_stacks(items),
    }


def sample_data():
    now = time.time()
    rows = [
        ("r/MUAontheCheap", "Ulta 20% off prestige coupon is live through Saturday (exclusions apply)", now - 3600),
        ("r/Ulta", "PSA: 5x points on all haircare stacks with the liter sale — Redken liters basically 60% off", now - 5400),
        ("Slickdeals", "Costco: Sol de Janeiro Bum Bum Cream 2-pack $38 (way under Ulta price)", now - 40000),
        ("r/MUAontheCheap", "Sephora: Tarte BOGO 50% off + free 6-pc gift with $40", now - 90000),
        ("Slickdeals", "Ulta: e.l.f. sale 30% off + free shipping over $35", now - 300000),
    ]
    results = {"sample": [item(s, t, f"https://example.com/{i}", c) for i, (s, t, c) in enumerate(rows)]}
    statuses = {k: {"ok": None, "count": 0, "note": "sample mode"} for k in SOURCES}
    return build(results, statuses, sample=True)


def main():
    if "--sample" in sys.argv:
        data = sample_data()
    else:
        prev = load_previous()
        prev_items = [i for i in prev.get("items", [])
                      if "example.com" not in i.get("url", "")]
        results, statuses = {}, {}
        for name, fn in SOURCES.items():
            try:
                log(f"fetching {name}…")
                results[name] = fn()
                statuses[name] = {"ok": True, "count": len(results[name]),
                                  "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            except Exception as e:  # noqa: BLE001
                log(f"  {name} FAILED: {e}")
                kept = [i for i in prev_items if name.split("_")[0] in i["source"].lower()
                        or i["source"].lower().replace("/", "_").replace("r_", "reddit_") in name]
                results[name] = kept
                statuses[name] = {"ok": False, "count": len(kept), "error": str(e)[:200],
                                  "fetched_at": prev.get("sources", {}).get(name, {}).get("fetched_at")}
        if not any(s["ok"] for s in statuses.values()) and prev:
            log("every source failed — keeping previous deals.json untouched")
            return
        data = build(results, statuses)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1))
    log(f"wrote {OUT} — {len(data['items'])} items, {len(data['stacks'])} stacks")


if __name__ == "__main__":
    main()
