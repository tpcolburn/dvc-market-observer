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
    # Desirability. Cost per point-year answers "what does this cost to carry",
    # not "would I enjoy owning it". Ranked resorts get a credit against their
    # cost, best-first. The curve is convex rather than linear because taste
    # falls off faster than rank does: linear spacing promoted a #6 resort into
    # ACT NOW purely because it started 17 cents away, which is not a preference
    # being expressed, just a coincidence being rewarded.
    "resort_rank": [],
    # Sized against the band width, not chosen freely: ACT NOW to NOTABLE spans
    # $0.70, so a $0.60 credit moves a top-tier resort almost a whole band and
    # drags its stripped contracts across with the clean ones. $0.40 promotes
    # the good ones and leaves the depleted ones behind.
    "desirability_premium_top": 0.40,
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
    """Brokers rarely publish closing, so this is fitted to the quotes we do
    have: $642 and $586 (DVC Store, 100pt), $668 (DVC495, 100pt). The old
    95 + 2*points gave $295 on a 100-pointer and understated every all-in
    cash figure in the digest by roughly $350 — enough to put a contract
    inside a budget it actually misses."""
    return 450 + 2 * points


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
    # None means the broker published no points breakdown. Scoring it as 0 is
    # what let a contract missing a full year read as unstripped; carry the
    # uncertainty forward instead so the caller can refuse to call it a buy.
    raw_delta = row.get("point_delta")
    surplus = 0 if raw_delta is None else raw_delta
    acq = row["price"] + closing_estimate(pts) + CAF - surplus * RENTAL_CREDIT
    return (acq / years + pts * dues) / pts + penalty, surplus


def desirability(cfg):
    """{resort: $/pt-yr credit}. Top tier gets the full premium, bottom gets
    zero, unranked resorts get zero — silence is neutrality, not dislike.

    resort_rank accepts either a flat list ("A", "B", ...) or tiers of genuine
    ties (["A", "B"], ["C"], ...). Ties matter: Travis rates Polynesian,
    Boulder Ridge and Copper Creek as one group, and forcing an order on them
    would invent a preference he does not hold.
    """
    rank = cfg.get("resort_rank") or []
    tiers = [t if isinstance(t, list) else [t] for t in rank]
    n = len(tiers)
    if n < 2:
        return {}
    top = cfg.get("desirability_premium_top", 0.0)
    out = {}
    for i, tier in enumerate(tiers):
        credit = round(top * (((n - i - 1) / (n - 1)) ** 2), 3)
        for resort in tier:
            out[resort] = credit
    return out


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
    prem = desirability(cfg)
    scored = []
    for r in rows:
        if r.get("status") in ("Sold", "Archived"):
            continue
        pts = r.get("points")
        if not pts or not (lo <= pts <= hi):
            continue
        pen = cfg["restriction_penalty"] if r.get("resort") in restricted else 0.0
        pen -= prem.get(r.get("resort"), 0.0)
        cpy, surplus = cost_per_point_year(r, today_year, pen)
        if cpy is None:
            continue
        rec = dict(r)
        rec["cpy"] = round(cpy, 2)
        rec["desirability"] = prem.get(r.get("resort"), 0.0)
        rec["points_unknown"] = r.get("point_delta") is None
        raw, _ = cost_per_point_year(r, today_year, 0.0)
        rec["raw_cpy"] = round(raw, 2)
        rec["surplus"] = surplus
        rec["cash"] = round(r["price"] + closing_estimate(pts) + CAF)
        # Sticker price lies when points are stripped or loaded. A $139/pt
        # contract missing a full year of points really costs $158/pt for what
        # you receive; a loaded one costs less than it asks. The cost/pt-yr
        # figure already credits this — the sticker override did not, and was
        # promoting stripped contracts to ACT NOW on asking price alone.
        rec["effective_ppp"] = round(
            (r["price"] - surplus * RENTAL_CREDIT) / pts, 2)
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
        eppp = rec["effective_ppp"]
        over = (cfg.get("sticker_override") or {}).get(resort)
        if rec["cpy"] <= th["act_now"] or (over and eppp <= over):
            band = "ACT NOW"
        elif rec["cpy"] <= th["watch"]:
            band = "WATCH"
        elif rec["cpy"] <= th["notable"]:
            band = "NOTABLE"

        # Never call a contract a definite buy when nobody has published what
        # points come with it — that is precisely how WLCC100-04-0817 read as
        # unstripped while missing an entire use year.
        if rec["points_unknown"] and band == "ACT NOW":
            band = "WATCH"

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

        if rec["points_unknown"]:
            notes.append("points breakdown not published — verify before offering")
        elif rec["surplus"] < 0:
            notes.append(f"stripped {abs(rec['surplus'])} pts "
                         f"→ ${rec['effective_ppp']:,.0f}/pt effective")
        elif rec["surplus"] > 0:
            notes.append(f"+{rec['surplus']} pts banked/current "
                         f"→ ${rec['effective_ppp']:,.0f}/pt effective")
        if resort in restricted:
            notes.append(f"resale-restricted (+${cfg['restriction_penalty']:.2f} penalty applied)")
        if rec["desirability"] >= 0.10:
            notes.append(f"{rec['raw_cpy']:.2f} before ${rec['desirability']:.2f} "
                         f"desirability credit")
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
