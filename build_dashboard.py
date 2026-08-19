#!/usr/bin/env python3
"""
Turn data/listings_history.csv into index.html and the email body.

Every visible element is rendered here, in Python, as real HTML. Mail clients
(iOS Mail in particular) do not execute JavaScript in attachment previews, so a
page that assembles itself client-side shows up blank. JavaScript is used only
to add sorting and filtering on top of markup that is already complete.
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
HEALTH = BASE / "data" / "source_health.json"

# Resale points at these resorts can only book that same resort, never the wider
# DVC system. It is why they trade cheap, and no cost metric can see it.
RESTRICTED = {"Riviera Resort", "Villas at Disneyland Hotel", "Cabins at Fort Wilderness"}

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
    """What changed between the two most recent snapshots.

    Brokers whose scrape failed are skipped entirely: they have no rows today,
    so every listing they carry would otherwise be reported as withdrawn."""
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


def listing_rows(items, extra=None):
    out = []
    for r in items:
        badge = ' <span class="tag r">restricted</span>' if r["resort"] in RESTRICTED else ""
        note = ""
        if extra == "drop":
            note = f'<span class="was">was ${r["_was"]:,.0f}</span>'
        elif extra == "from":
            note = f'<span class="was">from {e(r["_from"])}</span>'
        delta = r["point_delta"] or 0
        dtxt = (f'<span class="pos">+{delta}</span>' if delta > 0
                else f'<span class="neg">{delta}</span>' if delta < 0 else "—")
        url = r.get("url") or ""
        name = f'<a href="{e(url)}">{e(r["resort"])}</a>' if url else e(r["resort"])
        out.append(f"""<tr>
