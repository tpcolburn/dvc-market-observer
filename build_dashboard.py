#!/usr/bin/env python3
"""
Turn data/listings_history.csv into index.html and the email body.

Everything visible is rendered here as real HTML — mail clients and no-JS
contexts get the complete page. JavaScript only adds sorting and filtering.
"""

import csv, html, json, sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import median

BASE = Path(__file__).resolve().parent
HISTORY = BASE / "data" / "listings_history.csv"
OUT = BASE / "index.html"
SUMMARY = BASE / "summary.txt"
SUMMARY_HTML = BASE / "summary.html"
HEALTH = BASE / "data" / "source_health.json"

# Resorts read line-by-line; everything else is summarised.
FOCUS = ["Copper Creek Villas", "Boulder Ridge Villas",
         "Animal Kingdom Villas", "Saratoga Springs"]

# Resale points at these resorts can only book that same resort.
RESTRICTED = {"Riviera Resort", "Villas at Disneyland Hotel", "Cabins at Fort Wilderness"}

# The 2026-01-10 rows are a sparse archive from the original scraper — a useful
# baseline for "vs January" deltas, but plotting them on a daily-trend axis puts
# a 7-month gap next to a 1-day gap. Charts start at the continuous window.
CHART_START = "2026-08-01"

SOLD_LIKE = {"Sold"}
UNDER_OFFER = {"Offer Accepted", "Sale Pending", "Under Contract"}
NUM = {"points", "price", "price_per_point", "dues_per_point", "deed_year",
       "point_delta", "cost_per_point_year"}

e = html.escape


def load():
    if not HISTORY.exists():
        sys.exit("no history file — run scrape_all.py first")
    rows = []
    with open(HISTORY, newline="") as f:
        for r in csv.DictReader(f):
            for k in NUM:
                v = (r.get(k) or "").strip()
                r[k] = (float(v) if "." in v else int(v)) if v else None
            if r.get("points") and r.get("price_per_point"):
                rows.append(r)
    return rows


def key(r):
    return f"{r['broker']}|{r['listing_id']}"


def load_health():
    if HEALTH.exists():
        try:
            return json.loads(HEALTH.read_text()).get("brokers", {})
        except Exception:
            pass
    return {}


def diff(rows, latest, prev, dead=()):
    """Changes between the two most recent snapshots. Brokers whose scrape
    failed are skipped entirely so their inventory doesn't read as withdrawn."""
    now = {key(r): r for r in rows if r["date"] == latest and r["broker"] not in dead}
    was = ({key(r): r for r in rows if r["date"] == prev and r["broker"] not in dead}
           if prev else {})
    out = dict(new=[], accepted=[], sold=[], drops=[], relisted=[], gone=[])
    for k, r in now.items():
        old = was.get(k)
        if not old:
            if r["status"] not in SOLD_LIKE:
                out["new"].append(r)
            continue
        if old["status"] not in UNDER_OFFER and r["status"] in UNDER_OFFER:
            out["accepted"].append({**r, "_from": old["status"]})
        elif old["status"] not in SOLD_LIKE and r["status"] in SOLD_LIKE:
            out["sold"].append(r)
        elif old["status"] in (UNDER_OFFER | SOLD_LIKE) and r["status"] == "Available":
            out["relisted"].append({**r, "_from": old["status"]})
        if (old["price_per_point"] and r["price_per_point"]
                and r["price_per_point"] < old["price_per_point"]):
            out["drops"].append({**r, "_was": old["price_per_point"]})
    for k, r in was.items():
        if k not in now and r["status"] not in SOLD_LIKE:
            out["gone"].append(r)
    for v in out.values():
        v.sort(key=lambda r: (r["cost_per_point_year"] is None, r["cost_per_point_year"] or 0))
    return out


def money(v, d=0):
    return "—" if v is None else f"${v:,.{d}f}"


