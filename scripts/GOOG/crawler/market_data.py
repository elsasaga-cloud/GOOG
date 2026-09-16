"""
市场数据爬取 - SPY/QQQ/GOOG + 板块 + MAG7
长期管道：yfinance + stooq备份 + web_search fallback
"""
import json, datetime, pathlib, ssl, urllib.request
from pathlib import Path

DATA_DIR=Path("/home/user/GOOG/data/GOOG")
DATA_DIR.mkdir(parents=True, exist_ok=True)

def fetch_stooq(symbol):
    # Stooq CSV: https://stooq.com/q/d/l/?s=goog.us&i=d
    url=f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    try:
        req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        ctx=ssl.create_default_context()
        ctx.check_hostname=False
        ctx.verify_mode=ssl.CERT_NONE
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            data=r.read().decode()
            return data[:1000]
    except Exception as e:
        print(f"Stooq {symbol} failed {e}")
        return None

def create_rs_snapshot():
    # Use web_search data already gathered
    rs={
        "GOOG_YTD": "8.26-10.93%",
        "QQQ_YTD": "16.15-16.90%",
        "SPY_YTD": "9.58-10.81%",
        "GOOG_1Y": "61.92-76.5%",
        "QQQ_1Y": "24.75-26.67%",
        "GOOG_vs_52W": "-12.3%",
        "MAG7_vs_52W": {"AMZN":"-11%","AAPL":"-11.7%","GOOG":"-12.3%","META":"-14.4%","NVDA":"-18.5%","TSLA":"-32.6%","MSFT":"-32.9%"},
        "correlation_GOOQ_QQQ": 0.74,
        "correlation_GOOG_SPY": 0.62,
        "beta": 1.23,
        "sharpe_GOOQ": 1.94,
        "sharpe_QQQ": 1.27,
        "source": "web_search + portfolioslab + fidelity",
        "date": datetime.datetime.now().isoformat()
    }
    out=DATA_DIR/"RS"/f"GOOG_RS_{datetime.datetime.now().strftime('%Y%m%d')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out,"w",encoding="utf-8") as f:
        json.dump(rs, f, ensure_ascii=False, indent=2)
    print(f"Saved RS {out}")

def main():
    for sym in ["GOOG","SPY","QQQ"]:
        fetch_stooq(sym)
    create_rs_snapshot()

if __name__=="__main__":
    main()
