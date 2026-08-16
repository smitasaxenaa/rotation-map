#!/usr/bin/env python3
"""
build_data.py — Sector / Industry Rotation Dashboard data pipeline
==================================================================

Pulls the Nifty 500 universe from TradingView's public scanner endpoint,
aggregates stocks up to industry level, and writes a single `data.json`
that the dashboard (index.html) renders.

No API key. No paid data. Run it locally or on a GitHub Actions cron.

    pip install requests
    python build_data.py

Everything you would want to tune lives in the CONFIG block below.
"""

import json
import math
import sys
import time
from datetime import datetime, timezone

import requests

# ----------------------------------------------------------------------------
# CONFIG — the product decisions live here, not buried in the code
# ----------------------------------------------------------------------------

CONFIG = {
    # A single stock can't dominate its industry's return. NIFTY has industries
    # where one name is 70% of the market cap; uncapped, the "industry" is just
    # that stock wearing a hat.
    "max_stock_weight_in_industry": 0.25,

    # Same idea one level up, for the fallback benchmark.
    "max_stock_weight_in_benchmark": 0.05,

    # Industries with fewer than this many names get flagged as thin — the
    # aggregate is still shown, but it's noise-prone.
    "thin_industry_stock_count": 3,

    # Which horizon the headline "vs CNX500" column uses.
    "relative_headline_horizon": "3M",

    # Mood thresholds, on a -6..+6 score (see score_mood).
    "mood_bullish_at": 3,
    "mood_bearish_at": -2,

    # Cycle thresholds.
    "late_near_high_pct": -8.0,      # within 8% of 52w high counts as "at highs"
    "late_min_1y_rs": 10.0,          # needs a year of outperformance to be Late
    "early_max_1y_rs": 0.0,          # Early = 3M works, 1Y doesn't yet

    # A stock earns a star if it beats its own industry and is structurally OK.
    "star_max_drawdown": -15.0,
    "star_requires_above_sma200": True,

    # ---- Universe -----------------------------------------------------------
    # "broad"    — every NSE primary listing above the market cap floor below.
    #              ~2,000 names, ~140 industries, full TradingView taxonomy.
    #              More one- and two-name industries; the `thin` flag marks them.
    # "nifty500" — index constituents only. ~500 names, ~85 industries.
    #              Cleaner and more liquid, but the narrow industries vanish
    #              entirely because their members sit below the index cutoff.
    "universe_mode": "broad",

    # Floor for "broad" mode, in rupees. 5e9 = Rs 500 crore.
    # Raise to 2e10 (Rs 2,000 cr) if the small-cap tail feels too noisy.
    "min_market_cap_inr": 5e9,

    # Hard ceiling on rows requested, whichever mode is used.
    "universe_size": 2500,

    "request_timeout": 45,
}

SCANNER_URL = "https://scanner.tradingview.com/india/scan"

# TradingView column keys -> what we call them.
COLUMNS = [
    "name", "description", "close", "change",
    "Perf.W", "Perf.1M", "Perf.3M", "Perf.6M", "Perf.Y", "Perf.5Y",
    "market_cap_basic", "sector", "industry",
    "price_52_week_high", "price_52_week_low",
    "SMA50", "SMA200", "RSI",
    "Value.Traded", "average_volume_10d_calc",
]

HORIZONS = [
    ("1D", "change"),
    ("1W", "Perf.W"),
    ("1M", "Perf.1M"),
    ("3M", "Perf.3M"),
    ("6M", "Perf.6M"),
    ("1Y", "Perf.Y"),
    ("5Y", "Perf.5Y"),
]

# Approximate calendar days back, used to place sparkline points on a time axis.
HORIZON_DAYS = {"1D": 1, "1W": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365, "5Y": 1825}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}


# ----------------------------------------------------------------------------
# Fetch
# ----------------------------------------------------------------------------

def _post(payload):
    r = requests.post(SCANNER_URL, json=payload, headers=HEADERS,
                      timeout=CONFIG["request_timeout"])
    r.raise_for_status()
    return r.json()