def listing_rows(items, extra=None, status_col=False):
    out = []
    for r in items:
        focus = ' <span class="dot"></span>' if r["resort"] in FOCUS else ""
        badge = ' <span class="tag r">restricted</span>' if r["resort"] in RESTRICTED else ""
        note = ""
        if extra == "drop":
            note = f' <span class="was">was ${r["_was"]:,.0f}</span>'
        elif extra == "from":
            note = f' <span class="was">{e(r["_from"])} → {e(r["status"])}</span>'
        delta = r["point_delta"] or 0
        dtxt = (f'<span class="pos">+{delta}</span>' if delta > 0
                else f'<span class="neg">{delta}</span>' if delta < 0 else "—")
        url = r.get("url") or ""
        name = f'<a href="{e(url)}">{e(r["resort"])}</a>' if url else e(r["resort"])
        status_td = f'<td class="n mh">{e(r["status"] or "—")}</td>' if status_col else ""
        out.append(f"""<tr>
<td class="c">{name}{focus}{badge}<div class="sub">{e(r['broker'])} · {e(r['listing_id'])} · {e(r['use_year'] or '?')} UY{note}</div></td>
<td class="n">{r['points']}</td>
<td class="n"><b>{money(r['price_per_point'])}</b></td>
<td class="n mh">{money(r['price'])}</td>
<td class="n"><b>{money(r['cost_per_point_year'],2)}</b></td>
<td class="n mh">{r['deed_year'] or '—'}</td>
<td class="n mh">{dtxt}</td>{status_td}</tr>""")
    return "\n".join(out)


def thead(status_col=False):
    s = '<th class="n mh">Status</th>' if status_col else ""
    return ('<tr><th class="c">Contract</th><th class="n">Pts</th><th class="n">$/pt</th>'
            '<th class="n mh">Price</th><th class="n">$/pt-yr</th><th class="n mh">Deed</th>'
            f'<th class="n mh">Banked</th>{s}</tr>')


def section(title, blurb, items, extra=None, cls="", collapsed=False):
    n = len(items)
    if not n:
        return (f'<h2 class="{cls}">{e(title)} <span class="count">0</span></h2>'
                '<p class="none">Nothing today.</p>')
    body = (f'<p class="blurb">{e(blurb)}</p>'
            f'<div class="scroll"><table><thead>{thead()}</thead>'
            f'<tbody>{listing_rows(items, extra)}</tbody></table></div>')
    if collapsed:
        return (f'<details class="fold"><summary><h2 class="{cls} inl">{e(title)} '
                f'<span class="count">{n}</span></h2></summary>{body}</details>')
    return f'<h2 class="{cls}">{e(title)} <span class="count">{n}</span></h2>{body}'


PAL = ["#4493f8", "#3fb950", "#d29922", "#db6d28"]   # one per focus resort, stable


def svg_chart(series, dates, yfmt="${v:.0f}"):
    """Focus resorts drawn in color; every other resort as a thin gray line.
    Time-scaled x. Rendered inside a horizontal-scroll container so axis text
    stays readable on a phone instead of scaling down."""
    days = [d for d in dates if d >= CHART_START]
    names = [k for k in series if sum(1 for d in days if d in series[k]) > 1]
    if len(days) < 2 or not names:
        return '<p class="none">Trend lines need at least two days of history.</p>'
    W, H, P = 760, 270, dict(t=12, r=14, b=26, l=48)
    o = [date.fromisoformat(d).toordinal() for d in days]
    lo_x, hi_x = min(o), max(o)
    vals = [v for n in names for d, v in series[n].items() if d in days]
    lo, hi = min(vals) * .95, max(vals) * 1.05
    if hi - lo < 1e-9:
        hi = lo + 1
    X = lambda d: P["l"] + (date.fromisoformat(d).toordinal() - lo_x) * (W - P["l"] - P["r"]) / max(1, hi_x - lo_x)
    Y = lambda v: P["t"] + (hi - v) * (H - P["t"] - P["b"]) / (hi - lo)
    g = [f'<svg viewBox="0 0 {W} {H}" class="chart">']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        g.append(f'<line x1="{P["l"]}" y1="{Y(v):.1f}" x2="{W-P["r"]}" y2="{Y(v):.1f}" class="grid"/>'
                 f'<text x="{P["l"]-6}" y="{Y(v)+4:.1f}" class="ax" text-anchor="end">{yfmt.format(v=v)}</text>')
    for d in days:
        g.append(f'<text x="{X(d):.1f}" y="{H-7}" class="ax" text-anchor="middle">{d[5:]}</text>')
    legend = []
    order = [n for n in names if n not in FOCUS] + [f for f in FOCUS if f in names]
    for n in order:
        pts = [f"{X(d):.1f},{Y(series[n][d]):.1f}" for d in days if d in series[n]]
        if len(pts) < 2:
            continue
        if n in FOCUS:
            c = PAL[FOCUS.index(n) % len(PAL)]
            g.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{c}" stroke-width="2.5"/>')
            x, y = pts[-1].split(",")
            g.append(f'<circle cx="{x}" cy="{y}" r="3" fill="{c}"/>')
        else:
            g.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="currentColor" '
                     'stroke-opacity=".18" stroke-width="1"/>')
    for f in FOCUS:
        if f in names:
            c = PAL[FOCUS.index(f) % len(PAL)]
            legend.append(f'<span><i style="background:{c}"></i>{e(f)}</span>')
    legend.append('<span><i class="gray"></i>all other resorts</span>')
    return ('<div class="chartwrap">' + "".join(g) + "</svg></div>"
            + f'<div class="legend">{"".join(legend)}</div>')


