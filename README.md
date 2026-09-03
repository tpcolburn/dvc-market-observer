# DVC Resale Market Observer

Weekly snapshot of every DVC resale listing the brokers publish, at every contract
size, across all 17 resorts. Emails a buy-list digest and dashboard link every
Sunday morning; publishes the dashboard itself to GitHub Pages.

Was daily through 2026-08; moved to weekly on 2026-08-29 — see *Cadence* below
for what that trades away.

## How it works

`.github/workflows/daily_scrape.yml` runs Sundays at 09:00 UTC on GitHub's
servers — no local machine involved:

1. **`scrape_all.py`** — walks each broker's published listing sitemap, parses every
   listing, appends the week's snapshot to `data/listings_history.csv`.
2. **`build_dashboard.py`** — turns the full history into `index.html` (served by
   GitHub Pages) plus `summary.html`/`summary.txt` (the email body) and the personal
   buy-list scoring from `alerts.py`.
3. Commits everything, then emails the digest with `index.html` attached.

## Setup

The email step needs two repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `MAIL_USERNAME` | `tpcolburn@gmail.com` |
| `MAIL_PASSWORD` | A Gmail **app password** (not the account password) |

Generate one at <https://myaccount.google.com/apppasswords>. This is a second app
password, separate from the one the local alert monitor uses — revoking either won't
affect the other.

Run it on demand from the Actions tab → *Weekly DVC Market Snapshot* → *Run workflow*
— useful right after a real-world change (an offer, a counter, ROFR clearing)
when Sunday is too far off.

## What the numbers mean

**Cost per point-year** is the ranking metric:

```
acquisition = price + closing + $500 Disney CAF − (banked points × $19)
cost/pt-yr  = (acquisition ÷ years left on deed + points × annual dues) ÷ points
```

Sticker `$/pt` is misleading across resorts. A contract at a resort with a 2042 deed
and $11.21 dues is worse than one costing 50% more at a 2068 deed with $9.02 dues.
Dues and deed year are read off the listing pages themselves, so this stays correct
without a maintained reference table.

### Caveats that change conclusions

- **Resale-restricted resorts** — Riviera, Villas at Disneyland Hotel, and the Cabins
  at Fort Wilderness score well *because* their resale points can only book that one
  resort. Riviera currently shows the best carrying cost in the entire system. That is
  a warning, not a recommendation. They are badged everywhere they appear.
- **ROFR is not modelled.** Disney can exercise right of first refusal on an
  aggressively priced contract. A good score is not a contract you get to keep.
- **DVC Sales publishes no machine-readable status**, so its rows show `Unverified`.
  Confirm availability before making an offer.
- **Cost per point-year measures cost per *point*, not per *night*.** Resorts with
  cheaper point charts buy more nights per point than the score implies.

## Coverage

| Broker | Method |
|---|---|
| DVC Resale Market | Published listing sitemap |
| The DVC Store | Published listing sitemap |
| DVC Sales | Published listing sitemap |
| Fidelity Real Estate | **Not included** — blocks automated requests |
| DVC Resale Experts | **Not included** — CAPTCHA wall |

Neither exclusion is worked around. Fidelity often carries the lowest prices;
subscribe to their list directly.

One request per listing, ~1.2s apart, identifying user agent, robots.txt respected.

## History

`data/listings_history.csv` accumulates one row per listing per day. The
`2026-01-10` rows are archived from the original scraper and carry only resort,
points, and price per point — the columns that version recorded.

## Cadence

Weekly since 2026-08-29, previously daily. Traded away by the move:

- **Thin-inventory use years go dark for up to 6 days.** March Copper Creek — the
  use year contract #2 is locked to — has run at roughly one new listing a week
  market-wide, and that listing can sell before the next Sunday snapshot. A
  fast-moving contract in a thin pool can appear and disappear between runs with
  no alert either way.
- **Departure-based signals (sold, withdrawn) lose a week of resolution** — a
  listing that goes pending and closes inside the week never appears as a change.
- Trigger a manual run (above) around any specific negotiation instead of waiting
  for Sunday.

## Related

The personal buy-list scoring (Copper Creek–focused, point-band filtered,
resort-desirability weighted) now runs inside this same workflow via
`alerts.py`, reading `ALERT_CONFIG` from repo secrets. It no longer runs
separately from `~/.dvc-monitor/` — that local monitor was retired 2026-08-20.