def _broad_attempt():
    floor = CONFIG["min_market_cap_inr"]
    return (f"NSE above Rs {floor/1e7:,.0f} cr", {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "exchange", "operation": "equal", "right": "NSE"},
            {"left": "is_primary", "operation": "equal", "right": True},
            {"left": "market_cap_basic", "operation": "egreater", "right": floor},
        ],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": COLUMNS,
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, CONFIG["universe_size"]],
    })


def _index_attempt():
    return ("NIFTY500 constituents", {
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "options": {"lang": "en"},
        "symbols": {"symbolset": ["SYML:NSE;NIFTY500"]},
        "columns": COLUMNS,
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 550],
    })


def fetch_universe():
    """
    Pull the stock universe, in the order set by CONFIG["universe_mode"].

    The two modes answer different questions. Broad mode covers the whole
    TradingView industry taxonomy, which is what you want if the dashboard's
    job is spotting rotation into corners nobody is looking at yet. Index mode
    covers only what a fund could buy at size.

    Broad is the default because a row reading "1 stk / thin" is not an
    industry. It is the largest member of a group whose other members were cut
    off by the universe filter, and that silently distorts every number on the
    row -- the 52-week drawdown and the long-horizon returns most of all.

    Whichever mode is chosen, the other stays as a fallback, so a change at
    TradingView's end degrades the dashboard rather than breaking it.
    """
    if CONFIG["universe_mode"] == "nifty500":
        attempts = [_index_attempt(), _broad_attempt()]
    else:
        attempts = [_broad_attempt(), _index_attempt()]

    last_err = None
    for label, payload in attempts:
        try:
            data = _post(payload)
            rows = data.get("data") or []
            if len(rows) >= 50:
                print(f"  universe source: {label} ({len(rows)} rows)")
                return rows, label
            last_err = f"{label} returned only {len(rows)} rows"
        except Exception as e:            # noqa: BLE001
            last_err = f"{label}: {e}"
        time.sleep(1)

    raise RuntimeError(f"Could not fetch universe. Last error: {last_err}")


def fetch_benchmark():
    """CNX500 index itself, so 'relative to benchmark' means the real index."""
    payload = {
        "symbols": {"tickers": ["NSE:CNX500"], "query": {"types": ["index"]}},
        "columns": ["close", "change", "Perf.W", "Perf.1M", "Perf.3M",
                    "Perf.6M", "Perf.Y", "Perf.5Y"],
    }
    try:
        data = _post(payload)
        row = (data.get("data") or [{}])[0].get("d")
        if not row:
            return None
        keys = ["close", "change", "Perf.W", "Perf.1M", "Perf.3M",
                "Perf.6M", "Perf.Y", "Perf.5Y"]
        raw = dict(zip(keys, row))
        return {label: _num(raw.get(key)) for label, key in HORIZONS}
    except Exception as e:               # noqa: BLE001
        print(f"  ! CNX500 index quote unavailable ({e}); "
              f"falling back to capped universe aggregate")
        return None


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def capped_weights(mcaps, cap):
    """
    Iterative water-filling: clip any weight above `cap`, redistribute the
    excess proportionally across the uncapped names, repeat until stable.
    """
    total = sum(mcaps)
    if total <= 0:
        n = len(mcaps)
        return [1 / n] * n if n else []

    w = [m / total for m in mcaps]
    for _ in range(60):
        over = [i for i, x in enumerate(w) if x > cap + 1e-12]
        if not over:
            break
        excess = sum(w[i] - cap for i in over)
        for i in over:
            w[i] = cap
        free = [i for i in range(len(w)) if i not in set(over)]
        free_total = sum(w[i] for i in free)
        if free_total <= 0:
            break
        for i in free:
            w[i] += excess * (w[i] / free_total)
    s = sum(w)
    return [x / s for x in w] if s else w


def relative(industry_pct, bench_pct):
    """
    Relative strength, compounded rather than subtracted.

    Over 3 months the two are near-identical, but over 5 years subtraction
    is nonsense: an industry up 60% against a benchmark up 128% is not
    "-68%", it is -30% of the benchmark's ending value. Compounding keeps
    every horizon on the same footing.
    """
    if industry_pct is None or bench_pct is None:
        return None
    denom = 1 + bench_pct / 100
    if denom <= 0:
        return None
    return round(((1 + industry_pct / 100) / denom - 1) * 100, 2)


