#!/usr/bin/env python3
"""UltraUlta deal scraper.

Pulls current Ulta-related deals from community sources (Reddit, Slickdeals),
tags them by brand and offer type, finds overlapping ("stackable") promos,
and writes data/deals.json for the web app.

Stdlib only — no pip installs needed. Designed to run on GitHub Actions.
Every source is best-effort: if one fails, we keep its last-known items
from the previous deals.json rather than wiping them.

Usage:
  python3 scripts/scrape.py            # normal run
  python3 scripts/scrape.py --sample   # generate sample data (no network)
"""

import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "deals.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15")

MAX_AGE_DAYS = 21          # how long a community post counts as "active"
STACK_MIN_OFFERS = 2       # offers needed on one brand to call it a stack

# Brands commonly carried at Ulta. Lowercase; longest-first matching.
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


def log(msg):
    print(f"[ultraulta] {msg}", flush=True)


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json,text/xml,application/xml,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_any(urls):
    """Try each URL until one works."""
    last_err = None
    for u in urls:
        try:
            body = fetch(u)
            return body, u
        except Exception as e:  # noqa: BLE001 — every failure is non-fatal by design
            last_err = e
            log(f"  failed {u}: {e}")
            time.sleep(2)
    raise RuntimeError(f"all mirrors failed: {last_err}")


def tag_brands(text):
    t = " " + text.lower() + " "
    found = []
    for b in BRANDS:
        if b in t:
            name = b.strip().title().replace("'S", "'s")
            # Normalize a few aliases
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


def is_ulta_related(title, source):
    if source.startswith("r/Ulta"):
        return True
    return "ulta" in title.lower()


def item(source, title, url, created_utc, score=0):
    types = tag_types(title)
    return {
        "id": f"{source}:{abs(hash(url)) % 10**10}",
        "source": source,
        "title": title.strip(),
        "url": url,
        "created": int(created_utc),
        "score": score,
        "brands": tag_brands(title),
        "types": types,
        "type_labels": [TYPE_LABELS[t] for t in types],
    }


# ---------------- sources ----------------

def src_reddit(sub, listing="new", limit=60):
    body, used = fetch_any([
        f"https://www.reddit.com/r/{sub}/{listing}.json?limit={limit}&raw_json=1",
        f"https://old.reddit.com/r/{sub}/{listing}.json?limit={limit}&raw_json=1",
    ])
    data = json.loads(body)
    out = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        title = d.get("title", "")
        if not title:
            continue
        src = f"r/{sub}"
        if not is_ulta_related(title, src):
            continue
        out.append(item(src, title,
                        "https://www.reddit.com" + d.get("permalink", ""),
                        d.get("created_utc", 0), d.get("score", 0)))
    log(f"  r/{sub}: {len(out)} ulta-related posts via {used}")
    return out


def src_slickdeals():
    body, used = fetch_any([
        "https://slickdeals.net/newsearch.php?src=SearchBarV2&q=ulta&searcharea=deals&searchin=first&rss=1",
        "https://slickdeals.net/newsearch.php?q=ulta&searchin=first&rss=1",
    ])
    root = ET.fromstring(body)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        if not title or "ulta" not in title.lower():
            continue
        try:
            created = time.mktime(time.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S"))
        except Exception:
            created = time.time()
        out.append(item("Slickdeals", title, link, created))
    log(f"  slickdeals: {len(out)} items via {used}")
    return out


SOURCES = {
    "reddit_muaonthecheap": lambda: src_reddit("MUAontheCheap"),
    "reddit_ulta":          lambda: src_reddit("Ulta", listing="hot"),
    "slickdeals":           src_slickdeals,
}


# ---------------- processing ----------------

def find_stacks(items):
    now = time.time()
    cutoff = now - MAX_AGE_DAYS * 86400
    by_brand = {}
    for it in items:
        if it["created"] < cutoff:
            continue
        for b in it["brands"]:
            by_brand.setdefault(b, []).append(it)
    stacks = []
    for brand, its in by_brand.items():
        # Distinct offer signals: count unique offer types across items;
        # two typeless posts from different sources still hint at overlap.
        types = sorted({t for i in its for t in i["types"]})
        # A stack = one brand with 2+ distinct offer types in play (even in a
        # single post), or 2+ separate active posts about it.
        if len(types) >= 2 or len(its) >= STACK_MIN_OFFERS:
            stacks.append({
                "brand": brand,
                "count": len(its),
                "types": types,
                "type_labels": [TYPE_LABELS[t] for t in types],
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
    items = []
    seen = set()
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
        "items": items[:200],
        "stacks": find_stacks(items),
    }


def sample_data():
    now = time.time()
    rows = [
        ("r/MUAontheCheap", "Ulta 20% off prestige coupon is live through Saturday (exclusions apply)", now - 3600),
        ("r/MUAontheCheap", "Ulta Gorgeous Hair Event: Redken + Biolage liters up to 50% off this week", now - 7200),
        ("r/Ulta", "PSA: 5x points on all haircare stacks with the liter sale — Redken liters basically 60% off", now - 5400),
        ("Slickdeals", "Ulta Beauty: Tarte free 6-pc gift with $40 purchase + BOGO 50% off", now - 90000),
        ("r/Ulta", "Sol de Janeiro GWP + $10 off $50 coupon worked together for me online", now - 200000),
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
        prev_items = prev.get("items", [])
        results, statuses = {}, {}
        for name, fn in SOURCES.items():
            try:
                log(f"fetching {name}…")
                results[name] = fn()
                statuses[name] = {"ok": True, "count": len(results[name]),
                                  "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            except Exception as e:  # noqa: BLE001
                log(f"  {name} FAILED: {e}")
                # keep this source's items from the previous run
                kept = [i for i in prev_items if i["source"].lower().replace("/", "_").replace("r_", "reddit_").lower() in name or name.split("_")[0] in i["source"].lower()]
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
