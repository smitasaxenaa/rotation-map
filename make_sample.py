#!/usr/bin/env python3
"""
make_sample.py — generates a demo data.json without hitting the network.

Uses the *same* aggregation and classification functions as build_data.py, so
what you see in the sample is exactly what the live pipeline will produce.
Values are synthetic. The dashboard shows a SAMPLE DATA banner when isSample.
"""

import json
import random
from datetime import datetime, timezone

import build_data as B

random.seed(11)

# (industry, sector, n_stocks, regime)  — regime shapes the synthetic returns
UNIVERSE = [
    ("Water Utilities", "Utilities", 3, "leader"),
    ("Computer Peripherals", "Electronic Technology", 4, "leader"),
    ("Electrical Products", "Producer Manufacturing", 18, "leader"),
    ("Aerospace & Defense", "Electronic Technology", 9, "leader"),
    ("Oilfield Services/Equipment", "Industrial Services", 7, "leader"),
    ("Containers/Packaging", "Process Industries", 26, "leader"),
    ("Food: Meat/Fish/Dairy", "Consumer Non-Durables", 8, "turn"),
    ("Food Distributors", "Distribution Services", 7, "turn"),
    ("Internet Retail", "Retail Trade", 7, "turn"),
    ("Hotels/Resorts/Cruise lines", "Consumer Services", 11, "turn"),
    ("Investment Banks/Brokers", "Finance", 14, "turn"),
    ("Wholesale Distributors", "Distribution Services", 9, "turn"),
    ("Major Banks", "Finance", 12, "mid"),
    ("Regional Banks", "Finance", 21, "mid"),
    ("Finance/Rental/Leasing", "Finance", 24, "mid"),
    ("Electric Utilities", "Utilities", 9, "mid"),
    ("Engineering & Construction", "Industrial Services", 22, "mid"),
    ("Building Products", "Producer Manufacturing", 16, "mid"),
    ("Steel", "Non-Energy Minerals", 14, "mid"),
    ("Auto Parts: OEM", "Producer Manufacturing", 19, "mid"),
    ("Pharmaceuticals: Major", "Health Technology", 13, "basing"),
    ("Pharmaceuticals: Generic", "Health Technology", 17, "basing"),
    ("Medical/Nursing Services", "Health Services", 8, "basing"),
    ("Specialty Stores", "Retail Trade", 10, "basing"),
    ("Textiles", "Consumer Durables", 12, "basing"),
    ("Agricultural Commodities/Milling", "Process Industries", 9, "basing"),
    ("Information Technology Services", "Technology Services", 24, "lagging"),
    ("Packaged Software", "Technology Services", 11, "lagging"),
    ("Telecommunications Equipment", "Electronic Technology", 6, "lagging"),
    ("Household/Personal Care", "Consumer Non-Durables", 13, "lagging"),
    ("Motor Vehicles", "Consumer Durables", 8, "lagging"),
    ("Chemicals: Specialty", "Process Industries", 23, "lagging"),
    ("Oil Refining/Marketing", "Energy Minerals", 7, "lagging"),
    ("Media Conglomerates", "Consumer Services", 6, "lagging"),
    ("Real Estate Development", "Finance", 15, "lagging"),
    ("Apparel/Footwear Retail", "Retail Trade", 9, "lagging"),
]

# regime -> (3M mean, 1Y mean, drawdown mean) in % terms
REGIME = {
    "leader":  (22, 70, -7),
    "turn":    (12, -3, -21),
    "mid":     (10, 24, -12),
    "basing":  (1, -8, -19),
    "lagging": (-9, -18, -27),
}

BENCH = {"1D": 0.42, "1W": 0.9, "1M": 2.1, "3M": 5.6,
         "6M": 9.4, "1Y": 6.8, "5Y": 128.0}


