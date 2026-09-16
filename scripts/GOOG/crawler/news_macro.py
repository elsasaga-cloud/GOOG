"""
新闻与宏观爬取 - 长期用
"""
import json, datetime, pathlib
from pathlib import Path

DATA_DIR=Path(__file__).resolve().parents[3] / "data" / "GOOG"

def create_news():
    news={
        "earnings": {
            "next": "2026-10-28 After Market Q3 2026 UNCONFIRMED",
            "last_Q2": "2026-07-22 EPS $9.11 vs $2.89 Revenue $119.8B +24% Cloud $24.8B +82%",
            "last_Q1": "2026-04-29 EPS $5.11 Revenue $109.9B Cloud $20B +63%"
        },
        "antitrust": {
            "liability": "Aug 2024 monopoly",
            "remedy": "Chrome sale proposed, not broken up yet, appeal to 2028+",
            "status": "Aug 9 2026 still on appeal, technical committee implementation"
        },
        "cloud": "Q2 Cloud $24.8B +82% backlog $514B Gemini 22B tokens/min 950M MAU",
        "youtube": "Q2 $11.055B +13% Q1 $9.88B +11% 1.7B World Cup viewers",
        "waymo": "$126B valuation Feb 2026 $16B round 355M annualized +127%",
        "macro": {
            "fomc": "2026 rate path neutral, GOOG rate sensitivity medium",
            "cpi": "Ad spend cyclicality",
            "sector": "Communication Services"
        },
        "date": datetime.datetime.now().isoformat()
    }
    out=DATA_DIR/"news"/f"GOOG_news_{datetime.datetime.now().strftime('%Y%m%d')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out,"w",encoding="utf-8") as f:
        json.dump(news, f, ensure_ascii=False, indent=2)
    print(f"Saved news {out}")

def main():
    create_news()

if __name__=="__main__":
    main()