def weighted(values, weights):
    """Weighted mean that ignores missing values and renormalises."""
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None]
    if not pairs:
        return None
    tw = sum(w for _, w in pairs)
    if tw <= 0:
        return None
    return sum(v * w for v, w in pairs) / tw


# ----------------------------------------------------------------------------
# Classification — mood, cycle, stars
# ----------------------------------------------------------------------------

def score_mood(rs, dd52, breadth50, breadth200):
    """
    Mood answers: is this industry currently working? Score runs -6..+6.
    Deliberately blends relative strength (is it beating the index),
    participation (are most names above their averages), and damage
    (how far from the 52-week high).
    """
    s = 0
    if rs.get("3M") is not None:
        s += 2 if rs["3M"] > 0 else -2
    if rs.get("1M") is not None:
        s += 1 if rs["1M"] > 0 else -1
    if breadth200 is not None:
        s += 1 if breadth200 >= 50 else -1
    if breadth50 is not None:
        s += 1 if breadth50 >= 50 else -1
    if dd52 is not None:
        s += 1 if dd52 > -15 else -1

    if s >= CONFIG["mood_bullish_at"]:
        return "Bullish", s
    if s <= CONFIG["mood_bearish_at"]:
        return "Bearish", s
    return "Neutral", s


def classify_cycle(rs, dd52):
    """
    Where is this industry in its relative-strength lifecycle?

      Lagging  -> losing to the index on every horizon that matters
      Basing   -> 3M still negative, but 1M has turned
      Early    -> 3M positive, 1Y not yet — the turn is recent
      Mid      -> 3M and 6M and 1Y all positive, still room below the high
      Late     -> long run of outperformance, pinned at the highs, 1M decelerating

    Returned as a position 0..4 so the UI can place it on an arc.
    """
    r1m, r3m, r6m, r1y = (rs.get("1M"), rs.get("3M"), rs.get("6M"), rs.get("1Y"))

    if r3m is None:
        return {"stage": "Unrated", "action": "No data", "position": None}

    if r3m <= 0:
        if r1m is not None and r1m > 0:
            return {"stage": "Basing", "action": "Watch", "position": 1}
        return {"stage": "Lagging", "action": "Avoid", "position": 0}

    near_high = dd52 is not None and dd52 > CONFIG["late_near_high_pct"]
    long_run = r1y is not None and r1y > CONFIG["late_min_1y_rs"]
    decelerating = r1m is not None and r1m < (r3m / 3.0)

    if long_run and near_high and decelerating:
        return {"stage": "Late", "action": "Trim / trail", "position": 4}

    if r1y is not None and r1y <= CONFIG["early_max_1y_rs"]:
        return {"stage": "Early", "action": "Accumulate", "position": 2}

    if r6m is not None and r6m > 0:
        return {"stage": "Mid", "action": "Hold / add", "position": 3}

    return {"stage": "Early", "action": "Accumulate", "position": 2}


def is_star(stock, industry_perf_3m):
    """
    A star is a stock carrying its industry, not just riding it:
    beats the industry's own 3M return, above its 200-day, and not
    nursing a large drawdown.
    """
    p3 = stock["perf"].get("3M")
    if p3 is None or industry_perf_3m is None or p3 <= industry_perf_3m:
        return False
    if stock["dd52"] is None or stock["dd52"] < CONFIG["star_max_drawdown"]:
        return False
    if CONFIG["star_requires_above_sma200"]:
        if stock["close"] is None or stock["sma200"] is None:
            return False
        if stock["close"] <= stock["sma200"]:
            return False
    return True


# ----------------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------------

