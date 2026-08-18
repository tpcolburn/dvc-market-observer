#!/usr/bin/env python3
"""
Turn data/listings_history.csv into a single self-contained dashboard.

Everything is inlined — data, styles, chart drawing — so the file works opened
straight off disk, from OneDrive, or as a mail attachment. No CDN, no fetch,
no CORS.
"""

import csv, json, sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import median

BASE = Path(__file__).resolve().parent
HISTORY = BASE / "data" / "listings_history.csv"
OUT = BASE / "index.html"

# Resale points at these resorts can only book that same resort (plus Interval),
# never the wider DVC system. It is the reason they trade cheap, and no
# cost metric can see it — so it is labelled everywhere the number appears.
RESTRICTED = {"Riviera Resort", "Villas at Disneyland Hotel", "Cabins at Fort Wilderness"}

NUM = {"points", "price", "price_per_point", "dues_per_point", "deed_year",
       "point_delta", "cost_per_point_year"}


def load():
    if not HISTORY.exists():
        sys.exit("no history file yet — run scrape_all.py first")
    rows = []
    with open(HISTORY, newline="") as f:
        for r in csv.DictReader(f):
            for k in NUM:
                v = (r.get(k) or "").strip()
                r[k] = (float(v) if "." in v else int(v)) if v else None
            if r.get("points") and r.get("price_per_point"):
                rows.append(r)
    return rows


def build(rows):
    dates = sorted({r["date"] for r in rows})
    latest = dates[-1]
    live = [r for r in rows if r["date"] == latest and r.get("status") != "Sold"]

    # median $/pt per resort per day — the trend series
    series = defaultdict(dict)
    for d in dates:
        by = defaultdict(list)
        for r in rows:
            if r["date"] == d and r["price_per_point"]:
                by[r["resort"]].append(r["price_per_point"])
        for resort, v in by.items():
            if len(v) >= 2:
                series[resort][d] = round(median(v), 2)

    # resort summary off the latest snapshot
    summary = []
    by_resort = defaultdict(list)
    for r in live:
        by_resort[r["resort"]].append(r)
    for resort, rs in sorted(by_resort.items()):
        ppp = [r["price_per_point"] for r in rs if r["price_per_point"]]
        cpy = [r["cost_per_point_year"] for r in rs if r["cost_per_point_year"]]
        deeds = [r["deed_year"] for r in rs if r["deed_year"]]
        dues = [r["dues_per_point"] for r in rs if r["dues_per_point"]]
        hist = series.get(resort, {})
        first = hist.get(dates[0])
        last = hist.get(latest)
        summary.append(dict(
            resort=resort, n=len(rs), restricted=resort in RESTRICTED,
            median_ppp=round(median(ppp), 2) if ppp else None,
            min_ppp=min(ppp) if ppp else None,
            median_cpy=round(median(cpy), 2) if cpy else None,
            deed=max(set(deeds), key=deeds.count) if deeds else None,
            dues=round(median(dues), 4) if dues else None,
            change=(round(last - first, 2) if first and last and len(hist) > 1 else None)))

    listings = [dict(
        resort=r["resort"], broker=r["broker"], id=r["listing_id"], url=r["url"],
        pts=r["points"], uy=r["use_year"] or "", price=r["price"],
        ppp=r["price_per_point"], cpy=r["cost_per_point_year"],
        deed=r["deed_year"], dues=r["dues_per_point"],
        delta=r["point_delta"] or 0, status=r["status"] or "",
        restricted=r["resort"] in RESTRICTED)
        for r in sorted(live, key=lambda r: (r["cost_per_point_year"] is None,
                                             r["cost_per_point_year"] or 0))]

    payload = dict(generated=date.today().isoformat(), latest=latest, dates=dates,
                   series={k: v for k, v in series.items()}, summary=summary,
                   listings=listings,
                   totals=dict(live=len(live), resorts=len(by_resort),
                               days=len(dates), rows=len(rows)))
    OUT.write_text(TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":"))))
    return payload


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DVC Resale Market</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1f2328;--muted:#656d76;--line:#e3e6ea;
      --accent:#0969da;--good:#1a7f37;--warn:#9a6700;--bad:#b35900}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--ink:#e6edf3;
      --muted:#9198a1;--line:#30363d;--accent:#4493f8;--good:#3fb950;--warn:#d29922;--bad:#db6d28}}