def build(rows):
    dates = sorted({r["date"] for r in rows})
    latest = dates[-1]
    prev = dates[-2] if len(dates) > 1 else None
    health = load_health()
    dead = {b for b, v in health.items() if not v.get("ok")}
    ch = diff(rows, latest, prev, dead)
    live = [r for r in rows if r["date"] == latest and r["status"] not in SOLD_LIKE]

    # per-resort daily series: median $/pt and inventory on market
    ppp_series, inv_series = defaultdict(dict), defaultdict(dict)
    for d in dates:
        by = defaultdict(list)
        for r in rows:
            if r["date"] == d and r["status"] not in SOLD_LIKE:
                by[r["resort"]].append(r)
        for resort, rs in by.items():
            p = [x["price_per_point"] for x in rs if x["price_per_point"]]
            if len(p) >= 2:
                ppp_series[resort][d] = round(median(p), 2)
            inv_series[resort][d] = len(rs)

    # January baseline for the ranking table
    jan = {}
    for resort in set(r["resort"] for r in rows if r["date"] == dates[0]):
        p = [r["price_per_point"] for r in rows
             if r["date"] == dates[0] and r["resort"] == resort and r["price_per_point"]]
        if len(p) >= 2:
            jan[resort] = median(p)

    by_resort = defaultdict(list)
    for r in live:
        by_resort[r["resort"]].append(r)
    summary = []
    for resort, rs in by_resort.items():
        ppp = [r["price_per_point"] for r in rs if r["price_per_point"]]
        cpy = [r["cost_per_point_year"] for r in rs if r["cost_per_point_year"]]
        deeds = [r["deed_year"] for r in rs if r["deed_year"]]
        dues = [r["dues_per_point"] for r in rs if r["dues_per_point"]]
        med = round(median(ppp), 2) if ppp else None
        prev_inv = inv_series[resort].get(prev) if prev else None
        summary.append(dict(
            resort=resort, n=len(rs), restricted=resort in RESTRICTED,
            focus=resort in FOCUS,
            inv_delta=(len(rs) - prev_inv) if prev_inv is not None else None,
            ppp=med,
            jan_delta=(round(med - jan[resort], 2) if med and resort in jan else None),
            cpy=round(median(cpy), 2) if cpy else None,
            deed=max(set(deeds), key=deeds.count) if deeds else None,
            dues=round(median(dues), 4) if dues else None))
    summary.sort(key=lambda s: (s["cpy"] is None, s["cpy"] or 0))

    _sr = []
    for s in summary:
        tag = ' <span class="tag r">restricted</span>' if s["restricted"] else ""
        dot = ' <span class="dot"></span>' if s["focus"] else ""
        if s["inv_delta"] is None:
            inv = str(s["n"])
        elif s["inv_delta"] == 0:
            inv = f'{s["n"]} <span class="was">±0</span>'
        else:
            cls = "pos" if s["inv_delta"] > 0 else "neg"
            inv = f'{s["n"]} <span class="{cls}">{s["inv_delta"]:+d}</span>'
        if s["jan_delta"] is None:
            janc = "—"
        else:
            cls = "neg" if s["jan_delta"] > 0 else "pos"
            janc = f'<span class="{cls}">{s["jan_delta"]:+,.0f}</span>'
        _sr.append("<tr><td class=\"c\">" + e(s["resort"]) + dot + tag + "</td>"
                   + '<td class="n">' + inv + "</td>"
                   + '<td class="n">' + money(s["ppp"]) + "</td>"
                   + '<td class="n mh">' + janc + "</td>"
                   + '<td class="n mh">' + money(s["dues"], 2) + "</td>"
                   + '<td class="n mh">' + (str(s["deed"]) if s["deed"] else "—") + "</td>"
                   + '<td class="n"><b>' + money(s["cpy"], 2) + "</b></td></tr>")
    srows = "\n".join(_sr)

    unres = [s for s in summary if s["cpy"] and not s["restricted"]]
    cards = [("Live listings", f"{len(live):,}"), ("New today", str(len(ch["new"]))),
             ("Under offer", str(len(ch["accepted"]))), ("Price drops", str(len(ch["drops"]))),
             ("Best unrestricted", money(unres[0]["cpy"], 2) if unres else "—",
              unres[0]["resort"] if unres else "")]
    _ch = []
    for c in cards:
        sub = ('<div class="s">' + e(c[2]) + "</div>") if len(c) > 2 and c[2] else ""
        _ch.append('<div class="card"><div class="l">' + e(c[0]) + "</div>"
                   + '<div class="v">' + c[1] + "</div>" + sub + "</div>")
    cardhtml = "".join(_ch)

    if dead:
        detail = "; ".join(f'{b}: {health[b]["parsed"]}/{health[b]["targets"]} parsed'
                           for b in sorted(dead))
        banner = ('<div class="alert"><b>Incomplete sweep.</b> ' + e(detail)
                  + " — these brokers' listings are missing from today's snapshot and are "
                    "excluded from the change sections, so nothing here is a real withdrawal "
                    "for them.</div>")
    else:
        banner = ""

    chips = "".join(f'<button class="chip" data-q="{e(f)}">{e(f.replace(" Villas",""))}</button>'
                    for f in FOCUS) + '<button class="chip" data-q="">All</button>'

    page = TEMPLATE.format(
        banner=banner, latest=latest, prev=prev or "—", ndates=len(dates), nrows=len(rows),
        cards=cardhtml,
        new=section("Newly listed", "On the market since the previous sweep.", ch["new"]),
        accepted=section("New offers accepted", "Moved to Offer Accepted, Sale Pending or Under Contract.",
                         ch["accepted"], "from", "warn"),
        relisted=section("Back on the market", "Returned to Available after being under offer — failed "
                         "ROFR or financing. Often the best prices.", ch["relisted"], "from", "good"),
        drops=section("Price drops", "Asking price per point fell since the previous sweep.",
                      ch["drops"], "drop", "good"),
        sold=section("Sold", "Left the market as sold.", ch["sold"], collapsed=True),
        gone=section("Withdrawn", "Present in the previous sweep, absent now, not marked sold.",
                     ch["gone"], collapsed=True),
        ppp_chart=svg_chart(ppp_series, dates, "${v:.0f}"),
        inv_chart=svg_chart(inv_series, dates, "{v:.0f}"),
        srows=srows, chips=chips,
        alltable=f'<div class="scroll tall"><table id="all"><thead>{thead(True)}</thead>'
                 f'<tbody>{listing_rows(sorted(live, key=lambda r: (r["cost_per_point_year"] is None, r["cost_per_point_year"] or 0)), status_col=True)}</tbody></table></div>',
        nlive=len(live))
    OUT.write_text(page)

    write_email(ch, live, dates, prev, dead, health)
    return dict(live=len(live), dates=len(dates), rows=len(rows),
                **{k: len(v) for k, v in ch.items()})