def parse_rows(rows):
    out = []
    for r in rows:
        d = dict(zip(COLUMNS, r.get("d") or []))
        sym = (r.get("s") or "").split(":")[-1]
        mcap = _num(d.get("market_cap_basic"))
        industry = d.get("industry")
        if not sym or not industry or not mcap:
            continue

        close = _num(d.get("close"))
        hi52 = _num(d.get("price_52_week_high"))
        dd52 = ((close / hi52) - 1) * 100 if close and hi52 else None

        traded = _num(d.get("Value.Traded"))
        avg_vol = _num(d.get("average_volume_10d_calc"))
        avg_traded = avg_vol * close if (avg_vol and close) else None

        out.append({
            "symbol": sym,
            "name": d.get("description") or sym,
            "sector": d.get("sector") or "Unclassified",
            "industry": industry,
            "mcap": mcap,
            "close": close,
            "sma50": _num(d.get("SMA50")),
            "sma200": _num(d.get("SMA200")),
            "rsi": _num(d.get("RSI")),
            "dd52": dd52,
            "traded": traded,
            "avg_traded": avg_traded,
            "perf": {label: _num(d.get(key)) for label, key in HORIZONS},
        })
    return out


def aggregate(stocks, cap):
    """Roll a list of stocks up into one capped-weight composite."""
    weights = capped_weights([s["mcap"] for s in stocks], cap)
    perf = {}
    for label, _ in HORIZONS:
        perf[label] = weighted([s["perf"].get(label) for s in stocks], weights)

    dd52 = weighted([s["dd52"] for s in stocks], weights)

    above50 = [s for s in stocks if s["close"] and s["sma50"]]
    above200 = [s for s in stocks if s["close"] and s["sma200"]]
    breadth50 = (100 * sum(1 for s in above50 if s["close"] > s["sma50"])
                 / len(above50)) if above50 else None
    breadth200 = (100 * sum(1 for s in above200 if s["close"] > s["sma200"])
                  / len(above200)) if above200 else None

    traded = sum(s["traded"] for s in stocks if s["traded"])
    avg_traded = sum(s["avg_traded"] for s in stocks if s["avg_traded"])
    turnover_mult = (traded / avg_traded) if avg_traded > 0 else None

    return {
        "perf": perf, "dd52": dd52, "weights": weights,
        "breadth50": breadth50, "breadth200": breadth200,
        "turnover_mult": turnover_mult, "traded": traded,
        "rsi": weighted([s["rsi"] for s in stocks], weights),
    }


def rs_path(industry_perf, bench_perf):
    """
    Reconstruct a relative-strength line from horizon returns.

    Price at t-X = now / (1 + perf_X). Do that for both the industry and the
    benchmark, take the ratio, index it to 100 at the earliest point. Gives a
    genuine 1-year RS path from seven numbers — no historical price series
    needed, which is what keeps this pipeline free.
    """
    order = ["1Y", "6M", "3M", "1M", "1W", "1D"]
    pts = []
    for h in order:
        ip, bp = industry_perf.get(h), bench_perf.get(h)
        if ip is None or bp is None:
            continue
        ratio = (1 + bp / 100) / (1 + ip / 100)   # RS level X ago, relative to now
        if ratio <= 0:
            continue
        pts.append({"days_ago": HORIZON_DAYS[h], "v": ratio})
    pts.append({"days_ago": 0, "v": 1.0})
    if len(pts) < 3:
        return []
    base = pts[0]["v"]
    return [{"d": p["days_ago"], "v": round(100 * p["v"] / base, 3)} for p in pts]


