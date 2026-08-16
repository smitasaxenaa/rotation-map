# Rotation Map — NSE industries vs CNX500

A sector/industry rotation dashboard for the Indian market. It ranks every industry in the Nifty 500 by how it is performing against the CNX500, classifies where each one sits in its relative-strength lifecycle, and lets you open any row to see the constituent stocks.

No API key, no paid data, no server. A Python script writes one `data.json`; a single HTML file renders it.

---

## Run it

```bash
pip install requests

python make_sample.py     # demo figures, works offline, for checking layout
python build_data.py      # live figures from TradingView's public scanner

python -m http.server 8000
```

Then open `http://localhost:8000`.

`index.html` fetches `data.json`, so opening the file directly with `file://` will fail on the fetch. Any local server works.

When `data.json` came from `make_sample.py`, the dashboard shows a yellow **Sample data** banner so you never mistake demo numbers for real ones.

---

## Put it online

1. Push these files to a GitHub repo.
2. Settings → Pages → Source: **Deploy from a branch**, branch `main`, folder `/ (root)`.
3. Add `.github/workflows/update.yml` (included) so the data refreshes on its own each weekday evening.

The workflow runs `build_data.py`, commits the new `data.json` and `history.json`, and Pages redeploys. Nothing else to maintain.

---

## Files

| File | What it does |
|---|---|
| `build_data.py` | Fetches the universe, aggregates to industry level, classifies, writes `data.json` |
| `make_sample.py` | Generates synthetic `data.json` using the same logic, for offline work |
| `index.html` | The dashboard. Self-contained — no build step, no framework |
| `data.json` | Output. Regenerated on every run |
| `history.json` | Yesterday's ranks, so the dashboard can show ▲▼ rank movement |
| `.github/workflows/update.yml` | Weekday refresh |

---

## The decisions behind the columns

Everything below is a choice, not a fact. All of it is tunable in the `CONFIG` block at the top of `build_data.py`.

**Capped weighting.** Industry returns are market-cap weighted, but no single stock may exceed 25% of its industry. Several NSE industries are one enormous name plus a handful of small ones; uncapped, "Oil Refining" is just Reliance in a costume. The cap is applied by water-filling — clip the offender, redistribute the excess across the rest, repeat.

**Relative strength is compounded, not subtracted.** `(1 + industry) ÷ (1 + index) − 1`. Over three months this is within a rounding error of plain subtraction. Over five years subtraction produces figures like −180%, which describe nothing.

**Mood** is a −6 to +6 score over four things: 3M and 1M relative strength, breadth above the 50- and 200-day averages, and distance from the 52-week high. Bullish at +3 or better, bearish at −2 or worse. Breadth is in there deliberately — a move carried by two stocks is not the same event as a move carried by twenty.

**Cycle** places each industry on a five-stage arc:

| Stage | Condition | Read |
|---|---|---|
| Lagging | 3M and 1M both behind the index | Avoid |
| Basing | 3M behind, 1M ahead | Watch |
| Early | 3M ahead, 1Y not yet | Accumulate |
| Mid | 3M, 6M and 1Y all ahead | Hold / add |
| Late | Over a year ahead, within 8% of the 52-week high, 1M decelerating | Trim / trail |

The distinction that matters is Early vs Late. Both look green on a returns table. One is a turn that started recently with a year of underperformance behind it; the other is a run that has been going long enough to be crowded. The dashboard draws this as a five-segment rail so the stage is a *position*, not a label.

**Trend** is one year of relative strength, reconstructed from the horizon returns rather than stored price history. If a stock is up 20% over 3M, its price 3M ago was `close ÷ 1.20`. Do that for the industry and the index at each horizon, take the ratio, index to 100. Seven points, no database.

**Turnover** is today's traded value over the 10-day average. Treat it as a participation check only. It is a weak proxy for capital movement and is labelled that way in the app.

**Star stock** — beats its own industry over 3M, trades above its 200-day average, and is within 15% of its 52-week high. The point is to separate the stocks carrying an industry from the ones being carried by it.

---

## Known limits

- **This measures price performance relative to a benchmark. It is not fund flow.** No part of this pipeline observes money entering or leaving anything.
- The scanner's numbers are end-of-day for most fields and near-live for the 1D change. Not a real-time feed.
- Industry labels come from TradingView's taxonomy (~145 industries), which does not always match NSE's own sector definitions.
- Industries with fewer than three members are marked **thin**. The aggregate is shown but is noise-prone.
- `build_data.py` tries the Nifty 500 constituent list first and falls back to the top 500 NSE stocks by market cap if TradingView changes that endpoint. The dashboard prints which one it used.
- Nothing here is investment advice. It is a screening and journaling instrument.