ACCENT = {"new": "#0969da", "accepted": "#9a6700", "drops": "#1a7f37"}
LABEL = {"new": "New listings", "accepted": "Offers accepted", "drops": "Price drops"}


def _card(r, kind):
    """One listing, stacked rather than tabular — a phone has no room for columns."""
    bits = []
    if kind == "drops":
        bits.append('<span style="color:#1a7f37;font-weight:600">${:,.0f}/pt</span>'
                    ' <span style="color:#8b949e;text-decoration:line-through">${:,.0f}</span>'
                    .format(r["price_per_point"], r["_was"]))
    else:
        bits.append('<span style="font-weight:600">${:,.0f}/pt</span>'.format(r["price_per_point"]))
    line2 = "{} &middot; {}".format(money(r["price"]),
                                    money(r["cost_per_point_year"], 2) + "/pt-yr"
                                    if r["cost_per_point_year"] else "no score")
    meta = "{} &middot; {}".format(e(r["broker"]), e(r["listing_id"]))
    if kind == "accepted":
        meta = "{} &rarr; {} &middot; {}".format(e(r["_from"]), e(r["status"]), meta)
    if (r["point_delta"] or 0) != 0:
        d = r["point_delta"]
        line2 += ' &middot; <span style="color:{}">{}{} pts</span>'.format(
            "#1a7f37" if d > 0 else "#b35900", "+" if d > 0 else "", d)
    head = "{} pts &middot; {} UY &middot; {}".format(r["points"], e(r["use_year"] or "?"), bits[0])
    url = r.get("url") or ""
    if url:
        head = '<a href="{}" style="color:#0969da;text-decoration:none">{}</a>'.format(e(url), head)
    return (
        '<div style="border-left:3px solid {};background:#f6f8fa;border-radius:0 6px 6px 0;'
        'padding:9px 11px;margin:7px 0">'
        '<div style="font-size:15px;line-height:1.35">{}</div>'
        '<div style="font-size:13px;color:#57606a;margin-top:2px">{}</div>'
        '<div style="font-size:11.5px;color:#8b949e;margin-top:2px">{}</div>'
        "</div>"
    ).format(ACCENT[kind], head, line2, meta)


