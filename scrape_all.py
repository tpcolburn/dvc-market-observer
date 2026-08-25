#!/usr/bin/env python3
"""
Sweep every DVC resale listing on the brokers that permit it, at every contract
size, and append today's snapshot to data/listings_history.csv.

Deed year and annual dues are read off the listing pages themselves rather than
hardcoded, so the resort reference data stays correct on its own.

Standard library only — nothing to pip install.
"""

import csv, gzip, html, re, sys, time, urllib.request, urllib.error
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
HISTORY = BASE / "data" / "listings_history.csv"
UA = "dvc-observer/2.0 (personal market tracker; contact tpcolburn@gmail.com)"
DELAY = 1.2

FIELDS = ["date", "broker", "listing_id", "url", "resort", "points", "use_year",
          "price", "price_per_point", "dues_per_point", "deed_year", "status",
          "point_delta", "cost_per_point_year"]

# broker slug -> canonical resort. Old Key West is split by deed: the 2057
# contracts are the extended ones and are a different asset from the 2042s.
RESORT = {
    "animal-kingdom": "Animal Kingdom Villas", "animal-kingdom-lodge": "Animal Kingdom Villas",
    "aulani": "Aulani", "bay-lake-tower": "Bay Lake Tower",
    "beach-club": "Beach Club Villas", "beach-club-villas": "Beach Club Villas",
    "boardwalk": "BoardWalk Villas", "boardwalk-villas": "BoardWalk Villas",
    "boulder-ridge": "Boulder Ridge Villas", "wilderness-lodge": "Boulder Ridge Villas",
    "copper-creek": "Copper Creek Villas",
    "disneyland-hotel": "Villas at Disneyland Hotel",
    "fort-wilderness": "Cabins at Fort Wilderness", "cabins-at-fort-wilderness": "Cabins at Fort Wilderness",
    "grand-californian": "Grand Californian Villas",
    "grand-floridian": "Grand Floridian Villas",
    "hilton-head": "Hilton Head Island",
    "old-key-west": "Old Key West", "old-key-west-2057": "Old Key West (2057)",
    "old-key-west57": "Old Key West (2057)",
    "polynesian": "Polynesian Villas", "polynesian-villas-and-bungalows": "Polynesian Villas",
    "riviera": "Riviera Resort", "rivieraresort": "Riviera Resort",
    "saratoga-springs": "Saratoga Springs", "vero-beach": "Vero Beach",
}

MONTHS = ("January February March April May June July August September "
          "October November December").split()