<td>{name}{badge}<div class="sub">{e(r['broker'])} · {e(r['listing_id'])} · {e(r['use_year'] or '?')} UY</div></td>
<td class="n">{r['points']}</td>
<td class="n"><b>{money(r['price_per_point'])}</b> {note}</td>
<td class="n">{money(r['price'])}</td>
<td class="n"><b>{money(r['cost_per_point_year'],2)}</b></td>
<td class="n">{r['deed_year'] or '—'}</td>
<td class="n">{dtxt}</td>
<td class="n">{e(r['status'] or '—')}</td></tr>""")
    return "\n".join(out)


TH = ("""<tr><th>Contract</th><th class="n">Pts</th><th class="n">$/pt</th><th class="n">Price</th>"""
      """<th class="n">$/pt-yr</th><th class="n">Deed</th><th class="n">Banked</th><th class="n">Status</th></tr>""")


def section(title, blurb, items, extra=None, cls=""):
    if not items:
        return f'<h2>{e(title)} <span class="count">0</span></h2><p class="none">Nothing today.</p>'
    return (f'<h2 class="{cls}">{e(title)} <span class="count">{len(items)}</span></h2>'
            f'<p class="blurb">{e(blurb)}</p>'
            f'<div class="scroll"><table><thead>{TH}</thead><tbody>{listing_rows(items, extra)}</tbody></table></div>')


def build(rows):
    dates = sorted({r["date"] for r in rows})
    latest = dates[-1]
    prev = dates[-2] if len(dates) > 1 else None
    health = load_health()
    dead = {b for b, v in health.items() if not v.get("ok")}
    ch = diff(rows, latest, prev, dead)
    live = [r for r in rows if r["date"] == latest and r["status"] not in SOLD_LIKE]

    # median $/pt per resort per day
    series = defaultdict(dict)
    for d in dates:
        by = defaultdict(list)
        for r in rows:
            if r["date"] == d and r["price_per_point"]:
                by[r["resort"]].append(r["price_per_point"])
        for k, v in by.items():
            if len(v) >= 2:
                series[k][d] = round(median(v), 2)

    by_resort = defaultdict(list)
    for r in live:
        by_resort[r["resort"]].append(r)
    summary = []
    for resort, rs in by_resort.items():
        ppp = [r["price_per_point"] for r in rs if r["price_per_point"]]
        cpy = [r["cost_per_point_year"] for r in rs if r["cost_per_point_year"]]
        deeds = [r["deed_year"] for r in rs if r["deed_year"]]
        dues = [r["dues_per_point"] for r in rs if r["dues_per_point"]]
        summary.append(dict(resort=resort, n=len(rs), restricted=resort in RESTRICTED,
                            ppp=round(median(ppp), 2) if ppp else None,
                            cpy=round(median(cpy), 2) if cpy else None,
                            deed=max(set(deeds), key=deeds.count) if deeds else None,
                            dues=round(median(dues), 4) if dues else None))
    summary.sort(key=lambda s: (s["cpy"] is None, s["cpy"] or 0))

    # ---- chart, drawn here rather than in the browser
    names = [k for k in series if len(series[k]) > 1]
    if len(dates) > 1 and names:
        W, H, P = 1100, 300, dict(t=12, r=12, b=26, l=46)
        vals = [v for n in names for v in series[n].values()]
        lo, hi = min(vals) * .97, max(vals) * 1.03
        X = lambda i: P["l"] + i * (W - P["l"] - P["r"]) / max(1, len(dates) - 1)
        Y = lambda v: P["t"] + (hi - v) * (H - P["t"] - P["b"]) / (hi - lo or 1)
        pal = ["#4493f8", "#3fb950", "#d29922", "#db6d28", "#a371f7", "#f778ba", "#56d4dd",
               "#7ee787", "#ffa657", "#79c0ff", "#d2a8ff", "#ff7b72", "#8ddb8c", "#bc8cff",
               "#e6edf3", "#9198a1", "#58a6ff"]
        g = [f'<svg viewBox="0 0 {W} {H}" class="chart">']
        for i in range(5):
            v = lo + (hi - lo) * i / 4
            g.append(f'<line x1="{P["l"]}" y1="{Y(v):.1f}" x2="{W-P["r"]}" y2="{Y(v):.1f}" class="grid"/>'
                     f'<text x="{P["l"]-6}" y="{Y(v)+4:.1f}" class="ax" text-anchor="end">${v:.0f}</text>')
        step = max(1, len(dates) // 8)
        for i, d in enumerate(dates):
            if i % step == 0 or i == len(dates) - 1:
                g.append(f'<text x="{X(i):.1f}" y="{H-7}" class="ax" text-anchor="middle">{d[5:]}</text>')
        legend = []
        for k, n in enumerate(sorted(names)):
            pts = [f"{X(i):.1f},{Y(series[n][d]):.1f}" for i, d in enumerate(dates) if d in series[n]]
            if len(pts) > 1:
                c = pal[k % len(pal)]
                g.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{c}" stroke-width="2"/>')
                legend.append(f'<span><i style="background:{c}"></i>{e(n)}</span>')
        chart = "".join(g) + "</svg>" + f'<div class="legend">{"".join(legend)}</div>'
    else:
        chart = '<p class="none">Trend lines need at least two days of history.</p>'

    _sr = []
    for s in summary:
        tag = ' <span class="tag r">restricted</span>' if s["restricted"] else ""
        _sr.append("<tr><td>" + e(s["resort"]) + tag + "</td>"
                   + '<td class="n">' + str(s["n"]) + "</td>"
                   + '<td class="n">' + money(s["ppp"]) + "</td>"
                   + '<td class="n">' + money(s["dues"], 2) + "</td>"
                   + '<td class="n">' + (str(s["deed"]) if s["deed"] else "—") + "</td>"
                   + '<td class="n"><b>' + money(s["cpy"], 2) + "</b></td></tr>")
    srows = "\n".join(_sr)

    unres = [s for s in summary if s["cpy"] and not s["restricted"]]
    cards = [("Live listings", f"{len(live):,}"), ("New today", str(len(ch["new"]))),
             ("Newly under offer", str(len(ch["accepted"]))), ("Price drops", str(len(ch["drops"]))),
             ("Best unrestricted", money(unres[0]["cpy"], 2) if unres else "—",
              unres[0]["resort"] if unres else "")]
    _ch = []
    for c in cards:
        sub = ('<div class="s">' + e(c[2]) + "</div>") if len(c) > 2 and c[2] else ""
        _ch.append('<div class="card"><div class="l">' + e(c[0]) + "</div>"
                   + '<div class="v">' + c[1] + "</div>" + sub + "</div>")
    cardhtml = "".join(_ch)

    if dead:
        detail = "; ".join(
            f'{b}: {health[b]["parsed"]}/{health[b]["targets"]} parsed' for b in sorted(dead))
        banner = ('<div class="alert"><b>Incomplete sweep.</b> '
                  + e(detail)
                  + " — these brokers' listings are missing from today's snapshot and are "
                    "excluded from the change sections below, so nothing here is a real withdrawal "
                    "for them.</div>")
    else:
        banner = ""

    page = TEMPLATE.format(
        banner=banner,
        latest=latest, prev=prev or "—", ndates=len(dates), nrows=len(rows),
        cards=cardhtml,
        new=section("Newly listed", "On the market since the previous sweep.", ch["new"]),
        accepted=section("New offers accepted", "Moved to Offer Accepted, Sale Pending or Under Contract.",
                         ch["accepted"], "from", "warn"),
        drops=section("Price drops", "Asking price per point fell since the previous sweep.",
                      ch["drops"], "drop", "good"),
        relisted=section("Back on the market", "Returned to Available after being under offer — failed "
                         "ROFR or financing. Often the best prices.", ch["relisted"], "from", "good"),
        sold=section("Sold", "Left the market as sold.", ch["sold"]),
        gone=section("Withdrawn", "Present in the previous sweep, absent now, and not marked sold.", ch["gone"]),
        chart=chart, srows=srows,
        alltable=f'<div class="scroll tall"><table id="all"><thead>{TH}</thead>'
                 f'<tbody>{listing_rows(sorted(live, key=lambda r: (r["cost_per_point_year"] is None, r["cost_per_point_year"] or 0)))}</tbody></table></div>',
        nlive=len(live))
    OUT.write_text(page)

    # ---- email body: what changed, not a standing leaderboard
    L = []
    if dead:
        L.append("!! INCOMPLETE SWEEP — " + ", ".join(sorted(dead)) + " failed to scrape.")
        L.append("   Their listings are missing today and excluded from the counts below.")
        L.append("")
    L += [f"{len(ch['new'])} new · {len(ch['accepted'])} newly under offer · "
         f"{len(ch['drops'])} price drops · {len(ch['relisted'])} back on market",
         f"({len(live)} live listings, {len(dates)} days of history, vs {prev or 'n/a'})", ""]

    def block(title, items, fmt):
        if not items:
            return
        L.append(f"{title} ({len(items)})")
        for r in items[:12]:
            L.append("  " + fmt(r))
        if len(items) > 12:
            L.append(f"  … and {len(items)-12} more")
        L.append("")

    block("NEWLY LISTED", ch["new"], lambda r:
          f"{r['resort'][:26]:<26} {r['points']:>4}pt {str(r['use_year'] or '?')[:3]:<3} "
          f"${r['price_per_point']:>4.0f}/pt  ${r['cost_per_point_year'] or 0:>5.2f}/pt-yr  {r['broker'][:18]}")
    block("NEW OFFERS ACCEPTED", ch["accepted"], lambda r:
          f"{r['resort'][:26]:<26} {r['points']:>4}pt ${r['price_per_point']:>4.0f}/pt  "
          f"{r['_from']} -> {r['status']}")
    block("BACK ON MARKET", ch["relisted"], lambda r:
          f"{r['resort'][:26]:<26} {r['points']:>4}pt ${r['price_per_point']:>4.0f}/pt  (was {r['_from']})")
    block("PRICE DROPS", ch["drops"], lambda r:
          f"{r['resort'][:26]:<26} {r['points']:>4}pt ${r['_was']:.0f} -> ${r['price_per_point']:.0f}/pt")
    L.append("Full detail in the attached dashboard, or:")
    L.append("https://tpcolburn.github.io/dvc-market-observer/")
    SUMMARY.write_text("\n".join(L))
    return dict(live=len(live), dates=len(dates), rows=len(rows),
                **{k: len(v) for k, v in ch.items()})


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
h2.good{{color:var(--good)}} h2.warn{{color:var(--warn)}}
.count{{font-size:12px;color:var(--muted);font-weight:400}}
.sub,.blurb,.none{{color:var(--muted);font-size:12.5px}}
.blurb,.none{{margin:2px 0 10px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:18px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:12px}}
.card .l{{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
.card .v{{font-size:21px;font-weight:650;margin-top:4px}}
.card .s{{font-size:11px;color:var(--muted)}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:var(--card)}}
th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;
position:sticky;top:0;background:var(--card);cursor:pointer}}
td.n,th.n{{text-align:right;white-space:nowrap}}
.scroll{{overflow:auto;border:1px solid var(--line);border-radius:9px;max-height:460px}}
.scroll.tall{{max-height:700px}}
a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
.tag{{font-size:10px;padding:1px 6px;border-radius:20px;border:1px solid var(--line);color:var(--muted)}}
.tag.r{{border-color:var(--bad);color:var(--bad)}}
.was{{font-size:11px;color:var(--muted)}}
.pos{{color:var(--good)}} .neg{{color:var(--bad)}}
.chart{{width:100%;height:auto;color:var(--ink)}}
.grid{{stroke:currentColor;stroke-opacity:.13}}
.ax{{font-size:11px;fill:currentColor;opacity:.55}}
.legend{{display:flex;gap:12px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin-top:8px}}
.legend i{{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}}
.ctrl{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}}
input,select{{background:var(--card);color:var(--ink);border:1px solid var(--line);
border-radius:7px;padding:6px 9px;font:inherit;font-size:13px}}
.alert{{background:var(--card);border:1px solid var(--bad);border-left:4px solid var(--bad);border-radius:9px;padding:12px;margin:16px 0;font-size:13px;line-height:1.6}}
.note{{background:var(--card);border:1px solid var(--line);border-radius:9px;
padding:13px;margin-top:26px;color:var(--muted);font-size:12px;line-height:1.65}}
</style></head><body><div class="wrap">
<h1>DVC Resale Market</h1>
<div class="sub">Snapshot {latest} · compared with {prev} · {ndates} days, {nrows:,} rows</div>
{banner}<div class="cards">{cards}</div>
{new}{accepted}{relisted}{drops}{sold}{gone}
<h2>Median price per point over time</h2>
<p class="blurb">Resorts with at least two listings that day. Sticker price only — it ignores dues and deed length.</p>
{chart}
<h2>Resorts ranked by carrying cost</h2>
<p class="blurb">Cost per point-year = (price + closing + $500 Disney CAF − banked points at $19) ÷ years left on deed, plus annual dues, ÷ points.</p>
<div class="scroll"><table><thead><tr><th>Resort</th><th class="n">Listings</th><th class="n">Median $/pt</th><th class="n">Dues/pt</th><th class="n">Deed</th><th class="n">Median $/pt-yr</th></tr></thead><tbody>{srows}</tbody></table></div>
<h2>All live listings <span class="count">{nlive}</span></h2>
<div class="ctrl"><input id="q" placeholder="Search resort, use year, broker, ID…" style="flex:1;min-width:190px"></div>
{alltable}
<div class="note">
<b>Resale-restricted</b> resorts (Riviera, Villas at Disneyland Hotel, Cabins at Fort Wilderness) score well
<i>because</i> their resale points can only book that one resort. Treat their ranking as a warning.<br>
<b>ROFR is not modelled</b> — Disney can take an aggressively priced contract regardless of its score.<br>
<b>DVC Sales</b> publishes no machine-readable status, so its rows show <i>Unverified</i>.<br>
Sources: DVC Resale Market, The DVC Store, DVC Sales, via published listing sitemaps. Fidelity Real Estate
and DVC Resale Experts are not included.
</div></div>
<script>
// progressive enhancement only — the tables above are already complete without it
document.querySelectorAll("table").forEach(function(t){{
  t.querySelectorAll("th").forEach(function(th,i){{
    th.onclick=function(){{
      var tb=t.tBodies[0],rows=[].slice.call(tb.rows),d=th.dataset.d==="1"?-1:1;
      th.dataset.d=d===1?"1":"";
      rows.sort(function(a,b){{
        var x=a.cells[i].innerText.replace(/[$,]/g,""),y=b.cells[i].innerText.replace(/[$,]/g,"");
        var nx=parseFloat(x),ny=parseFloat(y);
        if(!isNaN(nx)&&!isNaN(ny))return (nx-ny)*d;
        return x.localeCompare(y)*d;
      }});
      rows.forEach(function(r){{tb.appendChild(r)}});
    }};
  }});
}});
var q=document.getElementById("q");
if(q)q.addEventListener("input",function(){{
  var v=q.value.toLowerCase(),tb=document.getElementById("all").tBodies[0];
  [].slice.call(tb.rows).forEach(function(r){{
    r.style.display=r.innerText.toLowerCase().indexOf(v)>-1?"":"none";
  }});
}});
</script></body></html>"""


if __name__ == "__main__":
    s = build(load())
    print(f"wrote {OUT.name} and {SUMMARY.name} — {s['live']} live, "
          f"{s['new']} new, {s['accepted']} under offer, {s['drops']} drops, "
          f"{s['relisted']} relisted, {s['gone']} withdrawn")