def make_stock(industry, sector, i, regime, big):
    m3, m1y, mdd = REGIME[regime]
    j = random.gauss(0, 1)
    p3 = m3 + random.gauss(0, 9)
    p1y = m1y + random.gauss(0, 22) + j * 6
    p6 = p3 * random.uniform(1.1, 2.0) + random.gauss(0, 8)
    p1m = p3 / random.uniform(2.2, 4.5) + random.gauss(0, 4)
    p1w = p1m / random.uniform(2.5, 5.0) + random.gauss(0, 1.6)
    p1d = p1w / random.uniform(2.0, 5.0) + random.gauss(0, 0.8)
    p5y = max(-60, p1y * random.uniform(1.6, 5.0) + random.gauss(0, 60))

    close = round(random.uniform(80, 4200), 1)
    dd = min(-0.1, mdd + random.gauss(0, 9))
    hi = close / (1 + dd / 100)
    sma200 = close * (1 - (p1y / 100) * random.uniform(0.15, 0.4))
    sma50 = close * (1 - (p3 / 100) * random.uniform(0.2, 0.5))
    mcap = (big * random.uniform(0.35, 1.0)) if i == 0 else \
        big * random.uniform(0.02, 0.45)

    return {
        "symbol": f"{''.join(w[0] for w in industry.split()[:3]).upper()}{i+1:02d}",
        "name": f"{industry.split(':')[0].split('/')[0].strip()} Co {i+1} Ltd",
        "sector": sector, "industry": industry,
        "mcap": mcap, "close": close,
        "sma50": sma50, "sma200": sma200,
        "rsi": max(12, min(90, 50 + p3 * 0.7 + random.gauss(0, 8))),
        "dd52": dd,
        "traded": random.uniform(2e7, 9e9),
        "avg_traded": random.uniform(2e7, 9e9),
        "perf": {"1D": p1d, "1W": p1w, "1M": p1m, "3M": p3,
                 "6M": p6, "1Y": p1y, "5Y": p5y},
    }


def main():
    stocks = []
    for industry, sector, n, regime in UNIVERSE:
        big = random.uniform(4e11, 9e12)
        for i in range(n):
            stocks.append(make_stock(industry, sector, i, regime, big))

    total_mcap = sum(s["mcap"] for s in stocks)
    by_industry = {}
    for s in stocks:
        by_industry.setdefault(s["industry"], []).append(s)

    industries = []
    for name, members in by_industry.items():
        agg = B.aggregate(members, B.CONFIG["max_stock_weight_in_industry"])
        perf = agg["perf"]
        rs = {h: B.relative(perf.get(h), BENCH[h]) for h, _ in B.HORIZONS}
        mood, score = B.score_mood(rs, agg["dd52"], agg["breadth50"], agg["breadth200"])
        cycle = B.classify_cycle(rs, agg["dd52"])

        ms = sorted(members, key=lambda x: -x["mcap"])
        ws = B.capped_weights([m["mcap"] for m in ms],
                              B.CONFIG["max_stock_weight_in_industry"])
        stars, out = 0, []
        for s, w in zip(ms, ws):
            star = B.is_star(s, perf.get("3M"))
            stars += int(star)
            out.append({
                "symbol": s["symbol"], "name": s["name"],
                "weight": round(100 * w, 2),
                "mcapCr": round(s["mcap"] / 1e7, 0),
                "close": s["close"], "dd52": round(s["dd52"], 1),
                "rsi": round(s["rsi"]),
                "aboveSma200": s["close"] > s["sma200"],
                "star": star,
                "perf": {k: round(v, 2) for k, v in s["perf"].items()},
            })

        industries.append({
            "industry": name, "sector": members[0]["sector"],
            "stockCount": len(members), "starCount": stars,
            "thin": len(members) < B.CONFIG["thin_industry_stock_count"],
            "weight": round(100 * sum(m["mcap"] for m in members) / total_mcap, 2),
            "perf": {k: round(v, 2) for k, v in perf.items()},
            "rs": rs,
            "dd52": round(agg["dd52"], 1),
            "breadth50": round(agg["breadth50"]) if agg["breadth50"] is not None else None,
            "breadth200": round(agg["breadth200"]) if agg["breadth200"] is not None else None,
            "turnoverMult": round(agg["turnover_mult"], 2) if agg["turnover_mult"] else None,
            "rsi": round(agg["rsi"]),
            "mood": mood, "moodScore": score, "cycle": cycle,
            "spark": B.rs_path(perf, BENCH),
            "stocks": out,
        })

    industries.sort(key=lambda x: x["rs"].get("3M", -999), reverse=True)
    for i, ind in enumerate(industries):
        ind["rank"] = i + 1
        ind["rankChange"] = random.choice([None, 0, 1, -1, 2, -2, 4, -3, 6, -5])

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universeSource": "SAMPLE (synthetic)",
        "benchmarkSource": "SAMPLE (synthetic CNX500)",
        "benchmark": BENCH,
        "universeStocks": len(stocks),
        "industryCount": len(industries),
        "config": B.CONFIG,
        "horizons": [h for h, _ in B.HORIZONS],
        "isSample": True,
        "industries": industries,
    }
    with open("data.json", "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"sample data.json — {len(industries)} industries, {len(stocks)} stocks")


if __name__ == "__main__":
    main()
