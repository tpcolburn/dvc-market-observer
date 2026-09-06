#!/usr/bin/env python3
"""Temporary diagnostic: dump the flattened text DVC Sales listing pages
produce, so the p_dvcsales() regexes in scrape_all.py can be corrected
against real current markup. Not part of the weekly pipeline — delete
once the fix lands.
"""
import re
from scrape_all import fetch, flat, UA

URLS = [
    # a listing where use_year/price_per_point came back wrong in the
    # 2026-09-06 snapshot (285pt SSR -> $9.19/pt, blank use year)
    "https://dvcsales.com/dvc-resale/saratoga-springs/285-points/96",
    # a listing that parsed correctly the same day, for comparison
    "https://dvcsales.com/dvc-resale/saratoga-springs/240-points/1291",
]

for url in URLS:
    print("=" * 100)
    print(url)
    print("=" * 100)
    doc = fetch(url)
    if doc is None:
        print("FETCH FAILED")
        continue
    t = flat(doc)
    print(f"[flattened length: {len(t)}]")
    # show text around every '$' and around 'Use Year' / 'Dues' / 'Annual'
    for label, pat in [
        ("USE YEAR", r".{80}[Uu]se\s*[Yy]ear.{80}"),
        ("DUES", r".{80}[Dd]ues.{80}"),
        ("ANNUAL", r".{80}[Aa]nnual.{80}"),
        ("DOLLAR SIGNS", r".{40}\$[\d,.]+.{40}"),
    ]:
        print(f"--- {label} ---")
        matches = re.findall(pat, t)
        for m in matches[:8]:
            print(repr(m))
        if not matches:
            print("(no match)")
    print()