*{box-sizing:border-box}
body{margin:0;padding:22px;background:var(--bg);color:var(--ink);
     font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:1240px;margin:0 auto}
h1{font-size:22px;margin:0 0 3px}
.sub{color:var(--muted);font-size:13px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.card .v{font-size:22px;font-weight:650;margin-top:5px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:22px}
h2{font-size:15px;margin:0 0 4px}
.hint{color:var(--muted);font-size:12px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;
   cursor:pointer;user-select:none;position:sticky;top:0;background:var(--card)}
th:first-child,td:first-child{text-align:left}
th.s::after{content:" ▾";opacity:.55}
tbody tr:hover{background:rgba(127,127,127,.07)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.scroll{overflow:auto;max-height:620px;border:1px solid var(--line);border-radius:8px}
.tblwrap{overflow-x:auto}
.pill{font-size:11px;padding:1px 7px;border-radius:20px;border:1px solid var(--line);color:var(--muted)}
.up{color:var(--bad)}.down{color:var(--good)}
.pill.restr{border-color:var(--bad);color:var(--bad)}
.ctrl{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
input,select{background:var(--card);color:var(--ink);border:1px solid var(--line);
             border-radius:7px;padding:6px 9px;font:inherit;font-size:13px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:10px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
.note{color:var(--muted);font-size:12px;line-height:1.65;margin-top:6px}
</style></head><body><div class="wrap">
<h1>DVC Resale Market</h1>
<div class="sub" id="sub"></div>
<div class="cards" id="cards"></div>

<div class="panel">
  <h2>Median price per point over time</h2>
  <div class="hint">Resorts with at least two listings on a given day. Sticker price only —
    it ignores dues and deed length, which is why the table below ranks on carrying cost instead.</div>
  <div id="chart"></div>
  <div class="legend" id="legend"></div>
</div>

<div class="panel">
  <h2>Resorts ranked by carrying cost</h2>
  <div class="hint">Cost per point-year = (price + closing + $500 Disney CAF − banked points at $19)
    ÷ years left on the deed, plus annual dues, ÷ points. A cheap sticker at a short-deed,
    high-dues resort is not a cheap contract.</div>
  <div class="tblwrap"><table id="summary"></table></div>
</div>

<div class="panel">
  <h2>All listings</h2>
  <div class="ctrl">
    <input id="q" placeholder="Search resort, use year, broker, ID…" style="flex:1;min-width:200px">
    <select id="fr"></select>
    <select id="fp">
      <option value="">Any size</option><option value="0-99">Under 100</option>
      <option value="100-174">100–174</option><option value="175-249">175–249</option>
      <option value="250-9999">250+</option>
    </select>
  </div>
  <div class="scroll tblwrap"><table id="listings"></table></div>
  <div class="note" id="count"></div>
</div>

<div class="panel">
  <div class="note">
    Sources: DVC Resale Market, The DVC Store, DVC Sales — each via its published listing sitemap.
    Fidelity Real Estate and DVC Resale Experts are not included; they block automated access and
    are not worked around. DVC Sales does not publish a machine-readable status, so its rows show
    <b>Unverified</b> — confirm before making an offer.<br>
    Carrying cost needs both a deed year and a dues figure; listings missing either are ranked last.
    ROFR is not modelled — Disney can take an aggressively priced contract regardless of what it scores.<br>
    <b>Resale-restricted</b> resorts (Riviera, Villas at Disneyland Hotel, Cabins at Fort Wilderness) score
    well precisely because their resale points can only book that one resort. Carrying cost cannot see that.
    Treat their ranking as a warning, not a recommendation.
  </div>
</div>
</div>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const fmt = (n,d=0) => n==null ? "—" : n.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});

$("#sub").textContent = `${D.totals.live} live listings across ${D.totals.resorts} resorts · `
  + `snapshot ${D.latest} · ${D.totals.days} day${D.totals.days>1?"s":""} of history`;

const withCpy = D.listings.filter(l=>l.cpy!=null);
const best = withCpy.filter(l=>!l.restricted)[0];
const cards = [
  ["Live listings", D.totals.live],
  ["Resorts tracked", D.totals.resorts],
  ["Median $/pt", "$"+fmt(median(D.listings.map(l=>l.ppp)),0)],
  ["Best unrestricted", best ? "$"+best.cpy.toFixed(2) : "—", best ? best.resort : ""],
];
$("#cards").innerHTML = cards.map(([l,v,s])=>
  `<div class="card"><div class="l">${l}</div><div class="v">${v}</div>${s?`<div class="l" style="margin-top:4px;text-transform:none">${s}</div>`:""}</div>`).join("");

function median(a){a=a.filter(x=>x!=null).sort((x,y)=>x-y);if(!a.length)return null;
  const m=a.length>>1;return a.length%2?a[m]:(a[m-1]+a[m])/2}

// ---- chart: inline SVG, no library
(function(){
  const names = Object.keys(D.series).filter(k=>Object.keys(D.series[k]).length>1);
  if(D.dates.length<2 || !names.length){
    $("#chart").innerHTML = `<div class="note">Only one day of data so far — trend lines appear once
      the daily job has run at least twice.</div>`;
    return;
  }
  const W=1100,H=320,P={t:14,r:14,b:28,l:46};
  const xs=D.dates, all=[];
  names.forEach(n=>xs.forEach(d=>{const v=D.series[n][d]; if(v!=null)all.push(v)}));
  const lo=Math.min(...all)*0.97, hi=Math.max(...all)*1.03;
  const X=i=>P.l+i*(W-P.l-P.r)/Math.max(1,xs.length-1);
  const Y=v=>P.t+(hi-v)*(H-P.t-P.b)/(hi-lo);
  const pal=["#4493f8","#3fb950","#d29922","#db6d28","#a371f7","#f778ba","#56d4dd","#e6edf3",
             "#7ee787","#ffa657","#79c0ff","#d2a8ff","#ff7b72","#8ddb8c","#c9d1d9","#bc8cff"];
  let g=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">`;
  for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,y=Y(v);
    g+=`<line x1="${P.l}" y1="${y}" x2="${W-P.r}" y2="${y}" stroke="currentColor" stroke-opacity=".12"/>`
     + `<text x="${P.l-7}" y="${y+4}" text-anchor="end" font-size="11" fill="currentColor" opacity=".55">$${v.toFixed(0)}</text>`}
  xs.forEach((d,i)=>{ if(xs.length<=8||i%Math.ceil(xs.length/8)===0)
    g+=`<text x="${X(i)}" y="${H-8}" text-anchor="middle" font-size="11" fill="currentColor" opacity=".55">${d.slice(5)}</text>`});
  names.forEach((n,k)=>{
    let pts=[];
    xs.forEach((d,i)=>{const v=D.series[n][d]; if(v!=null)pts.push(`${X(i)},${Y(v)}`)});
    if(pts.length>1) g+=`<polyline points="${pts.join(" ")}" fill="none" stroke="${pal[k%pal.length]}" stroke-width="2" stroke-linejoin="round"/>`;
  });
  $("#chart").innerHTML=g+"</svg>";
  $("#legend").innerHTML=names.map((n,k)=>`<span><i style="background:${pal[k%pal.length]}"></i>${n}</span>`).join("");
})();

// ---- resort summary
(function(){
  const cols=[["resort","Resort"],["n","Listings"],["median_ppp","Median $/pt"],["min_ppp","Lowest $/pt"],
              ["dues","Dues/pt"],["deed","Deed"],["median_cpy","Median $/pt-yr"],["change","Trend"]];
  let rows=D.summary.slice().sort((a,b)=>(a.median_cpy==null)-(b.median_cpy==null)||(a.median_cpy-b.median_cpy));
  const cell=(r,k)=>{
    if(k==="resort")return r.resort + (r.restricted?' <span class="pill restr">resale-restricted</span>':"");
    if(k==="change"){if(r.change==null)return `<span class="pill">new</span>`;
      const c=r.change>0?"up":"down";return `<span class="${c}">${r.change>0?"+":""}$${r.change.toFixed(2)}</span>`}
    if(k==="dues")return r.dues==null?"—":"$"+r.dues.toFixed(2);
    if(k==="deed")return r.deed||"—";
    if(k==="n")return r.n;
    return r[k]==null?"—":"$"+r[k].toFixed(2);
  };
  $("#summary").innerHTML=`<thead><tr>${cols.map(c=>`<th>${c[1]}</th>`).join("")}</tr></thead><tbody>`
    +rows.map(r=>`<tr>${cols.map(c=>`<td>${cell(r,c[0])}</td>`).join("")}</tr>`).join("")+`</tbody>`;
})();

// ---- full listing table
(function(){
  const cols=[["resort","Resort"],["pts","Pts"],["uy","Use yr"],["ppp","$/pt"],["price","Price"],
              ["cpy","$/pt-yr"],["dues","Dues"],["deed","Deed"],["delta","Banked"],
              ["status","Status"],["broker","Broker"]];
  let sort="cpy", dir=1;
  const sel=$("#fr");
  sel.innerHTML=`<option value="">All resorts</option>`+
    [...new Set(D.listings.map(l=>l.resort))].sort().map(r=>`<option>${r}</option>`).join("");
  function view(){
    const q=$("#q").value.toLowerCase(), r=sel.value, p=$("#fp").value;
    let rows=D.listings.filter(l=>{
      if(r&&l.resort!==r)return false;
      if(p){const[a,b]=p.split("-").map(Number); if(l.pts<a||l.pts>b)return false}
      if(q&&!`${l.resort} ${l.uy} ${l.broker} ${l.id}`.toLowerCase().includes(q))return false;
      return true});
    rows.sort((a,b)=>{const x=a[sort],y=b[sort];
      if(x==null)return 1; if(y==null)return -1;
      return (typeof x==="string"?x.localeCompare(y):x-y)*dir});
    $("#listings").innerHTML=`<thead><tr>${cols.map(c=>
      `<th data-k="${c[0]}" class="${c[0]===sort?"s":""}">${c[1]}</th>`).join("")}</tr></thead><tbody>`
      +rows.map(l=>`<tr>
        <td><a href="${l.url}" target="_blank" rel="noopener">${l.resort}</a>
            <span class="pill" style="margin-left:6px">${l.id}</span>${l.restricted?' <span class="pill restr">restricted</span>':""}</td>
        <td>${l.pts}</td><td>${l.uy||"—"}</td><td><b>$${fmt(l.ppp,0)}</b></td>
        <td>$${fmt(l.price)}</td>
        <td>${l.cpy==null?"—":"<b>$"+l.cpy.toFixed(2)+"</b>"}</td>
        <td>${l.dues==null?"—":"$"+l.dues.toFixed(2)}</td><td>${l.deed||"—"}</td>
        <td>${l.delta?(l.delta>0?"+"+l.delta:l.delta):"—"}</td>
        <td>${l.status||"—"}</td><td>${l.broker}</td></tr>`).join("")+`</tbody>`;
    $("#count").textContent=`${rows.length} of ${D.listings.length} listings shown`;
    $("#listings").querySelectorAll("th").forEach(th=>th.onclick=()=>{
      const k=th.dataset.k; dir=(k===sort)?-dir:1; sort=k; view()});
  }
  ["#q","#fr","#fp"].forEach(s=>$(s).addEventListener("input",view));
  view();
})();
</script></body></html>
"""

if __name__ == "__main__":
    p = build(load())
    msg = (f"{p['totals']['live']} live listings across {p['totals']['resorts']} resorts, "
           f"{p['totals']['days']} days of history.")
    unres = [s for s in p["summary"] if s["median_cpy"] and not s["restricted"]]
    unres.sort(key=lambda s: s["median_cpy"])
    lines = [msg, "", "Best carrying cost, unrestricted resorts:"]
    for s in unres[:5]:
        lines.append(f"  {s['resort']:<28} ${s['median_cpy']:.2f}/pt-yr   "
                     f"median ${s['median_ppp']:.0f}/pt   deed {s['deed']}")
    lines += ["", "Full detail in the attached dashboard — open it in any browser."]
    (BASE / "summary.txt").write_text("\n".join(lines))
    print(f"wrote {OUT} — {msg}")
