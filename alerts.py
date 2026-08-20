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
    "priority_resorts": ["Copper Creek Villas", "Boulder Ridge Villas",
                         "Animal Kingdom Villas", "Saratoga Springs"],
    "restricted_resorts": ["Riviera Resort", "Villas at Disneyland Hotel",
                           "Cabins at Fort Wilderness"],
    "point_band": [100, 225],
    "relative_alert": {"discount_vs_median": 0.06, "strong_discount": 0.10,
                       "min_sample": 4},
    "global_percentile": {"act_now": 0.01, "watch": 0.05, "notable": 0.15},
    # Resale points at a restricted resort book only that resort. Staying
    # elsewhere means renting your points out and renting others in, and the
    # round trip leaks: you are taxed on the income but get no deduction on the
    # personal travel. At ~50% of trips off-home, $19 out taxed ~24%, $22 in:
    #   0.50 x (22 - 19*0.76) = $3.78  -> $3.75
    # Sensitive almost entirely to the tax treatment; untaxed it is $1.00.
    "restriction_penalty": 3.75,
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


def cost_per_point_year(row, today_year, penalty=0.0):
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
    return (acq / years + pts * dues) / pts + penalty, surplus


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

    restricted = set(cfg["restricted_resorts"])
    scored = []
    for r in rows:
        if r.get("status") in ("Sold", "Archived"):
            continue
        pts = r.get("points")
        if not pts or not (lo <= pts <= hi):
            continue
        pen = cfg["restriction_penalty"] if r.get("resort") in restricted else 0.0
        cpy, surplus = cost_per_point_year(r, today_year, pen)
        if cpy is None:
            continue
        rec = dict(r)
        rec["cpy"] = round(cpy, 2)
        rec["surplus"] = surplus
        rec["cash"] = round(r["price"] + closing_estimate(pts) + CAF)
        rec["years_left"] = max(1, (r.get("deed_year") or 2050) - today_year)
        scored.append(rec)

    by_resort = {}
    for rec in scored:
        by_resort.setdefault(rec["resort"], []).append(rec["cpy"])
    minn = cfg["relative_alert"]["min_sample"]
    baselines = {k: percentile(v, 0.5) for k, v in by_resort.items() if len(v) >= minn}

    # One absolute band table for every resort. Cost per point-year already
    # normalises deed length and dues, so it compares resorts honestly; banding
    # each resort against its own median would rank a 15.82 BoardWalk above a
    # 12.40 Copper Creek purely for being less bad than its neighbours.
    th = cfg.get("thresholds_cost_per_point_year")
    if not th:
        allv = [r["cpy"] for r in scored]
        gp = cfg["global_percentile"]
        th = {k: percentile(allv, gp[k]) for k in ("act_now", "watch", "notable")}

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
        ppp = rec.get("price_per_point") or 0
        over = (cfg.get("sticker_override") or {}).get(resort)
        if rec["cpy"] <= th["act_now"] or (over and ppp and ppp <= over):
            band = "ACT NOW"
        elif rec["cpy"] <= th["watch"]:
            band = "WATCH"
        elif rec["cpy"] <= th["notable"]:
            band = "NOTABLE"

        # Cheap for its own resort is worth seeing but it is not a buy signal,
        # so it can raise a listing to NOTABLE and no further.
        if resort in baselines:
            disc = (baselines[resort] - rec["cpy"]) / baselines[resort]
            if disc >= cfg["relative_alert"]["discount_vs_median"]:
                if band == "PASS":
                    band = "NOTABLE"
                notes.append(f"{disc * 100:.0f}% below resort median")
        if band == "PASS":
            continue

        if rec["surplus"] < 0:
            notes.append(f"stripped {abs(rec['surplus'])} pts")
        elif rec["surplus"] > 0:
            notes.append(f"+{rec['surplus']} pts banked/current")
        if resort in restricted:
            notes.append(f"resale-restricted (+${cfg['restriction_penalty']:.2f} penalty applied)")
        if rec.get("status") == "Unverified":
            notes.append("status not published")
        # Most contracts exceed the cash budget, so "over" is not information.
        # The scarce, actionable fact is which ones he can actually pay for.
        budget = cfg.get("cash_budget")
        if budget and rec["cash"] <= budget:
            notes.append(f"within ${budget:,.0f} cash budget")

        rec["band"] = band
        rec["notes"] = notes
        alerts.append(rec)

    # Restricted resorts rarely survive the penalty, but Travis still wants eyes
    # on them — carried out separately rather than smuggled into the buy list.
    ref = {}
    for rec in scored:
        if rec["resort"] not in restricted:
            continue
        cur = ref.get(rec["resort"])
        if cur is None or rec["cpy"] < cur["cpy"]:
            raw, _ = cost_per_point_year(rec, today_year, 0.0)
            rec = dict(rec, raw_cpy=round(raw, 2))
            ref[rec["resort"]] = rec

    prio = {r: i for i, r in enumerate(cfg["priority_resorts"])}
    order = {"ACT NOW": 0, "WATCH": 1, "NOTABLE": 2}
    alerts.sort(key=lambda r: (order[r["band"]], prio.get(r["resort"], 99), r["cpy"]))
    return alerts, baselines, ref