def build():
    print("Fetching universe from TradingView scanner ...")
    rows, source = fetch_universe()
    stocks = parse_rows(rows)
    print(f"  parsed {len(stocks)} stocks")

    print("Fetching CNX500 benchmark ...")
    bench_perf = fetch_benchmark()
    bench_source = "NSE:CNX500 index"
    if bench_perf is None:
        bench_perf = aggregate(stocks, CONFIG["max_stock_weight_in_benchmark"])["perf"]
        bench_source = "capped universe aggregate (index quote unavailable)"
    print(f"  benchmark: {bench_source}")

    total_mcap = sum(s["mcap"] for s in stocks)

    by_industry = {}
    for s in stocks:
        by_industry.setdefault(s["industry"], []).append(s)

    industries = []
    for name, members in sorted(by_industry.items()):
        agg = aggregate(members, CONFIG["max_stock_weight_in_industry"])
        perf = agg["perf"]

        rs = {}
        for label, _ in HORIZONS:
            rs[label] = relative(perf.get(label), bench_perf.get(label))

        mood, mood_score = score_mood(rs, agg["dd52"], agg["breadth50"], agg["breadth200"])
        cycle = classify_cycle(rs, agg["dd52"])

        members_sorted = sorted(members, key=lambda x: -x["mcap"])
        star_count = 0
        member_out = []
        for s, w in zip(members_sorted,
                        capped_weights([m["mcap"] for m in members_sorted],
                                       CONFIG["max_stock_weight_in_industry"])):
            star = is_star(s, perf.get("3M"))
            star_count += int(star)
            member_out.append({
                "symbol": s["symbol"], "name": s["name"],
                "weight": round(100 * w, 2),
                "mcapCr": round(s["mcap"] / 1e7, 0),
                "close": s["close"],
                "dd52": round(s["dd52"], 1) if s["dd52"] is not None else None,
                "rsi": round(s["rsi"], 0) if s["rsi"] is not None else None,
                "aboveSma200": (None if not (s["close"] and s["sma200"])
                                else s["close"] > s["sma200"]),
                "star": star,
                "perf": {k: (round(v, 2) if v is not None else None)
                         for k, v in s["perf"].items()},
            })

        industries.append({
            "industry": name,
            "sector": members_sorted[0]["sector"],
            "stockCount": len(members),
            "starCount": star_count,
            "thin": len(members) < CONFIG["thin_industry_stock_count"],
            "weight": round(100 * sum(m["mcap"] for m in members) / total_mcap, 2),
            "perf": {k: (round(v, 2) if v is not None else None)
                     for k, v in perf.items()},
            "rs": rs,
            "dd52": round(agg["dd52"], 1) if agg["dd52"] is not None else None,
            "breadth50": round(agg["breadth50"]) if agg["breadth50"] is not None else None,
            "breadth200": round(agg["breadth200"]) if agg["breadth200"] is not None else None,
            "turnoverMult": (round(agg["turnover_mult"], 2)
                             if agg["turnover_mult"] else None),
            "rsi": round(agg["rsi"]) if agg["rsi"] is not None else None,
            "mood": mood,
            "moodScore": mood_score,
            "cycle": cycle,
            "spark": rs_path(perf, bench_perf),
            "stocks": member_out,
        })

    industries.sort(key=lambda x: (x["rs"].get(CONFIG["relative_headline_horizon"])
                                   if x["rs"].get(CONFIG["relative_headline_horizon"])
                                   is not None else -999), reverse=True)

    # Rank change vs the previous run, so the dashboard can show movement.
    ranks = {ind["industry"]: i + 1 for i, ind in enumerate(industries)}
    prev = {}
    try:
        with open("history.json") as f:
            prev = json.load(f).get("ranks", {})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    for i, ind in enumerate(industries):
        ind["rank"] = i + 1
        p = prev.get(ind["industry"])
        ind["rankChange"] = (p - (i + 1)) if p else None

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universeSource": source,
        "benchmarkSource": bench_source,
        "benchmark": {k: (round(v, 2) if v is not None else None)
                      for k, v in bench_perf.items()},
        "universeStocks": len(stocks),
        "industryCount": len(industries),
        "config": CONFIG,
        "horizons": [h for h, _ in HORIZONS],
        "isSample": False,
        "industries": industries,
    }

    with open("data.json", "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    with open("history.json", "w") as f:
        json.dump({"date": payload["generatedAt"], "ranks": ranks}, f)

    print(f"\nWrote data.json — {len(industries)} industries, "
          f"{len(stocks)} stocks, benchmark 3M "
          f"{bench_perf.get('3M')}%")


if __name__ == "__main__":
    try:
        build()
    except Exception as e:                # noqa: BLE001
        print(f"\nFAILED: {e}", file=sys.stderr)
        sys.exit(1)
