"""Personal buy-list scoring, folded into the daily observer run.

This used to live in a separate script on Travis's Mac that read this repo's
committed CSV. That arrangement had a failure mode we hit on 2026-08-20: the
local clone silently stopped pulling and the monitor scored the previous day's
market for days without saying so. Running here removes the sync entirely —
the rows scored below are the ones the scraper wrote minutes earlier.

The repo is public, so the buy criteria are NOT committed. They arrive through
the ALERT_CONFIG secret as JSON. Without it we fall back to percentile bands,
which reveal nothing about what anyone is willing to pay.
"""
import json, os
from datetime import date

CAF = 500                     # Disney Contract Administration Fee, charged per resale contract
RENTAL_CREDIT = 19            # $/point that banked points are worth if rented out

# Non-sensitive defaults. Which resorts someone watches is mild; what they will
# pay is not, so no thresholds or budgets live in this file.
DEFAULTS = {
    "focus_resorts": ["Copper Creek Villas"],
    "priority_resorts": ["Copper Creek Villas", "Boulder Ridge Villas",
                         "Animal Kingdom Villas", "Saratoga Springs"],
    "restricted_resorts": ["Riviera Resort", "Villas at Disneyland Hotel",
                           "Cabins at Fort Wilderness"],
    "point_band": [100, 225],
    "relative_alert": {"discount_vs_median": 0.06, "strong_discount": 0.10,
                       "min_sample": 4},
    "focus_percentile": {"act_now": 0.10, "watch": 0.25, "notable": 0.40},
    "thresholds_cost_per_point_year": None,   # supplied via ALERT_CONFIG
    "sticker_override": {},
    "cash_budget": None,
    "lock": {"active": False},
}


def load_config():
    cfg = dict(DEFAULTS)
    raw = os.environ.get("ALERT_CONFIG", "").strip()
    if raw:
        try:
            cfg.update(json.loads(raw))
        except json.JSONDecodeError as exc:
            print(f"::warning::ALERT_CONFIG is not valid JSON ({exc}); using defaults")
    return cfg


def closing_estimate(points):
    """Brokers rarely publish closing. This bracket matches what the three that
    do have quoted all year; DVC495 lists $668 on a 100-pointer, we get $695."""
    return 95 + 2 * points


def cost_per_point_year(row, today_year):
    """Dues plus acquisition amortized over the remaining deed, with banked
    points credited and stripped points charged. The only figure that compares
    a 2068 deed against a 2042 one honestly."""
    pts = row["points"]
    if not pts or not row.get("price"):
        return None, 0
    deed = row.get("deed_year") or 2050
    years = max(1, deed - today_year)
    dues = row.get("dues_per_point") or 9.5
    surplus = row.get("point_delta") or 0
    acq = row["price"] + closing_estimate(pts) + CAF - surplus * RENTAL_CREDIT
    return (acq / years + pts * dues) / pts, surplus


def percentile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    i = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[i]


def score_all(rows, cfg, today_year=None):
    """Returns (alerts, baselines). Alerts are sorted best-first."""
    today_year = today_year or date.today().year
    lo, hi = cfg["point_band"]
    lock = cfg.get("lock") or {}

    scored = []
    for r in rows:
        if r.get("status") in ("Sold", "Archived"):
            continue
        pts = r.get("points")
        if not pts or not (lo <= pts <= hi):
            continue
        cpy, surplus = cost_per_point_year(r, today_year)
        if cpy is None:
            continue
        rec = dict(r)
        rec["cpy"] = round(cpy, 2)
        rec["surplus"] = surplus
        rec["cash"] = round(r["price"] + closing_estimate(pts) + CAF)
        rec["years_left"] = max(1, (r.get("deed_year") or 2050) - today_year)
        scored.append(rec)

    # median cost/pt-yr per resort, among in-band listings, as the yardstick for
    # everywhere that is not a focus resort
    by_resort = {}
    for rec in scored:
        by_resort.setdefault(rec["resort"], []).append(rec["cpy"])
    minn = cfg["relative_alert"]["min_sample"]
    baselines = {k: percentile(v, 0.5) for k, v in by_resort.items() if len(v) >= minn}

    # focus bands: explicit thresholds if configured, else percentiles of the
    # resort's own live distribution
    th = cfg.get("thresholds_cost_per_point_year")
    focus_bands = {}
    for f in cfg["focus_resorts"]:
        vals = by_resort.get(f, [])
        if th:
            focus_bands[f] = th
        elif len(vals) >= minn:
            p = cfg["focus_percentile"]
            focus_bands[f] = {k: percentile(vals, p[k]) for k in ("act_now", "watch", "notable")}

    alerts = []
    for rec in scored:
        resort = rec["resort"]
        if lock.get("active"):
            # once contract #1 closes only an exact resort + use-year match pools with it
            if resort != lock.get("resort") or (lock.get("use_year")
                                                and rec.get("use_year") != lock["use_year"]):
                continue

        notes = []
        band = "PASS"
        if resort in focus_bands:
            b = focus_bands[resort]
            ppp = rec.get("price_per_point") or 0
            over = (cfg.get("sticker_override") or {}).get(resort)
            if rec["cpy"] <= b["act_now"] or (over and ppp and ppp <= over):
                band = "ACT NOW"
            elif rec["cpy"] <= b["watch"]:
                band = "WATCH"
            elif rec["cpy"] <= b["notable"]:
                band = "NOTABLE"
        elif resort in baselines:
            base = baselines[resort]
            disc = (base - rec["cpy"]) / base
            ra = cfg["relative_alert"]
            if disc >= ra["strong_discount"]:
                band = "WATCH"
            elif disc >= ra["discount_vs_median"]:
                band = "NOTABLE"
            if band != "PASS":
                notes.append(f"{disc * 100:.0f}% below resort median")
        if band == "PASS":
            continue

        if rec["surplus"] < 0:
            notes.append(f"stripped {abs(rec['surplus'])} pts")
        elif rec["surplus"] > 0:
            notes.append(f"+{rec['surplus']} pts banked/current")
        if resort in cfg["restricted_resorts"]:
            notes.append("resale-restricted")
        if rec.get("status") == "Unverified":
            notes.append("status not published")
        budget = cfg.get("cash_budget")
        if budget and rec["cash"] > budget:
            notes.append("over cash budget")

        rec["band"] = band
        rec["notes"] = notes
        alerts.append(rec)

    prio = {r: i for i, r in enumerate(cfg["priority_resorts"])}
    order = {"ACT NOW": 0, "WATCH": 1, "NOTABLE": 2}
    alerts.sort(key=lambda r: (order[r["band"]], prio.get(r["resort"], 99), r["cpy"]))
    return alerts, baselines