def write_email(ch, live, dates, prev, dead, health):
    latest = dates[-1]
    kinds = ["new", "accepted", "drops"]
    focus_rows = {f: {k: [r for r in ch[k] if r["resort"] == f] for k in kinds} for f in FOCUS}
    other = {k: [r for r in ch[k] if r["resort"] not in FOCUS] for k in kinds}

    H = ['<div style="margin:0;padding:0;background:#ffffff">',
         '<div style="max-width:620px;margin:0 auto;padding:16px 14px;'
         'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',system-ui,sans-serif;'
         'color:#1f2328;background:#ffffff">']
    H.append('<div style="font-size:19px;font-weight:650">DVC Resale Market</div>')
    H.append('<div style="font-size:13px;color:#57606a;margin-top:2px">{} &middot; vs {} &middot; '
             '{:,} live listings</div>'.format(latest, prev or "n/a", len(live)))

    if dead:
        detail = "; ".join("{}: {}/{}".format(b, health[b]["parsed"], health[b]["targets"] or 0)
                           for b in sorted(dead))
        H.append('<div style="border:1px solid #b35900;border-left:4px solid #b35900;'
                 'background:#fff8f0;border-radius:6px;padding:10px 12px;margin:14px 0;font-size:13px">'
                 '<b>Incomplete sweep.</b> {} failed to scrape ({}). Their listings are missing '
                 "today and excluded from everything below.</div>".format(", ".join(sorted(dead)), e(detail)))

    tot = {k: len(ch[k]) for k in kinds}
    H.append('<div style="margin:14px 0 4px;font-size:13px;color:#57606a">'
             '<b style="color:#1f2328">{}</b> new &nbsp;&middot;&nbsp; <b style="color:#1f2328">{}</b> '
             "offers accepted &nbsp;&middot;&nbsp; <b style=\"color:#1f2328\">{}</b> price drops"
             "</div>".format(tot["new"], tot["accepted"], tot["drops"]))

    for f in FOCUS:
        blocks = focus_rows[f]
        n = sum(len(v) for v in blocks.values())
        H.append('<div style="margin-top:22px;padding-top:12px;border-top:2px solid #1f2328">'
                 '<span style="font-size:16px;font-weight:650">{}</span>'
                 '<span style="font-size:12px;color:#8b949e"> &nbsp;{} change{}</span></div>'
                 .format(e(f), n, "" if n == 1 else "s"))
        if not n:
            H.append('<div style="font-size:13px;color:#8b949e;padding:6px 0">No movement today.</div>')
            continue
        for k in kinds:
            items = blocks[k]
            if not items:
                continue
            H.append('<div style="font-size:11.5px;font-weight:650;letter-spacing:.05em;'
                     'text-transform:uppercase;color:{};margin:12px 0 2px">{} ({})</div>'
                     .format(ACCENT[k], LABEL[k], len(items)))
            for r in items:                      # every one, no truncation
                H.append(_card(r, k))

    on = sum(len(v) for v in other.values())
    H.append('<div style="margin-top:22px;padding-top:12px;border-top:2px solid #1f2328">'
             '<span style="font-size:16px;font-weight:650">All other resorts</span>'
             '<span style="font-size:12px;color:#8b949e"> &nbsp;{} change{}</span></div>'
             .format(on, "" if on == 1 else "s"))
    if on:
        H.append('<div style="font-size:13.5px;color:#57606a;line-height:1.9;padding:6px 0">')
        for k in kinds:
            if other[k]:
                best = other[k][0]
                H.append('<div><b style="color:#1f2328">{}</b> {} &nbsp;<span style="color:#8b949e">'
                         "best: {} {}pt ${:,.0f}/pt</span></div>"
                         .format(len(other[k]), LABEL[k].lower(), e(best["resort"]),
                                 best["points"], best["price_per_point"]))
        H.append("</div>")
    else:
        H.append('<div style="font-size:13px;color:#8b949e;padding:6px 0">No movement today.</div>')

    H.append('<div style="margin-top:24px;padding-top:14px;border-top:1px solid #e3e6ea;'
             'font-size:13px"><a href="https://tpcolburn.github.io/dvc-market-observer/" '
             'style="color:#0969da;text-decoration:none">Open the full dashboard &rarr;</a>'
             '<div style="font-size:11.5px;color:#8b949e;margin-top:8px;line-height:1.6">'
             "Sorted by cost per point-year within each group. Attached dashboard has every listing, "
             "trends and the resort ranking.</div></div>")
    H.append("</div></div>")
    SUMMARY_HTML.write_text("\n".join(H))

    # plain-text alternative, short lines so phones do not wrap them
    T = []
    if dead:
        T += ["!! INCOMPLETE SWEEP - " + ", ".join(sorted(dead)) + " failed to scrape.", ""]
    T += ["DVC Resale Market - {}".format(latest),
          "{} new | {} offers accepted | {} price drops".format(tot["new"], tot["accepted"], tot["drops"]),
          "{:,} live listings".format(len(live)), ""]
    for f in FOCUS:
        blocks = focus_rows[f]
        T.append(f.upper())
        if not sum(len(v) for v in blocks.values()):
            T += ["  no movement", ""]
            continue
        for k in kinds:
            for r in blocks[k]:
                extra = ("was ${:,.0f}".format(r["_was"]) if k == "drops"
                         else r["status"] if k == "accepted" else "")
                T.append("  [{}] {}pt {} ${:,.0f}/pt {}".format(
                    LABEL[k][:3].lower(), r["points"], (r["use_year"] or "?")[:3],
                    r["price_per_point"], extra).rstrip())
        T.append("")
    T.append("ALL OTHERS")
    for k in kinds:
        if other[k]:
            T.append("  {} {}".format(len(other[k]), LABEL[k].lower()))
    T += ["", "https://tpcolburn.github.io/dvc-market-observer/"]
    SUMMARY.write_text("\n".join(T))


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DVC Resale Market</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1f2328;--muted:#6b7280;--line:#e3e6ea;
--accent:#0969da;--good:#1a7f37;--warn:#9a6700;--bad:#b35900}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0d1117;--card:#161b22;--ink:#e6edf3;
--muted:#9198a1;--line:#30363d;--accent:#4493f8;--good:#3fb950;--warn:#d29922;--bad:#db6d28}}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:20px;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}}
.wrap{{max-width:1200px;margin:0 auto}}
h1{{font-size:21px;margin:0 0 3px}}
h2{{font-size:15px;margin:30px 0 3px;border-top:1px solid var(--line);padding-top:20px}}
h2.inl{{display:inline;border:0;padding:0;margin:0}}
h2.good{{color:var(--good)}} h2.warn{{color:var(--warn)}}
.count{{font-size:12px;color:var(--muted);font-weight:400}}
.sub{{color:var(--muted);font-size:12px;margin-top:1px}}
.blurb,.none{{color:var(--muted);font-size:12.5px;margin:2px 0 10px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:18px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:11px 12px}}
.card .l{{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
.card .v{{font-size:20px;font-weight:650;margin-top:3px}}
.card .s{{font-size:11px;color:var(--muted)}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:var(--card)}}
th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;
position:sticky;top:0;background:var(--card);cursor:pointer;white-space:nowrap;z-index:1}}
td.n,th.n{{text-align:right;white-space:nowrap}}
td.c{{min-width:185px}}
.scroll{{overflow:auto;border:1px solid var(--line);border-radius:9px;max-height:460px}}
.scroll.tall{{max-height:700px}}
a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
.tag{{font-size:10px;padding:1px 6px;border-radius:20px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}}
.tag.r{{border-color:var(--bad);color:var(--bad)}}
.dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--accent);
vertical-align:1px;margin-left:3px}}
.was{{font-size:11px;color:var(--muted)}}
.pos{{color:var(--good)}} .neg{{color:var(--bad)}}
.chartwrap{{overflow-x:auto;border:1px solid var(--line);border-radius:9px;background:var(--card);padding:8px}}
.chart{{min-width:640px;width:100%;height:auto;color:var(--ink);display:block}}
.grid{{stroke:currentColor;stroke-opacity:.13}}
.ax{{font-size:12px;fill:currentColor;opacity:.55}}
.legend{{display:flex;gap:12px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin-top:8px}}
.legend i{{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}}
.legend i.gray{{background:currentColor;opacity:.25}}
.ctrl{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0;align-items:center}}
input{{background:var(--card);color:var(--ink);border:1px solid var(--line);
border-radius:7px;padding:6px 9px;font:inherit;font-size:13px}}
.chip{{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:20px;
padding:4px 11px;font:inherit;font-size:12px;cursor:pointer}}
.chip:hover{{border-color:var(--accent);color:var(--accent)}}
details.fold{{margin-top:26px;border-top:1px solid var(--line);padding-top:18px}}
details.fold summary{{cursor:pointer;list-style:none}}
details.fold summary::before{{content:"▸ ";color:var(--muted)}}
details.fold[open] summary::before{{content:"▾ "}}
details.fold[open] summary{{margin-bottom:8px}}
.alert{{background:var(--card);border:1px solid var(--bad);border-left:4px solid var(--bad);
border-radius:9px;padding:12px;margin:16px 0;font-size:13px;line-height:1.6}}
.note{{background:var(--card);border:1px solid var(--line);border-radius:9px;
padding:13px;margin-top:26px;color:var(--muted);font-size:12px;line-height:1.65}}
th.sorted-a::after{{content:" ▲";font-size:9px}}
th.sorted-d::after{{content:" ▼";font-size:9px}}
@media (max-width:640px){{
  body{{padding:12px}}
  .mh{{display:none}}
  td.c{{min-width:150px}}
  h1{{font-size:19px}}
  .card .v{{font-size:18px}}
  th,td{{padding:6px 7px}}
}}
</style></head><body><div class="wrap">
<h1>DVC Resale Market</h1>
<div class="blurb">Snapshot {latest} · compared with {prev} · {ndates} days, {nrows:,} rows</div>
{banner}<div class="cards">{cards}</div>
{new}{accepted}{relisted}{drops}
<h2>Listings on the market</h2>
<p class="blurb">Inventory per resort per day — how fast contracts arrive versus get absorbed.
<span class="dot"></span> focus resorts in color; a dip can also mean a broker failed to scrape that day.</p>
{inv_chart}
<h2>Median price per point</h2>
<p class="blurb">Resorts with at least two listings that day. Sticker price only — it ignores dues and
deed length. The January 2026 baseline appears as the "vs Jan" column below rather than on this axis.</p>
{ppp_chart}
<h2>Resorts ranked by carrying cost</h2>
<p class="blurb">Cost per point-year = (price + closing + $500 Disney CAF − banked points at $19)
÷ years left on deed, plus annual dues, ÷ points. Inventory column shows change versus the previous day.</p>
<div class="scroll"><table><thead><tr><th class="c">Resort</th><th class="n">Inventory</th>
<th class="n">Median $/pt</th><th class="n mh">vs Jan</th><th class="n mh">Dues/pt</th>
<th class="n mh">Deed</th><th class="n">$/pt-yr</th></tr></thead><tbody>{srows}</tbody></table></div>
{sold}{gone}
<h2>All live listings <span class="count">{nlive}</span></h2>
<div class="ctrl"><input id="q" placeholder="Search resort, use year, broker, ID…" style="flex:1;min-width:160px">{chips}</div>
{alltable}
<div class="note">
<span class="dot"></span> marks the focus resorts (Copper Creek, Boulder Ridge, Animal Kingdom, Saratoga Springs).<br>
<b>Resale-restricted</b> resorts (Riviera, Villas at Disneyland Hotel, Cabins at Fort Wilderness) score well
<i>because</i> their resale points can only book that one resort — treat their ranking as a warning.<br>
<b>ROFR is not modelled</b> — Disney can take an aggressively priced contract regardless of its score.<br>
<b>DVC Sales</b> publishes no machine-readable status, so its rows show <i>Unverified</i>.<br>
Sources: DVC Resale Market, The DVC Store, DVC Sales, via published listing sitemaps. Fidelity Real Estate
and DVC Resale Experts are not included. On phones, tap any row's link for the full listing; hidden
columns (price, deed, banked) are on the broker page and the desktop view.
</div></div>
<script>
document.querySelectorAll("table").forEach(function(t){{
  t.querySelectorAll("th").forEach(function(th,i){{
    th.onclick=function(){{
      var tb=t.tBodies[0],rows=[].slice.call(tb.rows),d=th.dataset.d==="1"?-1:1;
      th.dataset.d=d===1?"1":"";
      t.querySelectorAll("th").forEach(function(o){{o.classList.remove("sorted-a","sorted-d")}});
      th.classList.add(d===1?"sorted-a":"sorted-d");
      rows.sort(function(a,b){{
        var x=a.cells[i].innerText.replace(/[$,±+]/g,""),y=b.cells[i].innerText.replace(/[$,±+]/g,"");
        var nx=parseFloat(x),ny=parseFloat(y);
        if(!isNaN(nx)&&!isNaN(ny))return (nx-ny)*d;
        return x.localeCompare(y)*d;
      }});
      rows.forEach(function(r){{tb.appendChild(r)}});
    }};
  }});
}});
var q=document.getElementById("q");
function filt(){{
  var v=q.value.toLowerCase(),tb=document.getElementById("all").tBodies[0];
  [].slice.call(tb.rows).forEach(function(r){{
    r.style.display=r.innerText.toLowerCase().indexOf(v)>-1?"":"none";
  }});
}}
if(q)q.addEventListener("input",filt);
document.querySelectorAll(".chip").forEach(function(c){{
  c.onclick=function(){{q.value=c.dataset.q;filt();
    document.getElementById("all").scrollIntoView({{behavior:"smooth"}});}};
}});
</script></body></html>"""


if __name__ == "__main__":
    s = build(load())
    print(f"wrote {OUT.name} — {s['live']} live, {s['new']} new, {s['accepted']} under offer, "
          f"{s['drops']} drops, {s['relisted']} relisted, {s['gone']} withdrawn")