def log(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def fetch(url, retries=2):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    for a in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
            if a == retries:
                return None
        except Exception:
            if a == retries:
                return None
        time.sleep(2 * (a + 1))
    return None


def flat(doc):
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", doc)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"[\s\xa0]+", " ", html.unescape(t))


def num(s):
    return float(re.sub(r"[^\d.]", "", s)) if s else None


# --------------------------------------------------------------- per broker

def current_uy_year(use_year, today=None):
    """The use year that is running right now. An April use year opened on
    1 Apr 2026 and runs to 31 Mar 2027, so in August 2026 the current one is
    2026. Needed because a listing that mentions only future years is not
    unstripped — it is missing the current year entirely, which is the most
    expensive kind of stripping there is."""
    today = today or date.today()
    if not use_year or use_year not in MONTHS:
        return None
    m = MONTHS.index(use_year) + 1
    return today.year if today.month >= m else today.year - 1


def points_delta(n, use_year, by_year, current_avail=None, complete=False):
    """Surplus/deficit against a clean contract.

    Brokers report availability in two incompatible ways, and conflating them
    is what put CCB2995 at -433 when it is -283:

    complete=True  (DVC Resale Market) — every use year gets a row, including
        empty ones ("December 2026 - 0 points"). The listed span IS the span;
        inferring an earlier year invents a deficit that is not there.
    complete=False (DVC Store) — only years holding points are named, plus
        "N points currently available" for the year in progress. Here a year
        the listing never mentions really is a year with nothing in it.

    Returns None when we cannot tell — an unknown deficit must not be
    recorded as zero.
    """
    if not by_year and current_avail is None:
        return None
    years = dict(by_year)
    cy = None if complete else current_uy_year(use_year)
    if cy is not None:
        # the running year counts even when the listing says nothing about it
        years.setdefault(cy, current_avail if current_avail is not None else 0)
    elif current_avail is not None and not complete:
        years.setdefault(min(years) - 1 if years else 0, current_avail)
    if not years:
        return None
    span = max(years) - min(years) + 1
    for y in range(min(years), max(years) + 1):
        years.setdefault(y, 0)
    return sum(years.values()) - n * span


def p_dvcrm(t, url):
    g = lambda p: (re.search(p, t).group(1) if re.search(p, t) else None)
    pts, price = g(r"Points on Contract\s*([\d,]+)"), g(r"Price\s*\$([\d,]+)")
    if not pts or not price:
        return None
    avail = re.findall(r"([A-Za-z]+) (\d{4}) - ([\d,]+) points", t)
    n = int(num(pts))
    uy = g(r"Use Year\s*(" + "|".join(MONTHS) + r")")
    delta = points_delta(n, uy, {int(y): int(num(p)) for _, y, p in avail}, complete=True)
    return dict(listing_id=g(r"Listing ID\s*([A-Z0-9]+)") or url.rstrip("/").split("/")[-1].upper(),
                points=n, price=int(num(price)),
                price_per_point=num(g(r"Price Per Point\s*\$([\d,.]+)")),
                use_year=g(r"Use Year\s*(" + "|".join(MONTHS) + r")"),
                dues_per_point=num(g(r"Dues per Point\s*\$([\d.]+)")),
                deed_year=int(num(g(r"Deed Expiration\s*(\d{4})")) or 0) or None,
                closing=num(g(r"Closing Costs\*?\s*\$([\d,]+) for a cash")),
                caf=num(g(r"Disney \(CAF\) Fee\s*\$([\d,]+)")), point_delta=delta)


def p_dvcstore(t, url):
    g = lambda p: (re.search(p, t).group(1) if re.search(p, t) else None)
    pts, price = g(r"(\d+)\s*Point Deed"), g(r"Price\s*\$([\d,]+)")
    if not pts or not price:
        return None
    n = int(num(pts))
    uy = g(r"[-–]\s*([A-Za-z]+)\s*Use Year")
    by_year, m2 = {}, re.findall(r"([\d,]+) points? coming on \d{1,2}/\d{1,2}/(\d{2,4})", t)
    for p_, y_ in m2:
        y_ = int(y_)
        by_year[y_ + 2000 if y_ < 100 else y_] = int(num(p_))
    cur = re.search(r"([\d,]+) points? currently available", t)
    delta = points_delta(n, uy, by_year, int(num(cur.group(1))) if cur else None)
    return dict(listing_id=g(r"([A-Z]{2,4}\d+[A-Z0-9-]*)") or url.rstrip("/").split("/")[-1].upper(),
                points=n, price=int(num(price)),
                price_per_point=num(g(r"Price Per Point\s*\$([\d,.]+)")),
                use_year=g(r"[-–]\s*([A-Za-z]+)\s*Use Year"),
                dues_per_point=num(g(r"Dues Per Point\s*\$([\d.]+)")),
                deed_year=int(num(g(r"Expiration\s*(20\d\d)")) or 0) or None,
                closing=num(g(r"Closing Costs\*?\s*\$([\d,]+)")),
                caf=num(g(r"Disney CAF\*{0,2}\s*\$([\d,]+)")), point_delta=delta)


def _uy(abbr):
    """dvcsales prints the use year as a three-letter abbreviation."""
    if not abbr:
        return None
    for m in MONTHS:
        if m.upper().startswith(abbr.upper()):
            return m
    return None


def p_dvcsales(t, url):
    g = lambda p: (re.search(p, t, re.I).group(1) if re.search(p, t, re.I) else None)
    m = re.search(r"/(\d+)-points/", url) or re.search(r"/(\d+)-points", url)
    pts = m.group(1) if m else g(r"([\d,]+)\s*points\b")
    ppp = g(r"\$\s*([\d.]+)\s*(?:/|per )\s*(?:pt|point)")
    price = g(r"(?:asking|price)[^$]{0,20}\$([\d,]{4,})")
    if not pts or not (ppp or price):
        return None
    n = int(num(pts))
    prc = int(num(price)) if price else int(round(num(ppp) * n))
    return dict(listing_id=url.rstrip("/").split("/")[-1].upper(), points=n, price=prc,
                price_per_point=num(ppp) if ppp else round(prc / n, 2),
                use_year=_uy(g(r"Use Year\s+([A-Z]{3})\b")) or g(r"use year[:\s]*(" + "|".join(MONTHS) + r")"),
                dues_per_point=num(g(r"Annual Dues\s*\$([\d.]+)")),
                deed_year=int(num(g(r"(?:expir\w*|deed)[^\d]{0,20}(20\d\d)")) or 0) or None,
                closing=None, caf=None, point_delta=None)


# status read from each broker's own field; whole-page keyword matching hits
# FAQ text, testimonials and "how buying works" boilerplate
STATUS = {
    "dvcresalemarket": [re.compile(r"(?i)\b(sale pending|offer accepted|under contract)\b")],
    "dvcstore": [re.compile(r"(?i)Status\s+(Sold|Sale Pending|Under Contract)\b"),
                 re.compile(r"(?i)^\s*.{0,200}?\b(SALE PENDING)\s*-\s*[A-Z]{2,4}\d")],
    "dvcsales": [],
}

BROKERS = [
    dict(key="dvcresalemarket", label="DVC Resale Market",
         sitemap="https://www.dvcresalemarket.com/listing-sitemap.xml",
         slug=r"/listings/([a-z0-9-]+)/", parse=p_dvcrm),
    dict(key="dvcstore", label="The DVC Store",
         sitemap="https://www.dvcstore.com/resale-listing-sitemap.xml",
         slug=r"/resort/([a-z0-9-]+)/[^/]+/?$", parse=p_dvcstore),
    dict(key="dvcsales", label="DVC Sales",
         sitemap="https://dvcsales.com/sitemap-listings.xml",
         slug=r"/dvc-resale/([a-z0-9-]+)/", parse=p_dvcsales),
]


def status_of(t, key):
    for pat in STATUS.get(key, []):
        m = pat.search(t)
        if m:
            return m.group(1).title()
    return "Available" if STATUS.get(key) else "Unverified"


def carrying_cost(rec, today_year):
    """Cost per point-year: the only cross-resort comparable. Needs deed + dues."""
    if not rec.get("deed_year") or not rec.get("dues_per_point") or not rec.get("points"):
        return None
    yrs = rec["deed_year"] - today_year
    if yrs <= 0:
        return None
    closing = rec.get("closing") if rec.get("closing") is not None else 95 + 2 * rec["points"]
    caf = rec.get("caf") if rec.get("caf") is not None else 500
    acq = rec["price"] + closing + caf - (rec.get("point_delta") or 0) * 19
    return round((acq / yrs + rec["points"] * rec["dues_per_point"]) / rec["points"], 3)


def main():
    today = date.today()
    rows, seen, health = [], set(), {}
    for b in BROKERS:
        doc = fetch(b["sitemap"])
        urls = re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*(https?://[^\s<\]]+)", doc or "")
        targets = []
        for u in urls:
            m = re.search(b["slug"], u)
            if m and m.group(1) in RESORT:
                targets.append((u, RESORT[m.group(1)]))
        if not urls:
            # a sitemap that returns nothing is a failure, never a legitimately
            # empty inventory — say so loudly instead of silently losing a broker
            log(f"::error::{b['label']}: sitemap returned 0 urls — source unreachable")
            health[b["label"]] = dict(ok=False, urls=0, targets=0, parsed=0)
            continue
        log(f"{b['label']}: {len(urls)} urls, {len(targets)} listings")
        ok = 0
        for i, (u, resort) in enumerate(targets, 1):
            doc = fetch(u)
            time.sleep(DELAY)
            if not doc:
                continue
            t = flat(doc)
            rec = b["parse"](t, u)
            if not rec or not rec.get("points") or not rec.get("price"):
                continue
            key = (b["key"], rec["listing_id"])
            if key in seen:
                continue
            seen.add(key)
            rec["cost_per_point_year"] = carrying_cost(rec, today.year)
            rows.append({"date": today.isoformat(), "broker": b["label"],
                         "url": u, "resort": resort, "status": status_of(t, b["key"]),
                         **{k: rec.get(k) for k in
                            ["listing_id", "points", "use_year", "price", "price_per_point",
                             "dues_per_point", "deed_year", "point_delta", "cost_per_point_year"]}})
            ok += 1
            if i % 50 == 0:
                log(f"  {i}/{len(targets)} ({ok} parsed)")
        log(f"  {ok}/{len(targets)} parsed")
        rate = ok / len(targets) if targets else 0
        health[b["label"]] = dict(ok=rate >= 0.5, urls=len(urls), targets=len(targets), parsed=ok)
        if rate < 0.5:
            log(f"::error::{b['label']}: only {ok}/{len(targets)} parsed — parser may be broken")

    if not rows:
        log("::error::no rows scraped at all — aborting so history is not corrupted")
        return 1
    import json as _json
    (HISTORY.parent / "source_health.json").write_text(
        _json.dumps({"date": today.isoformat(), "brokers": health}, indent=1))
    dead = [k for k, v in health.items() if not v["ok"]]
    if dead:
        log(f"::warning::sources failed this run: {', '.join(dead)} — "
            f"their listings are excluded from change detection")

    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    existing, header_ok = [], False
    if HISTORY.exists():
        with open(HISTORY, newline="") as f:
            r = csv.DictReader(f)
            header_ok = r.fieldnames == FIELDS
            if header_ok:
                existing = [x for x in r if x.get("date") != today.isoformat()]
    with open(HISTORY, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for x in existing:
            w.writerow(x)
        for x in rows:
            w.writerow({k: ("" if x.get(k) is None else x.get(k)) for k in FIELDS})
    log(f"wrote {len(rows)} rows for {today} ({len(existing)} historical rows kept"
        f"{'' if header_ok else '; old schema replaced'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
