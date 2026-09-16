"""
美股筹码四维分析脚本 v5.2
========================================
v5.2 修复与改进（基于 v5.1）：
  [P0] IV 不再信任 Yahoo impliedVolatility 字段：用买卖价中值二分反解 BSM IV（近月HPE
       曾因此显示"IV 1.8%"——Yahoo字段当天整体返回0.018级垃圾值，8/20显示61.8%是正常的）。
       Yahoo值降级为对照组，异常（<5%或>400%或偏离自算4倍）时红字告警
  [P1] 新增隐含Move：近月ATM straddle中值/现价，自动标注是否含财报窗口
  [P1] 新增GEX近似：BSM gamma×OI×S²×1%（dealer常规符号），正/负GEX+pin候选档
  [P1] 新增PEAD：近8季财报隔夜gap/当日/两日累计（盘后口径），附平均绝对波幅
  [P1] 新增执行层快照：ATR14(Wilder)+MA20/30/60+20日高低+挂单间隔/止损宽度参考
  [P2] 新增IV分位自建：每次运行追加近月IV快照到脚本目录iv_history/CSV，攒5次输出百分位
  [P1] Max Pain 抗污染双修：改回标准定义（到期给付最小化 argmin——v5.1 误用 argmax，
       把链顶档位当 MP，首跑实测三个到期全显示 $75=CBOE 口径 $54 的链顶假象）；
       且仅统计现价 ±30% 以内档位，屏蔽 Yahoo 链远档幽灵 OI
  [P2] 财报日历日期差改按日历日：财报日当天（北京时间凌晨）跑，不再误显示"上次财报 N 天前"
  [P2] RS对比默认 SPY → SPY,SOXX

v5.1 修复与改进（基于 v5.0）：
  [P0] 内部人交易过滤非市场操作（期权行权/赠予/税款留存），净买卖改用美元Value列
  [P0] 并发超时 25s → 38s，防止网络慢时误降级到 akshare/CDP
  [P1] 财报日期支持区间显示（2026-09-01 ~ 2026-09-04），明确标注"市场预估"
  [P1] IV 变化趋势：近14天→近7天历史波动率对比，判断波动率是否在加速上升
  [P2] Max Pain 加 OI 阈值检查，总OI<5000时标注"流动性不足，参考价值有限"
  [其他] cache_set 写入失败重试、缓存目录路径输出、--compare 帮助文字优化

v5.0 原有功能（完整保留）：
  ★ 综合评分系统（0-100分，五级评级）
    - Volume Profile  25%（安全垫梯度打分）
    - Anchored VWAP   30%（偏离度+锚点时效折扣）
    - IV结构          25%（IV水平+C/P双维度）
    - 机构持仓13F     15%（数据缺失给中性60分）
    - 做空+内部人      5%（新增维度）
  ★ 做空比例（Short Interest）追踪
  ★ 内部人交易监控（Form 4，近30天净买卖）
  ★ 期权最大痛点（Max Pain）计算
  ★ 财报日历联动（自动读取预估财报日，距今天数提醒）
  ★ 相对强弱（RS）vs 标普500 / 同类股
  ★ 本地数据缓存（默认1小时，--no-cache禁用）
  ★ 并发数据拉取（ThreadPoolExecutor，降低等待时间）
  ★ 降级模式：某维度数据缺失时继续输出其余维度
  ★ 离场条件自动量化预警

数据源架构（每层并发）：
  Layer 1: yfinance
  Layer 2: curl_cffi（完整Chrome指纹+Cookie）
  Layer 3: akshare（备用行情源）
  Layer 4: Playwright CDP（连接已运行Chrome）

用法：
  python chip_analysis_v5.1.py
  python chip_analysis_v5.1.py --ticker NVDA
  python chip_analysis_v5.1.py --ticker HPE --anchor 2026-07-15
  python chip_analysis_v5.1.py --ticker AAPL --anchor 2026-07-01 --anchor2 2026-05-01
  python chip_analysis_v5.1.py --ticker TSLA --period 180 --period2 30 --compare DELL
  python chip_analysis_v5.1.py --ticker HPE --no-cache

  ★ Playwright兜底需先启动Chrome：
  chrome.exe --remote-debugging-port=9222

依赖：
  pip install yfinance pandas numpy curl_cffi rich akshare playwright
  playwright install chromium
"""

import argparse
import json
import math
import os
import sys
import time
import warnings
import hashlib
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

# ── 依赖检查 ──────────────────────────────────────────────────────────────────
MISSING = []
for pkg in ["yfinance", "pandas", "numpy", "rich"]:
    try:
        __import__(pkg)
    except ImportError:
        MISSING.append(pkg)

if MISSING:
    print(f"缺少依赖：{' '.join(MISSING)}\npip install yfinance pandas numpy rich")
    sys.exit(1)

import yfinance as yf
import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

try:
    from curl_cffi import requests as cf_requests
    HAS_CURL = True
except ImportError:
    HAS_CURL = False

try:
    import akshare as ak
    HAS_AK = True
except ImportError:
    HAS_AK = False

console = Console()
CDP_PORT = 9222

# ── 缓存配置 ──────────────────────────────────────────────────────────────────
CACHE_DIR  = Path(os.environ.get("TEMP", "/tmp")) / "chip_cache"
CACHE_TTL  = 3600   # 秒，1小时
USE_CACHE  = True   # 由 --no-cache 覆盖

CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_key(tag: str) -> Path:
    h = hashlib.md5(tag.encode()).hexdigest()[:12]
    return CACHE_DIR / f"{h}.pkl"


def cache_get(tag: str):
    if not USE_CACHE:
        return None
    p = _cache_key(tag)
    if p.exists() and (time.time() - p.stat().st_mtime) < CACHE_TTL:
        try:
            with open(p, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    return None


def cache_set(tag: str, data):
    if not USE_CACHE:
        return
    p = _cache_key(tag)
    # [v5.1] 写入失败时重试一次，避免文件锁导致数据丢失
    for attempt in range(2):
        try:
            with open(p, "wb") as f:
                pickle.dump(data, f)
            return
        except Exception:
            if attempt == 0:
                time.sleep(0.1)


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 2: curl_cffi
# ═══════════════════════════════════════════════════════════════════════════════

_YF_COOKIE_CACHE = {}


def _get_yf_cookies() -> dict:
    global _YF_COOKIE_CACHE
    if _YF_COOKIE_CACHE:
        return _YF_COOKIE_CACHE
    if not HAS_CURL:
        return {}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    try:
        r1 = cf_requests.get(
            "https://finance.yahoo.com/", headers=headers,
            impersonate="chrome120", timeout=15,
        )
        cookies = dict(r1.cookies)
        r2 = cf_requests.get(
            "https://query1.finance.yahoo.com/v1/test/csrfToken",
            headers={**headers, "Referer": "https://finance.yahoo.com/"},
            cookies=cookies, impersonate="chrome120", timeout=10,
        )
        crumb = ""
        try:
            crumb = r2.json().get("crumb", "")
        except Exception:
            pass
        _YF_COOKIE_CACHE = {"cookies": cookies, "crumb": crumb}
        return _YF_COOKIE_CACHE
    except Exception:
        return {}


def curl_history(ticker: str, period_days: int, interval: str = "1d") -> "pd.DataFrame | None":
    if not HAS_CURL:
        return None
    now = int(time.time())
    p1  = now - period_days * 86400
    sd  = _get_yf_cookies()
    cookies, crumb = sd.get("cookies", {}), sd.get("crumb", "")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://finance.yahoo.com/",
        "Accept": "application/json, text/plain, */*",
    }
    for host in ["query1", "query2"]:
        url = (
            f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?period1={p1}&period2={now}&interval={interval}&includeAdjustedClose=true"
        )
        if crumb:
            url += f"&crumb={crumb}"
        try:
            resp = cf_requests.get(url, headers=headers, cookies=cookies,
                                   impersonate="chrome120", timeout=20)
            if resp.status_code != 200:
                continue
            data   = resp.json()
            result = data.get("chart", {}).get("result", [])
            if not result:
                continue
            r     = result[0]
            ts    = r.get("timestamp", [])
            quote = r.get("indicators", {}).get("quote", [{}])[0]
            df = pd.DataFrame({
                "Date":   pd.to_datetime(ts, unit="s"),
                "Open":   quote.get("open", []),
                "High":   quote.get("high", []),
                "Low":    quote.get("low", []),
                "Close":  quote.get("close", []),
                "Volume": quote.get("volume", []),
            }).dropna()
            df.set_index("Date", inplace=True)
            df.index = df.index.tz_localize(None)
            return df
        except Exception:
            pass
    return None


def curl_options(ticker: str, exp: str) -> "tuple | None":
    if not HAS_CURL:
        return None
    try:
        ts = int(datetime.strptime(exp, "%Y-%m-%d").timestamp())
    except Exception:
        return None
    sd = _get_yf_cookies()
    cookies, crumb = sd.get("cookies", {}), sd.get("crumb", "")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://finance.yahoo.com/",
        "Accept": "application/json, text/plain, */*",
    }
    url = f"https://query1.finance.yahoo.com/v7/finance/options/{ticker}?date={ts}"
    if crumb:
        url += f"&crumb={crumb}"
    try:
        resp = cf_requests.get(url, headers=headers, cookies=cookies,
                               impersonate="chrome120", timeout=20)
        if resp.status_code != 200:
            return None
        data   = resp.json()
        result = data.get("optionChain", {}).get("result", [])
        if not result:
            return None
        chain = result[0].get("options", [{}])[0]
        return pd.DataFrame(chain.get("calls", [])), pd.DataFrame(chain.get("puts", []))
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 3: akshare
# ═══════════════════════════════════════════════════════════════════════════════

def ak_history(ticker: str, period_days: int) -> "pd.DataFrame | None":
    if not HAS_AK:
        return None
    try:
        end   = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=period_days)).strftime("%Y%m%d")
        df = ak.stock_us_hist(symbol=ticker, period="daily",
                               start_date=start, end_date=end, adjust="qfq")
        if df is None or df.empty:
            return None
        col_map = {"日期":"Date","开盘":"Open","最高":"High","最低":"Low","收盘":"Close","成交量":"Volume"}
        df = df.rename(columns=col_map)
        if "Date" not in df.columns:
            df.columns = ["Date","Open","High","Low","Close","Volume"] + list(df.columns[6:])
        df["Date"] = pd.to_datetime(df["Date"])
        df.set_index("Date", inplace=True)
        df.index = df.index.tz_localize(None)
        for c in ["Open","High","Low","Close","Volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df[["Open","High","Low","Close","Volume"]].dropna()
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 4: Playwright CDP
# ═══════════════════════════════════════════════════════════════════════════════

def cdp_history(ticker: str, period_days: int) -> "pd.DataFrame | None":
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", CDP_PORT), timeout=2)
        s.close()
    except Exception:
        return None
    captured = {}
    now = int(time.time())
    p1  = now - period_days * 86400
    xhr_url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?period1={p1}&period2={now}&interval=1d&includeAdjustedClose=true"
    )
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            ctx     = browser.contexts[0] if browser.contexts else browser.new_context()
            page    = ctx.new_page()
            def on_resp(response):
                if "v8/finance/chart" in response.url and ticker.upper() in response.url:
                    try:
                        captured["data"] = response.json()
                    except Exception:
                        pass
            page.on("response", on_resp)
            page.goto(xhr_url, wait_until="networkidle", timeout=25000)
            time.sleep(1)
            if not captured.get("data"):
                try:
                    body = page.evaluate("() => document.body.innerText")
                    captured["data"] = json.loads(body)
                except Exception:
                    pass
            page.close()
    except Exception:
        return None
    if not captured.get("data"):
        return None
    try:
        result = captured["data"].get("chart", {}).get("result", [])
        if not result:
            return None
        r     = result[0]
        ts    = r.get("timestamp", [])
        quote = r.get("indicators", {}).get("quote", [{}])[0]
        df = pd.DataFrame({
            "Date":   pd.to_datetime(ts, unit="s"),
            "Open":   quote.get("open", []),
            "High":   quote.get("high", []),
            "Low":    quote.get("low", []),
            "Close":  quote.get("close", []),
            "Volume": quote.get("volume", []),
        }).dropna()
        df.set_index("Date", inplace=True)
        df.index = df.index.tz_localize(None)
        return df
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 统一数据入口（并发 + 缓存）
# ═══════════════════════════════════════════════════════════════════════════════

def get_history(ticker: str, period_days: int = 365, interval: str = "1d",
                silent: bool = False) -> "pd.DataFrame | None":
    tag = f"hist_{ticker}_{period_days}_{interval}"
    cached = cache_get(tag)
    if cached is not None:
        if not silent:
            console.print(f"  [dim]缓存命中 ({period_days}天)[/dim] [green]✓ {len(cached)}条[/green]")
        return cached

    layers = [
        ("yfinance",      lambda: _yf_history(ticker, period_days, interval)),
        ("curl_cffi",     lambda: curl_history(ticker, period_days, interval)),
        ("akshare",       lambda: ak_history(ticker, period_days)),
        ("Playwright CDP",lambda: cdp_history(ticker, period_days)),
    ]

    # 并发尝试前两层，失败才降级
    result = None
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(fn): name for name, fn in layers[:2]}
        # [v5.1] 并发超时从25s改为38s，防止网络慢时误降级
        for fut in as_completed(futures, timeout=38):
            name = futures[fut]
            try:
                df = fut.result()
                if df is not None and not df.empty:
                    if not silent:
                        console.print(f"  [dim]{name}[/dim] [green]✓ {len(df)}条[/green]")
                    result = df
                    break
            except Exception:
                pass

    # 降级到 Layer 3/4
    if result is None:
        for name, fn in layers[2:]:
            if not silent:
                console.print(f"  [dim]{name}...[/dim]", end="")
            try:
                df = fn()
                if df is not None and not df.empty:
                    if not silent:
                        console.print(f" [green]✓ {len(df)}条[/green]")
                    result = df
                    break
                if not silent:
                    console.print(" [dim]空[/dim]")
            except Exception as e:
                if not silent:
                    console.print(f" [red]✗ {e}[/red]")

    if result is not None:
        cache_set(tag, result)
    return result


def _yf_history(ticker, period_days, interval):
    t  = yf.Ticker(ticker)
    ps = f"{period_days}d" if period_days <= 729 else "2y"
    df = t.history(period=ps, interval=interval)
    if df is not None and not df.empty:
        df.index = df.index.tz_localize(None)
    return df


def get_current_price(ticker: str) -> "float | None":
    tag = f"price_{ticker}"
    cached = cache_get(tag)
    if cached is not None:
        return cached
    try:
        info = yf.Ticker(ticker).fast_info
        p = getattr(info, "last_price", None)
        if p:
            cache_set(tag, float(p))
            return float(p)
    except Exception:
        pass
    df = get_history(ticker, period_days=5, silent=True)
    if df is not None and not df.empty:
        p = float(df["Close"].iloc[-1])
        cache_set(tag, p)
        return p
    return None


def get_option_chain(ticker: str, exp: str) -> "tuple | None":
    tag = f"chain_{ticker}_{exp}"
    cached = cache_get(tag)
    if cached is not None:
        return cached
    try:
        chain = yf.Ticker(ticker).option_chain(exp)
        if chain and not chain.calls.empty:
            result = (chain.calls, chain.puts)
            cache_set(tag, result)
            return result
    except Exception:
        pass
    result = curl_options(ticker, exp)
    if result:
        cache_set(tag, result)
    return result


def get_expirations(ticker: str) -> list:
    tag = f"exps_{ticker}"
    cached = cache_get(tag)
    if cached is not None:
        return cached
    try:
        exps = list(yf.Ticker(ticker).options)
        cache_set(tag, exps)
        return exps
    except Exception:
        return []


def get_inst_holders(ticker: str) -> "pd.DataFrame | None":
    tag = f"inst_{ticker}"
    cached = cache_get(tag)
    if cached is not None:
        return cached
    try:
        df = yf.Ticker(ticker).institutional_holders
        if df is not None and not df.empty:
            cache_set(tag, df)
            return df
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 新增：做空比例
# ═══════════════════════════════════════════════════════════════════════════════

def get_short_interest(ticker: str) -> dict:
    """
    从 yfinance 获取做空比例相关数据。
    返回 dict: short_pct, short_ratio, shares_short, update_date
    """
    tag = f"short_{ticker}"
    cached = cache_get(tag)
    if cached is not None:
        return cached

    result = {}
    try:
        info = yf.Ticker(ticker).info
        sp   = info.get("shortPercentOfFloat", None)
        sr   = info.get("shortRatio", None)
        ss   = info.get("sharesShort", None)
        spd  = info.get("sharesShortPreviousMonthDate", None)
        ssp  = info.get("sharesShortPriorMonth", None)
        result = {
            "short_pct":   round(sp * 100, 2) if sp else None,
            "short_ratio": round(sr, 2)        if sr else None,
            "shares_short":ss,
            "shares_short_prev": ssp,
            "update_date": datetime.fromtimestamp(spd).strftime("%Y-%m-%d") if spd else "N/A",
        }
        if ss and ssp and ssp > 0:
            result["chg_pct"] = round((ss - ssp) / ssp * 100, 2)
        cache_set(tag, result)
    except Exception:
        pass
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 新增：内部人交易
# ═══════════════════════════════════════════════════════════════════════════════

def get_insider_trades(ticker: str) -> "pd.DataFrame | None":
    """
    从 yfinance 获取内部人交易（Form 4）。
    """
    tag = f"insider_{ticker}"
    cached = cache_get(tag)
    if cached is not None:
        return cached
    try:
        df = yf.Ticker(ticker).insider_transactions
        if df is not None and not df.empty:
            cache_set(tag, df)
            return df
    except Exception:
        pass
    return None

# [v5.1] 内部人交易过滤：仅保留公开市场买卖，剔除期权行权/赠予/税款留存等
_VALID_TXN = {
    "sale", "purchase", "sell", "buy", "s", "p",
    "sale - open market", "purchase - open market",
}
_SKIP_TXN = {
    "gift", "option exercise", "tax withholding", "will",
    "bona fide gift", "exercise", "automatic sale", "return",
    "reclassification",
}


def _is_real_market_trade(txn_str: str) -> bool:
    """判断是否为真实公开市场买卖"""
    if not txn_str or not isinstance(txn_str, str):
        return False
    t = txn_str.strip().lower()
    for skip in _SKIP_TXN:
        if skip in t:
            return False
    for valid in _VALID_TXN:
        if valid in t:
            return True
    return False


def _filter_insider_market_trades(df: pd.DataFrame) -> "tuple[pd.DataFrame, bool]":
    """
    [v5.1] 过滤真实市场买卖。
    返回 (filtered_df, has_txn_col)
    has_txn_col=False 表示没有 Transaction 列，无法过滤。
    """
    txn_col = next((c for c in ["Transaction", "Text", "Type", "transaction"]
                    if c in df.columns), None)
    if txn_col is None:
        return df.copy(), False
    mask = df[txn_col].apply(_is_real_market_trade)
    return df[mask].copy(), True


# ═══════════════════════════════════════════════════════════════════════════════
# 新增：财报日历
# ═══════════════════════════════════════════════════════════════════════════════

def get_earnings_dates(ticker: str) -> dict:
    """
    [v5.1] 获取财报日期，支持区间显示。
    返回 {date_start, date_end, is_range, source}
    """
    tag = f"earn2_{ticker}"
    cached = cache_get(tag)
    if cached is not None:
        return cached

    result = {"date_start": None, "date_end": None, "is_range": False, "source": "unknown"}
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            cache_set(tag, result)
            return result

        dates = []
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date", [])
            if not isinstance(raw, (list, tuple)):
                raw = [raw]
            dates = [pd.to_datetime(d) for d in raw if d is not None]
        elif isinstance(cal, pd.DataFrame) and not cal.empty:
            if "Earnings Date" in cal.index:
                raw = cal.loc["Earnings Date"]
                dates = [pd.to_datetime(v)
                         for v in (raw if hasattr(raw, "__iter__") else [raw])]

        dates = [d for d in dates if not pd.isna(d)]
        if not dates:
            cache_set(tag, result)
            return result

        result["date_start"] = dates[0].to_pydatetime()
        result["date_end"]   = dates[-1].to_pydatetime() if len(dates) >= 2 else dates[0].to_pydatetime()
        result["is_range"]   = len(dates) >= 2 and (dates[-1] - dates[0]).days > 0
        result["source"]     = "yfinance"
    except Exception:
        pass

    cache_set(tag, result)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 新增：相对强弱（RS）
# ═══════════════════════════════════════════════════════════════════════════════

def get_relative_strength(ticker: str, compare: str, period_days: int = 30) -> dict:
    """
    计算 ticker 相对 compare 的强弱：
    - 近N天涨跌幅对比
    - RS比率（ticker累计涨幅/compare累计涨幅）
    - 趋势：RS过去N天是在上升还是下降
    """
    result = {}
    try:
        h1 = get_history(ticker,  period_days=period_days + 5, silent=True)
        h2 = get_history(compare, period_days=period_days + 5, silent=True)
        if h1 is None or h2 is None or h1.empty or h2.empty:
            return result

        # 对齐时间轴
        combined = pd.DataFrame({
            "t": h1["Close"],
            "c": h2["Close"],
        }).dropna()
        if len(combined) < 5:
            return result

        t_ret = (combined["t"].iloc[-1] / combined["t"].iloc[0] - 1) * 100
        c_ret = (combined["c"].iloc[-1] / combined["c"].iloc[0] - 1) * 100
        rs    = t_ret - c_ret   # 相对超额收益

        # RS趋势：前半段 vs 后半段
        mid   = len(combined) // 2
        rs_h1 = (combined["t"].iloc[mid] / combined["t"].iloc[0] - 1) - \
                (combined["c"].iloc[mid] / combined["c"].iloc[0] - 1)
        rs_h2 = (combined["t"].iloc[-1] / combined["t"].iloc[mid] - 1) - \
                (combined["c"].iloc[-1] / combined["c"].iloc[mid] - 1)
        trend = "上升" if rs_h2 > rs_h1 else "下降"

        result = {
            "ticker_ret":  round(t_ret, 2),
            "compare_ret": round(c_ret, 2),
            "rs":          round(rs,    2),
            "trend":       trend,
            "period_days": period_days,
        }
    except Exception:
        pass
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 新增：Max Pain 计算
# ═══════════════════════════════════════════════════════════════════════════════

MIN_OI_FOR_MAX_PAIN = 5000   # [v5.1] 总OI低于此值标注流动性不足


def calc_max_pain(calls: pd.DataFrame, puts: pd.DataFrame,
                  spot: "float | None" = None) -> "tuple[float | None, bool]":
    """
    [v5.1] 返回 (max_pain_price, is_reliable)
    is_reliable=False 表示总OI不足5000，数据仅供参考，不纳入策略建议。
    [v5.2] 修复：Max Pain = 到期给付最小化的档位（argmin，最多期权归零）。
           v5.1 误用 argmax，恒指链顶档（2026-09-03首跑实测 9/4/9/11/9/25 全显示 $75，
           CBOE 同链口径 $54）。另加 spot ±30% 带过滤，屏蔽远档幽灵 OI。
    """
    try:
        calls = calls.copy(); puts = puts.copy()
        total_oi    = int(calls["openInterest"].fillna(0).sum() +
                          puts["openInterest"].fillna(0).sum())
        is_reliable = total_oi >= MIN_OI_FOR_MAX_PAIN

        if spot:
            lo, hi = spot * 0.7, spot * 1.3
            calls = calls[(calls["strike"] >= lo) & (calls["strike"] <= hi)]
            puts  = puts[(puts["strike"] >= lo) & (puts["strike"] <= hi)]
        strikes = sorted(set(calls["strike"].tolist() + puts["strike"].tolist()))
        pain = {}
        for s in strikes:
            c_loss = calls[calls["strike"] < s].apply(
                lambda r: (s - r["strike"]) * (r.get("openInterest") or 0), axis=1).sum()
            p_loss = puts[puts["strike"] > s].apply(
                lambda r: (r["strike"] - s) * (r.get("openInterest") or 0), axis=1).sum()
            pain[s] = c_loss + p_loss
        if not pain:
            return None, False
        return min(pain, key=pain.get), is_reliable   # [v5.2] argmin（v5.1为max，系bug）
    except Exception:
        return None, False


# ═══════════════════════════════════════════════════════════════════════════════
# [v5.2] BSM 内核：自算 IV / Gamma / 隐含Move / GEX
# ═══════════════════════════════════════════════════════════════════════════════
# 背景：Yahoo 期权链的 impliedVolatility 字段时常整体返回垃圾值（如全链0.018的近零常数，
# 2026-08-27/31 的 HPE 近月链即是，显示成"IV 1.8%"），无法靠单位换算修复。
# v5.2 改为用买卖价中值反解 BSM IV，Yahoo 值仅作对照并在异常时告警。
# 口径注：欧式 BSM 近似美式+股息未建模，ATM附近误差约1-2pct，方向性对比足够；
# 无风险利率取常数近似，不影响 IV 数量级。

RISK_FREE = 0.043   # 近似无风险利率（3个月T-Bill量级）


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bsm_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if is_call else max(0.0, K - S)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    if is_call:
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bsm_iv(price: float, S: float, K: float, T: float, r: float = RISK_FREE,
           is_call: bool = True) -> "float | None":
    """二分法反解隐含波动率（小数口径，1.62=162%）。价格≤内在价值或输入异常返回 None。"""
    if price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    intrinsic = max(0.0, (S - K) if is_call else (K - S))
    if price <= intrinsic + 1e-4:
        return None
    lo, hi = 0.01, 5.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _bsm_price(S, K, T, r, mid, is_call) > price:
            hi = mid
        else:
            lo = mid
    iv = 0.5 * (lo + hi)
    return iv if 0.011 <= iv <= 4.99 else None


def _bsm_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / sq
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * sq)


def _years_to_expiry(exp: str) -> float:
    try:
        exp_dt = datetime.strptime(exp, "%Y-%m-%d")
    except Exception:
        return 7 / 365
    days = (exp_dt - datetime.now()).total_seconds() / 86400
    return max(days, 0.25) / 365   # 当日到期按 0.25 天计


def _mid_price_series(df: "pd.DataFrame") -> "pd.Series":
    """买卖价中值；中值无效(≤0)时回退 lastPrice。"""
    bid = pd.to_numeric(df["bid"], errors="coerce").fillna(0) if "bid" in df.columns else pd.Series(0.0, index=df.index)
    ask = pd.to_numeric(df["ask"], errors="coerce").fillna(0) if "ask" in df.columns else pd.Series(0.0, index=df.index)
    mid = (bid + ask) / 2
    if "lastPrice" in df.columns:
        lp = pd.to_numeric(df["lastPrice"], errors="coerce")
        mid = mid.where(mid > 0, lp)
    return mid


def _yahoo_iv_pct(df: "pd.DataFrame", spot: float) -> "float | None":
    """Yahoo 原始 IV（×100，最近3档中位数），仅作对照组。"""
    if "impliedVolatility" not in df.columns:
        return None
    d = df.copy()
    d["dist"] = (d["strike"] - spot).abs()
    vals = pd.to_numeric(d.nsmallest(3, "dist")["impliedVolatility"], errors="coerce").dropna()
    vals = vals[(vals > 0) & (vals < 10)] * 100
    return float(vals.median()) if len(vals) else None


def chain_iv_report(calls: "pd.DataFrame", puts: "pd.DataFrame", spot: float,
                    exp: str) -> dict:
    """
    [v5.2] 自算 ATM IV：距现价最近的3档call+3档put，买卖价中值反解BSM IV取中位数。
    返回（百分数口径）：call_iv / put_iv / atm_iv / yahoo_atm / flag
      flag = 'yahoo_bad'（Yahoo字段异常，已用自算替代）| 'no_data' | ''
    """
    T = _years_to_expiry(exp)

    def side_ivs(df: "pd.DataFrame", is_call: bool) -> list:
        df = df.copy()
        df["__mid"] = _mid_price_series(df)
        df = df[pd.to_numeric(df["__mid"], errors="coerce") > 0]
        if df.empty:
            return []
        df["dist"] = (df["strike"] - spot).abs()
        ivs = []
        for _, row in df.nsmallest(3, "dist").iterrows():
            iv = bsm_iv(float(row["__mid"]), spot, float(row["strike"]), T, RISK_FREE, is_call)
            if iv is not None:
                ivs.append(iv * 100)
        return ivs

    c_ivs = side_ivs(calls, True)
    p_ivs = side_ivs(puts, False)

    def med(v):
        return float(np.median(v)) if v else None

    call_iv, put_iv = med(c_ivs), med(p_ivs)
    atm_iv = med(c_ivs + p_ivs) if (c_ivs or p_ivs) else None

    y_c, y_p = _yahoo_iv_pct(calls, spot), _yahoo_iv_pct(puts, spot)
    ylist = [v for v in (y_c, y_p) if v is not None]
    yahoo_atm = float(np.median(ylist)) if ylist else None

    flag = ""
    if yahoo_atm is not None:
        if yahoo_atm < 5 or yahoo_atm > 400:
            flag = "yahoo_bad"
        elif atm_iv and not (0.25 <= yahoo_atm / atm_iv <= 4.0):
            flag = "yahoo_bad"
    elif atm_iv is None:
        flag = "no_data"

    return {"call_iv": call_iv, "put_iv": put_iv, "atm_iv": atm_iv,
            "yahoo_atm": yahoo_atm, "flag": flag, "T": T}


def calc_implied_move(calls: "pd.DataFrame", puts: "pd.DataFrame",
                      spot: float) -> "dict | None":
    """[v5.2] ATM straddle 中值 / 现价 = 市场定价的到期隐含波幅。"""
    try:
        c = calls.copy(); p = puts.copy()
        c["dist"] = (c["strike"] - spot).abs()
        p["dist"] = (p["strike"] - spot).abs()
        k = float(c.nsmallest(1, "dist")["strike"].iloc[0])
        prow = p[p["strike"] == k]
        if prow.empty:
            return None
        cmid = float(_mid_price_series(c[c["strike"] == k]).iloc[0])
        pmid = float(_mid_price_series(prow).iloc[0])
        if cmid > 0 and pmid > 0 and spot > 0:
            return {"strike": k, "straddle": cmid + pmid,
                    "move_pct": (cmid + pmid) / spot * 100}
    except Exception:
        pass
    return None


def calc_gex(calls: "pd.DataFrame", puts: "pd.DataFrame", spot: float,
             exp: str) -> dict:
    """
    [v5.2] GEX 近似（dealer 常规符号：客户买call卖put → 做市商多call空put）：
      GEX$ = Σ gamma × OI × 100 × S² × 1%
    正=对冲抑制波动（价格被pin向高gamma档）；负=对冲放大波动（趋势日易加速）。
    量级/方向参考：符号假设+未含非到期日组合，非精确敞口。
    """
    T = _years_to_expiry(exp)
    pins: dict = {}

    def side(df: "pd.DataFrame", sign: int, is_call: bool) -> float:
        total = 0.0
        if df is None or df.empty:
            return 0.0
        mids = _mid_price_series(df)
        for i, (_, row) in enumerate(df.iterrows()):
            K = float(row["strike"])
            oi = float(row.get("openInterest", 0) or 0)
            if oi <= 0:
                continue
            yiv = None
            if "impliedVolatility" in df.columns and pd.notna(row.get("impliedVolatility")):
                yiv = float(row["impliedVolatility"])
            iv = bsm_iv(float(mids.iloc[i]), spot, K, T, RISK_FREE, is_call)
            if iv is None and yiv is not None and 0.05 <= yiv <= 3.0:
                iv = yiv   # 自算失败时用Yahoo值兜底（已做合理区间过滤）
            if iv is None:
                continue
            g = _bsm_gamma(spot, K, T, RISK_FREE, iv)
            v = g * oi * 100 * spot * spot * 0.01 * sign
            total += v
            pins[K] = pins.get(K, 0.0) + v
        return total

    net = side(calls, +1, True) + side(puts, -1, False)
    top_pins = sorted(pins.items(), key=lambda kv: abs(kv[1]), reverse=True)[:3]
    return {"net": net, "pins": top_pins, "T": T}


# ═══════════════════════════════════════════════════════════════════════════════
# [v5.2] 执行层快照：ATR14 + 均线 + 挂单参考（为挂策略单不看盘的场景服务）
# ═══════════════════════════════════════════════════════════════════════════════

def calc_atr14(hist: "pd.DataFrame", n: int = 14) -> "float | None":
    """Wilder ATR（ewm alpha=1/n）。"""
    try:
        h, l, c = hist["High"], hist["Low"], hist["Close"]
        pc = c.shift(1)
        tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        atr = float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])
        return atr if atr > 0 else None
    except Exception:
        return None


def print_execution_panel(ticker: str):
    hist = get_history(ticker, period_days=365, silent=True)
    if hist is None or len(hist) < 25:
        return
    cur  = float(hist["Close"].iloc[-1])
    atr  = calc_atr14(hist)
    ma20 = float(hist["Close"].rolling(20).mean().iloc[-1])
    ma30 = float(hist["Close"].rolling(30).mean().iloc[-1]) if len(hist) >= 30 else None
    ma60 = float(hist["Close"].rolling(60).mean().iloc[-1]) if len(hist) >= 60 else None
    hi20 = float(hist["High"].iloc[-20:].max())
    lo20 = float(hist["Low"].iloc[-20:].min())

    lines = [f"  现价 [bold yellow]${cur:.2f}[/bold yellow]   "
             f"ATR14 [bold]{('$%.2f (%.1f%%)' % (atr, atr / cur * 100)) if atr else 'N/A'}[/bold]",
             f"  MA20 ${ma20:.2f}   MA30 {('$%.2f' % ma30) if ma30 else 'N/A'}   "
             f"MA60 {('$%.2f' % ma60) if ma60 else 'N/A'} [dim](R8观察线)[/dim]",
             f"  20日高/低：${hi20:.2f} / ${lo20:.2f}"]
    if atr:
        lines.append(f"  挂单参考：限价单间隔 0.5×ATR≈[cyan]${atr * 0.5:.2f}[/cyan]   "
                     f"止损宽度 1×ATR≈[cyan]${atr:.2f}[/cyan]   "
                     f"现价-1ATR=[dim]${cur - atr:.2f}[/dim]")
    console.print()
    console.print(Panel("\n".join(lines),
                        title="[bold magenta]🎯 执行层快照（挂单参考）[/bold magenta]",
                        border_style="magenta"))
    console.print()


# ═══════════════════════════════════════════════════════════════════════════════
# [v5.2] PEAD：历史财报反应（隔夜跳空/当日/两日累计）
# ═══════════════════════════════════════════════════════════════════════════════

def get_earnings_reactions(ticker: str, hist: "pd.DataFrame | None" = None,
                           n_last: int = 8) -> "pd.DataFrame | None":
    """
    用 yfinance earnings_dates（已发布季）+ 日线，计算每次财报：
      隔夜gap% / 财报反应日全天% / 两日累计%
    口径：盘后发布假设——反应日=财报日后的第一个交易日；盘前发布的样本会整体后移一天，
    使用时以实际公告时间核对（HPE 近年均为盘后发布）。
    """
    try:
        ed = yf.Ticker(ticker).earnings_dates
    except Exception:
        return None
    if ed is None or ed.empty:
        return None
    if hist is None or len(hist) < 30:
        return None
    ed = ed.copy()
    ed.index = pd.to_datetime(ed.index).tz_localize(None).normalize()
    if "Reported EPS" in ed.columns:
        ed = ed[ed["Reported EPS"].notna()]
    ed = ed.sort_index().tail(n_last)

    h = hist.copy()
    h.index = pd.to_datetime(h.index).tz_localize(None)
    rows = []
    for dt, _ in ed.iterrows():
        after = h[h.index > dt]
        if after.empty:
            continue
        rx_dt = after.index[0]
        prior = h[h.index < rx_dt]
        if prior.empty:
            continue
        pc  = float(prior["Close"].iloc[-1])
        o   = float(after["Open"].iloc[0])
        c1  = float(after["Close"].iloc[0])
        gap = (o / pc - 1) * 100
        day = (c1 / pc - 1) * 100
        two = (float(after["Close"].iloc[1]) / pc - 1) * 100 if len(after) >= 2 else day
        rows.append({"earn_date": dt.strftime("%Y-%m-%d"), "gap_pct": gap,
                     "day_pct": day, "two_pct": two})
    if not rows:
        return None
    return pd.DataFrame(rows)


def render_pead(ticker: str):
    """[v5.2] PEAD 表格渲染（挂在⑦财报日历节内）。"""
    hist = get_history(ticker, period_days=400, silent=True)
    rx = get_earnings_reactions(ticker, hist)
    if rx is None or rx.empty:
        console.print("  [dim]PEAD 历史财报反应：数据不可用（待补，不影响其余维度）[/dim]")
        return
    t = Table(box=box.SIMPLE_HEAD, header_style="bold cyan",
              title="PEAD 历史财报反应 [dim]（盘后口径：反应日=财报次一交易日）[/dim]")
    t.add_column("财报日", style="dim")
    t.add_column("隔夜gap%", justify="right")
    t.add_column("当日%", justify="right")
    t.add_column("两日累计%", justify="right")
    for _, r in rx.iterrows():
        gc = "green" if r["gap_pct"] >= 0 else "red"
        dc = "green" if r["day_pct"] >= 0 else "red"
        t.add_row(r["earn_date"],
                  f"[{gc}]{r['gap_pct']:+.1f}[/{gc}]",
                  f"[{dc}]{r['day_pct']:+.1f}[/{dc}]",
                  f"{r['two_pct']:+.1f}")
    console.print(t)
    avg_gap = rx["gap_pct"].abs().mean()
    avg_day = rx["day_pct"].abs().mean()
    console.print(f"  平均|隔夜gap| [bold]{avg_gap:.1f}%[/bold]   平均|当日| [bold]{avg_day:.1f}%[/bold]"
                  f"  [dim]→ 隐含Move低于此=期权偏便宜，高于此=偏贵[/dim]")


# ═══════════════════════════════════════════════════════════════════════════════
# [v5.2] IV 分位自建：每次运行追加快照到脚本目录 iv_history/，攒5次输出百分位
# ═══════════════════════════════════════════════════════════════════════════════

IV_LOG_DIR = Path(__file__).resolve().parent / "iv_history"


def log_iv_snapshot(ticker: str, exp: str, atm_iv: "float | None",
                    hv_14: "float | None" = None) -> "dict | None":
    """
    记录近月IV快照（同日同到期去重覆盖），返回 {count, pct_below}。
    pct_below：历史快照中低于当前值的占比（%）；count<5 时为 None（样本不足）。
    """
    if atm_iv is None:
        return None
    try:
        IV_LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = IV_LOG_DIR / f"{ticker}_iv_log.csv"
        rows = {}
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines()[1:]:
                parts = line.split(",")
                if len(parts) >= 3:
                    hv = None
                    if len(parts) >= 4 and parts[3] not in ("", "None"):
                        try:
                            hv = float(parts[3])
                        except ValueError:
                            hv = None
                    rows[(parts[0], parts[1])] = (float(parts[2]), hv)
        today = datetime.now().strftime("%Y-%m-%d")
        rows[(today, exp)] = (float(atm_iv), hv_14)
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("date,expiry,atm_iv_pct,hv14_pct\n")
            for (d, e), (iv, hv) in sorted(rows.items()):
                fh.write(f"{d},{e},{iv},{hv if hv is not None else ''}\n")
        vals = [v[0] for v in rows.values()]
        pct_below = (sum(1 for v in vals if v < float(atm_iv)) / len(vals) * 100) if len(vals) >= 5 else None
        return {"count": len(vals), "pct_below": pct_below}
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# [v5.1] IV 变化趋势估算（用HV代理IV方向）
# ═══════════════════════════════════════════════════════════════════════════════

def _estimate_iv_trend(ticker: str) -> dict:
    """
    [v5.1] 用近14天/7天历史波动率（HV）估算IV趋势方向。
    真实IV历史需付费数据源；HV方向与IV方向高度相关，可作为免费替代。
    返回 {hv_14, hv_7, hv_trend, note}
    """
    result = {}
    try:
        hist = get_history(ticker, period_days=30, silent=True)
        if hist is None or hist.empty or len(hist) < 15:
            return result
        hist      = hist.copy()
        rets      = hist["Close"].pct_change().dropna()
        hv_14     = float(rets.iloc[-14:].std() * (252 ** 0.5) * 100)
        hv_7      = float(rets.iloc[-7:].std()  * (252 ** 0.5) * 100)
        ratio     = hv_7 / hv_14 if hv_14 > 0 else 1.0
        if ratio > 1.08:
            trend = "上升"
            note  = "短期波动率加速上升，财报或事件预期正在定价，期权卖方需谨慎"
        elif ratio < 0.92:
            trend = "下降"
            note  = "短期波动率回落，市场不确定性降低"
        else:
            trend = "平稳"
            note  = "波动率无明显方向性变化"
        result = {"hv_14": round(hv_14, 1), "hv_7": round(hv_7, 1),
                  "hv_trend": trend, "note": note}
    except Exception:
        pass
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 锚点智能识别（沿用v4.0逻辑）
# ═══════════════════════════════════════════════════════════════════════════════

def _days_ago(dt: pd.Timestamp) -> int:
    return (pd.Timestamp.now() - dt).days


def _find_anchor(hist: pd.DataFrame, prefer_recent_days: int = 90,
                 threshold: float = 0.08) -> "tuple[pd.Timestamp, str]":
    hist = hist.copy()
    hist["ret"] = hist["Close"].pct_change()
    now    = pd.Timestamp.now()
    cutoff = now - pd.Timedelta(days=prefer_recent_days)

    recent    = hist[hist.index >= cutoff]
    big_recent = recent[recent["ret"] > threshold].sort_index()
    if not big_recent.empty:
        dt     = big_recent.index[-1]
        reason = f"近{prefer_recent_days}天最近大涨日 (+{big_recent['ret'].iloc[-1]*100:.1f}%)"
        return dt, reason

    big_all = hist[hist["ret"] > threshold].sort_index()
    if not big_all.empty:
        dt     = big_all.index[-1]
        reason = f"全局最近大涨日 (+{big_all['ret'].iloc[-1]*100:.1f}%)"
        return dt, reason

    dt     = hist.index[max(0, len(hist) - 180)]
    reason = "未找到大涨日，使用近180天起点"
    return dt, reason


def _anchor_freshness(anchor_dt: pd.Timestamp) -> tuple:
    """返回 (days, discount, warn_text)"""
    days = _days_ago(anchor_dt)
    if days > 90:
        return days, 0.85, f"[bold red]⚠ 锚点距今 {days} 天，偏旧！建议 --anchor 更新[/bold red]"
    elif days > 60:
        return days, 0.92, f"[yellow]△ 锚点距今 {days} 天，可考虑更新[/yellow]"
    else:
        return days, 1.00, f"[green]✓ 锚点距今 {days} 天，时效良好[/green]"


# ═══════════════════════════════════════════════════════════════════════════════
# ╔══════════════════════════════════════════════════════════════════╗
# ║               综合评分引擎（v5.0核心）                          ║
# ╚══════════════════════════════════════════════════════════════════╝
# ═══════════════════════════════════════════════════════════════════════════════

class ScoreEngine:
    """
    权重分配：
      VP        25%
      AVWAP     30%
      IV        25%
      13F       15%
      做空+内部人  5%
    """
    WEIGHTS = {"vp": 25, "avwap": 30, "iv": 25, "inst": 15, "short_insider": 5}

    def __init__(self):
        self.scores   = {}   # {dim: raw_score 0-100}
        self.details  = {}   # {dim: detail_str}
        self.missing  = []   # 缺失维度

    # ── 各维度打分 ────────────────────────────────────────────────

    def score_vp(self, gap_pct: "float | None"):
        """安全垫梯度打分"""
        if gap_pct is None:
            self.missing.append("vp")
            return
        if gap_pct > 20:
            s = 90 + min(10, (gap_pct - 20) * 0.5)
        elif gap_pct > 10:
            s = 70 + (gap_pct - 10) * 2
        elif gap_pct > 0:
            s = 50 + gap_pct * 2
        elif gap_pct > -5:
            s = 30 + (gap_pct + 5) * 4
        else:
            s = max(0, 30 + gap_pct * 2)
        self.scores["vp"]  = round(min(100, max(0, s)), 1)
        self.details["vp"] = f"安全垫 {gap_pct:+.1f}%"

    def score_avwap(self, diff_pct: "float | None", days_ago: int = 0):
        """偏离度打分 + 锚点时效折扣"""
        if diff_pct is None:
            self.missing.append("avwap")
            return
        if diff_pct > 15:
            s = 90 + min(10, (diff_pct - 15) * 0.67)
        elif diff_pct > 10:
            s = 80 + (diff_pct - 10)
        elif diff_pct > 5:
            s = 70 + (diff_pct - 5) * 2
        elif diff_pct > 0:
            s = 55 + diff_pct * 3
        elif diff_pct > -5:
            s = 30 + (diff_pct + 5) * 5
        else:
            s = max(0, 30 + diff_pct * 2)

        # 时效折扣
        if days_ago > 90:
            discount = 0.85
        elif days_ago > 60:
            discount = 0.92
        else:
            discount = 1.00
        s = s * discount

        self.scores["avwap"]  = round(min(100, max(0, s)), 1)
        disc_str = f" (×{discount} 时效折扣)" if discount < 1 else ""
        self.details["avwap"] = f"偏离AVWAP {diff_pct:+.1f}%{disc_str}"

    def score_iv(self, atm_iv: "float | None", cp_ratio: "float | None"):
        """IV水平 + C/P各占一半"""
        if atm_iv is None:
            self.missing.append("iv")
            return
        # IV子分
        if atm_iv < 20:
            iv_s = 95
        elif atm_iv < 25:
            iv_s = 90
        elif atm_iv < 35:
            iv_s = 75 + (35 - atm_iv)
        elif atm_iv < 50:
            iv_s = 40 + (50 - atm_iv) * 2.33
        else:
            iv_s = max(0, 40 - (atm_iv - 50) * 1.5)

        # C/P子分
        cp = cp_ratio or 1.0
        if cp > 1.5:
            cp_s = 95
        elif cp > 1.2:
            cp_s = 85
        elif cp > 1.0:
            cp_s = 75
        elif cp > 0.8:
            cp_s = 60
        elif cp > 0.6:
            cp_s = 40
        else:
            cp_s = 20

        s = iv_s * 0.5 + cp_s * 0.5
        self.scores["iv"]  = round(min(100, max(0, s)), 1)
        cp_str = f"{cp_ratio:.2f}" if cp_ratio else "N/A"
        self.details["iv"] = f"IV={atm_iv:.1f}% C/P={cp_str}"

    def score_inst(self, holders_df: "pd.DataFrame | None"):
        """机构持仓：数据缺失给中性60分"""
        if holders_df is None or holders_df.empty:
            self.scores["inst"]  = 60.0
            self.details["inst"] = "数据缺失，给中性分"
            return
        # 简单评估：前10大占比之和
        pct_col = None
        for c in ["% Out", "pctHeld"]:
            if c in holders_df.columns:
                pct_col = c
                break
        if pct_col:
            total_pct = holders_df[pct_col].head(10).fillna(0).astype(float).sum()
            total_pct *= 100 if total_pct < 1 else 1   # 有时是小数有时是百分比
            total_pct  = min(total_pct, 100)
            if total_pct > 60:
                s = 90
            elif total_pct > 40:
                s = 75
            elif total_pct > 20:
                s = 60
            else:
                s = 50
            self.scores["inst"]  = float(s)
            self.details["inst"] = f"前10大机构占比 {total_pct:.1f}%"
        else:
            self.scores["inst"]  = 65.0
            self.details["inst"] = "持仓数据可用（无比例列）"

    def score_short_insider(self, short_pct: "float | None",
                            short_chg: "float | None",
                            insider_net: "float | None"):
        """做空+内部人综合打分（各占一半）"""
        # 做空子分
        sp = short_pct or 5.0
        if sp < 2:
            ss = 95
        elif sp < 5:
            ss = 80
        elif sp < 8:
            ss = 65
        elif sp < 12:
            ss = 45
        else:
            ss = 25
        # 做空趋势加减分
        if short_chg is not None:
            if short_chg < -10:
                ss = min(100, ss + 10)   # 空头大幅撤退，加分
            elif short_chg > 10:
                ss = max(0,   ss - 10)   # 空头大幅增加，减分

        # 内部人子分
        if insider_net is None:
            ins = 60   # 数据缺失给中性
        elif insider_net > 0:
            ins = 85   # 净买入
        elif insider_net > -500000:
            ins = 65   # 小额减持
        elif insider_net > -2000000:
            ins = 45   # 中等减持
        else:
            ins = 25   # 大额减持

        s = ss * 0.5 + ins * 0.5
        self.scores["short_insider"]  = round(min(100, max(0, s)), 1)
        sp_str  = f"{short_pct:.2f}%" if short_pct else "N/A"
        ins_str = f"净{'买入' if (insider_net or 0)>=0 else '卖出'}" if insider_net is not None else "N/A"
        self.details["short_insider"] = f"做空{sp_str} 内部人{ins_str}"

    # ── 汇总 ──────────────────────────────────────────────────────

    def total(self) -> float:
        if not self.scores:
            return 0.0
        total_w = sum(self.WEIGHTS[k] for k in self.scores)
        if total_w == 0:
            return 0.0
        weighted = sum(self.scores[k] * self.WEIGHTS[k] for k in self.scores)
        return round(weighted / total_w, 1)

    def grade(self, score: float) -> tuple:
        """返回 (等级文字, 颜色, emoji)"""
        if score >= 85:
            return "强势健康", "bold green",  "🟢"
        elif score >= 70:
            return "温和偏多", "green",        "🟡"
        elif score >= 55:
            return "中性观望", "yellow",       "🟠"
        elif score >= 40:
            return "偏弱警惕", "bold red",     "🔴"
        else:
            return "危险区间", "red",          "⛔"

    def render(self):
        """打印评分表格 + 综合分"""
        total = self.total()
        grade, color, emoji = self.grade(total)

        dim_names = {
            "vp":            "Volume Profile",
            "avwap":         "Anchored VWAP",
            "iv":            "IV结构",
            "inst":          "机构持仓13F",
            "short_insider": "做空+内部人",
        }

        tbl = Table(box=box.SIMPLE_HEAD, header_style="bold magenta", title="📊 综合评分明细")
        tbl.add_column("维度",   min_width=16)
        tbl.add_column("权重",   justify="right", style="dim")
        tbl.add_column("得分",   justify="right")
        tbl.add_column("状态",   justify="center")
        tbl.add_column("详情",   style="dim")

        for key, w in self.WEIGHTS.items():
            name = dim_names[key]
            if key in self.missing:
                tbl.add_row(name, f"{w}%", "[dim]--[/dim]", "[dim]缺失[/dim]", "数据获取失败")
                continue
            s = self.scores.get(key, 60)
            if s >= 75:
                sc, st = "green",  "●"
            elif s >= 55:
                sc, st = "yellow", "◑"
            else:
                sc, st = "red",    "○"
            tbl.add_row(name, f"{w}%",
                        f"[{sc}]{s:.1f}[/{sc}]",
                        f"[{sc}]{st}[/{sc}]",
                        self.details.get(key, ""))

        console.print(tbl)

        # 综合分 Banner
        bar_len = int(total / 2)
        bar     = "█" * bar_len + "░" * (50 - bar_len)
        console.print()
        console.print(Panel(
            f"  {emoji} [{color}]{total:.1f} / 100  —  {grade}[/{color}]\n\n"
            f"  [{color}]{bar}[/{color}]",
            title="[bold magenta]综合评分[/bold magenta]",
            border_style=color.replace("bold ", ""),
        ))

        if self.missing:
            console.print(f"  [dim]缺失维度：{', '.join(self.missing)}（已按可用维度重新加权）[/dim]")
        console.print()
        return total


# ═══════════════════════════════════════════════════════════════════════════════
# ① 13F 机构持仓
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_13f(ticker: str, engine: ScoreEngine):
    console.rule("[bold cyan]① 13F 机构持仓[/bold cyan]")
    console.print("[dim]长线基金持续增持=真实锁仓；对冲基金涌入=筹码质量下降预警。[/dim]\n")

    df = get_inst_holders(ticker)
    engine.score_inst(df)

    if df is not None and not df.empty:
        t = Table(box=box.SIMPLE_HEAD, header_style="bold cyan")
        t.add_column("机构名称",   min_width=28)
        t.add_column("持仓股数",   justify="right", style="cyan")
        t.add_column("报告日期",   justify="right", style="dim")
        t.add_column("价值($)",    justify="right", style="green")
        t.add_column("占比%",      justify="right", style="yellow")
        for _, row in df.head(10).iterrows():
            shares = f"{int(row.get('Shares', 0)):,}"  if pd.notna(row.get('Shares'))  else "N/A"
            date_v = str(row.get('Date Reported', row.get('Date', 'N/A')))[:10]
            val    = f"${int(row.get('Value', 0)):,}"  if pd.notna(row.get('Value'))    else "N/A"
            pct_r  = row.get('% Out', row.get('pctHeld', None))
            pct    = f"{float(pct_r)*100:.2f}%" if pct_r is not None and pd.notna(pct_r) else "N/A"
            t.add_row(str(row.get('Holder', row.get('Name', 'N/A'))), shares, date_v, val, pct)
        console.print(t)
    else:
        console.print("[yellow]  13F数据暂不可用，备选：[/yellow]")
        console.print(f"  [cyan]https://www.quiverquant.com/stock/{ticker}[/cyan]")
        console.print(f"  [cyan]https://finviz.com/quote.ashx?t={ticker}[/cyan]")
        console.print("  [dim]（已给予中性分 60/100）[/dim]")
    console.print()


# ═══════════════════════════════════════════════════════════════════════════════
# ② Volume Profile（双周期）
# ═══════════════════════════════════════════════════════════════════════════════

def _render_vp(hist: pd.DataFrame, cur: float, bins: int, label: str) -> dict:
    hist = hist.copy()
    hist["mid"] = (hist["High"] + hist["Low"]) / 2
    vp = hist.groupby(pd.cut(hist["mid"], bins=bins), observed=True)["Volume"].sum().reset_index()
    vp.columns = ["price_bin", "volume"]
    vp = vp.dropna().sort_values("price_bin")
    vp["pct"]    = vp["volume"] / vp["volume"].sum() * 100
    vp["is_hvn"] = vp["volume"] >= vp["volume"].quantile(0.70)

    max_v, W = vp["volume"].max(), 40
    # 筹码集中度
    hvn_pct = vp[vp["is_hvn"]]["pct"].sum()
    hvn_w   = vp[vp["is_hvn"]].apply(
        lambda r: r["price_bin"].right - r["price_bin"].left, axis=1).sum() if not vp[vp["is_hvn"]].empty else 0
    concentration = round(hvn_w / cur * 100, 1) if cur > 0 else 0

    console.print(f"\n  [bold]── {label}（{len(hist)}根K线）── 筹码集中度 {concentration}%[/bold]")

    for _, row in vp.iterrows():
        lo, hi   = row["price_bin"].left, row["price_bin"].right
        is_cur   = lo <= cur <= hi
        bar_len  = int((row["volume"] / max_v) * W)
        color    = "bold yellow" if is_cur else ("green" if row["is_hvn"] else "dim")
        marker   = " ◄ 当前" if is_cur else (" ★HVN" if row["is_hvn"] else "")
        bar      = "█" * bar_len + "░" * (W - bar_len)
        console.print(f"  ${lo:>6.1f}-${hi:<6.1f} [{color}]{bar}[/{color}] [dim]{row['pct']:4.1f}%[/dim][yellow]{marker}[/yellow]")

    hvn = vp[vp["is_hvn"]]
    result = {}
    if not hvn.empty:
        hvn_lo = min(r.left  for r in hvn["price_bin"])
        hvn_hi = max(r.right for r in hvn["price_bin"])
        gap    = cur - hvn_hi
        gp     = gap / cur * 100
        console.print(f"\n  主力成本密集区：[green]${hvn_lo:.2f}–${hvn_hi:.2f}[/green]  "
                      f"占总成交 [cyan]{hvn_pct:.1f}%[/cyan]  "
                      f"集中度 [dim]{concentration}%[/dim]")
        if concentration < 30:
            console.print("  [green]▸ 筹码高度集中（<30%），主力控盘特征[/green]")
        elif concentration < 50:
            console.print("  [dim]▸ 筹码集中度适中（30-50%）[/dim]")
        else:
            console.print("  [yellow]▸ 筹码较分散（>50%），市场分歧较大[/yellow]")

        if gap > 0:
            console.print(f"  安全垫：[green]+${gap:.2f} (+{gp:.1f}%)[/green] → 支撑充足")
        elif gap > -cur * 0.05:
            console.print("  [yellow]价格接近密集区上沿，支撑位验证中[/yellow]")
        else:
            console.print("  [red]价格已跌入成本密集区，关注量能能否守住[/red]")

        result = {"hvn_lo": hvn_lo, "hvn_hi": hvn_hi,
                  "gap_pct": gp, "vp": vp, "current_price": cur,
                  "concentration": concentration}
    return result


def analyze_vp(ticker: str, period_days: int = 365, period2: int = 60,
               bins: int = 20, engine: ScoreEngine = None):
    console.rule("[bold cyan]② Volume Profile 成交量价格分布[/bold cyan]")
    console.print("[dim]成交越密集的价格区间=持仓成本越集中=真实支撑底线。双周期对比揭示近期换手情况。[/dim]")

    console.print("\n  [dim]获取历史K线...[/dim]")
    hist_long = get_history(ticker, period_days=period_days)
    if hist_long is None or hist_long.empty:
        console.print("[red]  数据获取失败，跳过VP分析[/red]\n")
        if engine:
            engine.missing.append("vp")
        return None

    cur = float(hist_long["Close"].iloc[-1])
    console.print(f"\n  当前价格：[bold yellow]${cur:.2f}[/bold yellow]")

    res_long  = _render_vp(hist_long, cur, bins, f"全局周期 {period_days}天")

    cutoff     = hist_long.index[-1] - pd.Timedelta(days=period2)
    hist_short = hist_long[hist_long.index >= cutoff]
    res_short  = {}
    if len(hist_short) >= 10:
        res_short = _render_vp(hist_short, cur, max(bins // 2, 10), f"近期周期 {period2}天")

    if res_long and res_short:
        lo_g, hi_g = res_long.get("hvn_lo", 0),  res_long.get("hvn_hi", 0)
        lo_s, hi_s = res_short.get("hvn_lo", 0), res_short.get("hvn_hi", 0)
        console.print()
        if lo_s >= lo_g * 0.95 and hi_s <= hi_g * 1.05:
            console.print("  [green]▸ 近期与全局HVN重叠，支撑区一致性高[/green]")
        else:
            console.print(f"  [yellow]▸ 近期HVN(${lo_s:.2f}–${hi_s:.2f})与全局(${lo_g:.2f}–${hi_g:.2f})偏移，近期筹码在换手[/yellow]")

    if engine:
        engine.score_vp(res_long.get("gap_pct") if res_long else None)

    console.print()
    return res_long if res_long else res_short


# ═══════════════════════════════════════════════════════════════════════════════
# ③ 双锚点 Anchored VWAP
# ═══════════════════════════════════════════════════════════════════════════════

def _calc_avwap(hist_after: pd.DataFrame) -> pd.Series:
    df = hist_after.copy()
    df["tp"]      = (df["High"] + df["Low"] + df["Close"]) / 3
    df["tp_vol"]  = df["tp"] * df["Volume"]
    df["cum_tpv"] = df["tp_vol"].cumsum()
    df["cum_vol"] = df["Volume"].cumsum()
    return df["cum_tpv"] / df["cum_vol"]


def _render_avwap_section(hist, anchor_dt, anchor_label, reason, cur):
    before       = hist[hist.index < anchor_dt]
    anchor_price = float(before["Close"].iloc[-1]) if not before.empty else float(hist["Close"].iloc[0])
    after        = hist[hist.index >= anchor_dt].copy()
    if after.empty:
        console.print(f"  [red]{anchor_label} 后无数据[/red]")
        return None

    avwap_s = _calc_avwap(after)
    avwap   = float(avwap_s.iloc[-1])
    diff    = cur - avwap
    dp      = diff / avwap * 100
    days_n, discount, warn_txt = _anchor_freshness(anchor_dt)

    console.print(f"\n  [bold]{anchor_label}[/bold]  "
                  f"锚点：[cyan]{anchor_dt.strftime('%Y-%m-%d')}[/cyan]  "
                  f"[dim]({days_n}天前 | {reason})[/dim]")
    console.print(f"  {warn_txt}")
    console.print(f"  前收价：[yellow]${anchor_price:.2f}[/yellow]  "
                  f"AVWAP：[bold green]${avwap:.2f}[/bold green]  "
                  f"当前：[bold yellow]${cur:.2f}[/bold yellow]")
    if diff > 0:
        console.print(f"  → [green]+${diff:.2f}（+{dp:.1f}%）高于AVWAP，锚点后买方均盈利，无被套抛压[/green]")
    else:
        console.print(f"  → [red]{diff:.2f}（{dp:.1f}%）低于AVWAP，锚点后买方平均亏损，存在抛压[/red]")

    # 走势图
    after_plot = after.copy()
    after_plot["avwap"] = avwap_s.values
    if len(after_plot) >= 5:
        sample = after_plot[["Close", "avwap"]].iloc[::max(1, len(after_plot) // 18)]
        all_p  = list(sample["Close"]) + list(sample["avwap"])
        pmin, pmax = min(all_p), max(all_p)
        W = 44
        console.print("  [dim]── 走势图（● 价格  ─ AVWAP）──[/dim]")
        for idx, row in sample.iterrows():
            pp = int((row["Close"] - pmin) / (pmax - pmin + 1e-9) * (W - 1))
            vp = int((row["avwap"] - pmin) / (pmax - pmin + 1e-9) * (W - 1))
            line = [" "] * W
            if 0 <= vp < W: line[vp] = "─"
            if 0 <= pp < W: line[pp] = "●" if pp >= vp else "○"
            c = "green" if row["Close"] >= row["avwap"] else "red"
            console.print(f"  {idx.strftime('%m/%d')} {''.join(line)}  [{c}]${row['Close']:.2f}[/{c}] [dim]▸${row['avwap']:.2f}[/dim]")
        console.print("  [dim]● 绿=高于AVWAP  ○ 红=低于AVWAP[/dim]")

    return {
        "label": anchor_label, "anchor_date": anchor_dt.strftime("%Y-%m-%d"),
        "anchor_price": anchor_price, "avwap": avwap,
        "current_price": cur, "diff_pct": dp, "days_ago": days_n,
        "discount": discount,
    }


def analyze_avwap(ticker: str, anchor_date: str = None, anchor_date2: str = None,
                  period_days: int = 365, engine: ScoreEngine = None):
    console.rule("[bold cyan]③ Anchored VWAP 锚定均量成本线[/bold cyan]")
    console.print("[dim]锚定财报/大涨日，计算量价加权成本。持续高于AVWAP=无抛压。双锚点揭示近/中期压力。[/dim]")

    console.print("\n  [dim]获取历史K线...[/dim]")
    hist = get_history(ticker, period_days=period_days)
    if hist is None or hist.empty:
        console.print("[red]  数据获取失败，跳过AVWAP分析[/red]\n")
        if engine:
            engine.missing.append("avwap")
        return None

    hist = hist.copy()
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    cur = float(hist["Close"].iloc[-1])

    # 锚点1
    if anchor_date:
        anchor_dt1, reason1 = pd.Timestamp(anchor_date), "手动指定"
    else:
        anchor_dt1, reason1 = _find_anchor(hist, prefer_recent_days=90)

    # 锚点2
    anchor_dt2, reason2 = None, None
    if anchor_date2:
        anchor_dt2, reason2 = pd.Timestamp(anchor_date2), "手动指定（第二锚）"
    else:
        s_start = anchor_dt1 - pd.Timedelta(days=300)
        s_end   = anchor_dt1 - pd.Timedelta(days=60)
        cand    = hist[(hist.index >= s_start) & (hist.index < s_end)].copy()
        cand["ret"] = cand["Close"].pct_change()
        big2 = cand[cand["ret"] > 0.07].sort_index()
        if not big2.empty:
            anchor_dt2 = big2.index[-1]
            reason2    = f"自动识别中期锚 (+{big2['ret'].iloc[-1]*100:.1f}%)"

    results = []
    r1 = _render_avwap_section(hist, anchor_dt1, "【近期锚 AVWAP-1】", reason1, cur)
    if r1:
        results.append(r1)

    if anchor_dt2 is not None:
        console.print()
        r2 = _render_avwap_section(hist, anchor_dt2, "【中期锚 AVWAP-2】", reason2, cur)
        if r2:
            results.append(r2)

    # 双锚对比
    if len(results) == 2:
        console.print()
        d1, d2 = results[0]["diff_pct"], results[1]["diff_pct"]
        if d1 > 0 and d2 > 0:
            console.print("  [green]▸ 近期、中期AVWAP均在价格下方，多时间框架无抛压确认[/green]")
        elif d1 > 0 and d2 <= 0:
            console.print("  [yellow]▸ 近期AVWAP健康，中期成本线仍在价格上方，长线持有者存在解套压力[/yellow]")
        elif d1 <= 0 and d2 > 0:
            console.print("  [yellow]▸ 近期AVWAP破位，中期结构仍健康，可能为短期回调[/yellow]")
        else:
            console.print("  [red]▸ 近期、中期AVWAP均在价格上方，筹码普遍套牢，抛压较重[/red]")

    if engine and results:
        engine.score_avwap(results[0]["diff_pct"], results[0]["days_ago"])

    console.print()
    return results[0] if results else None


# ═══════════════════════════════════════════════════════════════════════════════
# ④ IV + Max Pain
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_iv(ticker: str, engine: ScoreEngine = None):
    console.rule("[bold cyan]④ IV 隐含波动率结构 + Max Pain + 隐含Move + GEX[/bold cyan]")
    console.print("[dim]IV=大资金对冲成本。低且稳=有信心。Max Pain=期权到期引力位（参考）。[/dim]\n")

    exps = get_expirations(ticker)
    if not exps:
        console.print("[yellow]  无法获取期权到期日，跳过IV分析[/yellow]\n")
        if engine:
            engine.missing.append("iv")
        return None

    cur = get_current_price(ticker) or 0
    console.print(f"  当前价格：[bold yellow]${cur:.2f}[/bold yellow]  到期日：[dim]{len(exps)}个[/dim]\n")

    results = {}
    max_pain_results = {}
    iv_flags = []            # [v5.2] Yahoo IV 异常记录
    implied_move_info = None # [v5.2] 近月隐含Move
    gex_net, gex_pins, gex_n = 0.0, {}, 0   # [v5.2] GEX 累计

    targets = [("近月", exps[0])]
    if len(exps) > 1: targets.append(("次月", exps[1]))
    if len(exps) > 3: targets.append(("季度", exps[3]))

    for label, exp in targets:
        chain = get_option_chain(ticker, exp)
        if chain is None:
            console.print(f"  [dim]{label}（{exp}）期权链获取失败[/dim]")
            continue
        calls, puts = chain
        if calls.empty or puts.empty:
            continue

        calls = calls.copy(); puts = puts.copy()
        calls["dist"] = (calls["strike"] - cur).abs()
        puts["dist"]  = (puts["strike"]  - cur).abs()

        cp_r = calls["volume"].fillna(0).sum() / (puts["volume"].fillna(0).sum() or 1)

        # [v5.2] BSM 自算 IV（Yahoo impliedVolatility 降级为对照组）
        rep = chain_iv_report(calls, puts, cur, exp)
        call_iv = rep["call_iv"] if rep["call_iv"] is not None else 0.0
        put_iv  = rep["put_iv"]  if rep["put_iv"]  is not None else 0.0
        atm_iv  = rep["atm_iv"]  if rep["atm_iv"]  is not None else (call_iv + put_iv) / 2

        results[label] = {
            "exp_date": exp, "atm_iv": round(atm_iv, 1),
            "call_iv":  round(call_iv, 1), "put_iv": round(put_iv, 1),
            "cp_ratio": round(cp_r, 2),    "skew":   round(put_iv - call_iv, 2),
            "c_oi":     int(calls["openInterest"].fillna(0).sum()),
            "p_oi":     int(puts["openInterest"].fillna(0).sum()),
            "yahoo_atm": rep["yahoo_atm"], "iv_flag": rep["flag"],
        }
        if rep["flag"] == "yahoo_bad" and rep["yahoo_atm"] is not None:
            iv_flags.append((label, rep["yahoo_atm"], round(atm_iv, 1)))

        # [v5.2] 隐含Move（取近月）
        if label == targets[0][0]:
            implied_move_info = calc_implied_move(calls, puts, cur)

        # [v5.2] GEX 累计（近月+次月+季度，同档跨到期合并，近似口径）
        g = calc_gex(calls, puts, cur, exp)
        gex_net += g["net"]
        for k, v in g["pins"]:
            gex_pins[k] = gex_pins.get(k, 0.0) + v
        gex_n += 1

        # [v5.1] Max Pain + OI 可靠性（[v5.2] 传 spot 做 ±30% 带过滤 + argmin 修复）
        mp, mp_reliable = calc_max_pain(calls, puts, spot=cur)
        if mp is not None:
            total_oi = int(calls["openInterest"].fillna(0).sum() +
                           puts["openInterest"].fillna(0).sum())
            max_pain_results[label] = {
                "exp":         exp,
                "max_pain":    mp,
                "dist_pct":    round((cur - mp) / mp * 100, 1),
                "is_reliable": mp_reliable,
                "total_oi":    total_oi,
            }

    if not results:
        console.print("[yellow]  期权数据获取失败[/yellow]\n")
        if engine:
            engine.missing.append("iv")
        return None

    # IV表格
    tbl = Table(box=box.SIMPLE_HEAD, header_style="bold cyan")
    for col, kw in [
        ("期限",     {}), ("到期日", {"style":"dim"}),
        ("平值IV%",  {"justify":"right"}),
        ("Call IV%", {"justify":"right","style":"green"}),
        ("Put IV%",  {"justify":"right","style":"red"}),
        ("偏斜",     {"justify":"right"}),
        ("C/P量比",  {"justify":"right"}),
        ("C未平仓",  {"justify":"right","style":"green"}),
        ("P未平仓",  {"justify":"right","style":"red"}),
    ]:
        tbl.add_column(col, **kw)

    for label, r in results.items():
        iv  = r["atm_iv"]
        ic  = "green" if iv < 35 else ("yellow" if iv < 50 else "red")
        ig  = "✓" if iv < 35 else ("~" if iv < 50 else "⚠")
        sk  = f"+{r['skew']:.1f}(put溢)" if r["skew"] > 0 else f"{r['skew']:.1f}(call溢)"
        cc  = "green" if r["cp_ratio"] > 1 else "red"
        tbl.add_row(
            label, r["exp_date"],
            f"[{ic}]{ig} {iv:.1f}[/{ic}]",
            str(r["call_iv"]), str(r["put_iv"]), sk,
            f"[{cc}]{r['cp_ratio']:.2f}[/{cc}]",
            f"{r['c_oi']:,}", f"{r['p_oi']:,}",
        )
    console.print(tbl)

    # Max Pain 表格
    if max_pain_results:
        console.print()
        mpt = Table(box=box.SIMPLE_HEAD, header_style="bold blue",
                    title="最大痛点 Max Pain [dim]（参考，非核心信号）[/dim]")
        mpt.add_column("期限")
        mpt.add_column("到期日",    style="dim")
        mpt.add_column("总OI",      justify="right", style="dim")
        mpt.add_column("Max Pain",  justify="right", style="blue")
        mpt.add_column("vs 当前价", justify="right")
        mpt.add_column("可靠性",    justify="center")
        mpt.add_column("解读",      style="dim")
        for label, mp in max_pain_results.items():
            d    = mp["dist_pct"]
            dc   = "green" if d > 0 else "red"
            rel  = ("[green]✓ OI充足[/green]" if mp.get("is_reliable", True)
                    else f"[yellow]⚠ OI不足({mp.get('total_oi',0):,})[/yellow]")
            note = ("价格高于MP，到期前有回落引力" if d > 10 else
                    "价格低于MP，到期前有上引力"   if d < -10 else
                    "价格接近MP，影响较小")
            if not mp.get("is_reliable", True):
                note += "  [dim]（低OI，参考价值有限）[/dim]"
            mpt.add_row(label, mp["exp"],
                        f"{mp.get('total_oi', 0):,}",
                        f"${mp['max_pain']:.2f}",
                        f"[{dc}]{d:+.1f}%[/{dc}]", rel, note)
        console.print(mpt)

    # [v5.1] IV 变化趋势
    iv_trend = _estimate_iv_trend(ticker)
    if iv_trend:
        hv14  = iv_trend["hv_14"]
        hv7   = iv_trend["hv_7"]
        trend = iv_trend["hv_trend"]
        tc    = "red" if trend == "上升" else ("green" if trend == "下降" else "dim")
        arrow = "📈 加速上行" if trend == "上升" else ("📉 回落" if trend == "下降" else "→ 平稳")
        console.print(f"\n  [bold]IV/HV 趋势（近14天→近7天年化波动率）：[/bold]"
                      f"  [dim]{hv14:.1f}%[/dim]  →  [{tc}]{hv7:.1f}%  {arrow}[/{tc}]")
        console.print(f"  [{tc}]  {iv_trend['note']}[/{tc}]")

    # [v5.2] Yahoo IV 字段异常告警
    for lb, yv, ov in iv_flags:
        console.print(f"  [bold red]⚠ {lb} Yahoo IV字段异常（显示{yv:.1f}%，不可能值），"
                      f"表中IV已用BSM买卖价自算（{ov}%）替代[/bold red]")

    # [v5.2] 隐含Move + 财报窗口标注
    if implied_move_info:
        mv, ks, sd = implied_move_info["move_pct"], implied_move_info["strike"], implied_move_info["straddle"]
        earn = get_earnings_dates(ticker)
        es   = earn.get("date_start")
        tag  = ""
        if es is not None:
            try:
                exp_dt = datetime.strptime(targets[0][1], "%Y-%m-%d")
                if datetime.now() <= es <= exp_dt + timedelta(days=1):
                    tag = f" [bold red]含财报({es.strftime('%m-%d')})[/bold red]"
                elif es > exp_dt:
                    tag = f" [dim]不含财报(财报{es.strftime('%m-%d')}，在到期后{(es - exp_dt).days}天)[/dim]"
            except Exception:
                pass
        console.print(f"\n  [bold]隐含Move[/bold]（近月 K={ks} straddle ${sd:.2f}）："
                      f"[bold cyan]±{mv:.1f}%[/bold cyan]{tag}")

    # [v5.2] GEX 近似
    if gex_n > 0:
        gcol  = "green" if gex_net >= 0 else "red"
        gword = ("正GEX：做市商对冲抑制波动，价格倾向pin在高gamma档/Max Pain附近"
                 if gex_net >= 0 else
                 "负GEX：做市商对冲放大波动，破位日易加速")
        pins_top = sorted(gex_pins.items(), key=lambda kv: abs(kv[1]), reverse=True)[:2]
        pin_str  = "  ".join(f"K{k} ${abs(v)/1e6:.2f}M" for k, v in pins_top)
        console.print(f"  [bold]GEX近似[/bold]（{gex_n}个到期日，dealer常规符号）："
                      f"[{gcol}]{gex_net/1e6:+.2f}M$ / 1%波动[/{gcol}]  → {gword}")
        if pin_str:
            console.print(f"  [dim]最强gamma档（pin候选）：{pin_str}[/dim]")
        console.print("  [dim]（口径：BSM gamma×OI×S²×1%，符号假设+跨到期同档合并，量级/方向参考）[/dim]")

    first = list(results.values())[0]
    iv, cp = first["atm_iv"], first["cp_ratio"]
    console.print("\n  " + (
        f"[green]IV {iv:.1f}% 低位，大资金对冲需求低[/green]" if iv < 35 else
        f"[yellow]IV {iv:.1f}% 中等[/yellow]"                 if iv < 50 else
        f"[red]IV {iv:.1f}% 高位（>50%），市场预期大波动[/red]"
    ))
    console.print("  " + (
        f"[green]C/P {cp:.2f} 偏多[/green]" if cp > 1.2 else
        f"[dim]C/P {cp:.2f} 中性[/dim]"     if cp > 0.8 else
        f"[red]C/P {cp:.2f} 偏空（市场在抢买保险）[/red]"
    ))

    if engine:
        engine.score_iv(first["atm_iv"], first["cp_ratio"])

    # [v5.2] IV 快照自建分位（同日重跑去重覆盖）
    hv14 = iv_trend.get("hv_14") if iv_trend else None
    snap = log_iv_snapshot(ticker, targets[0][1], first["atm_iv"], hv14)
    if snap:
        if snap["pct_below"] is not None:
            console.print(f"  [dim]IV分位(自建)：当前近月IV {first['atm_iv']:.1f}%，"
                          f"高于历史快照的 {snap['pct_below']:.0f}%（n={snap['count']}，明细 iv_history/）[/dim]")
        else:
            console.print(f"  [dim]IV分位(自建)：已记录第 {snap['count']} 次快照，攒到5次输出百分位[/dim]")

    console.print()
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# ⑤ 做空比例 + 内部人交易（新增）
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_short_insider(ticker: str, engine: ScoreEngine = None):
    console.rule("[bold cyan]⑤ 做空比例 + 内部人交易[/bold cyan]")
    console.print("[dim]做空比例飙升=对手盘增加。内部人持续净卖出=最了解公司的人在减仓。[/dim]\n")

    # ── 做空比例 ──
    si = get_short_interest(ticker)
    short_pct = si.get("short_pct")
    short_chg = si.get("chg_pct")

    if short_pct is not None:
        sc = "green" if short_pct < 5 else ("yellow" if short_pct < 10 else "red")
        console.print(f"  做空比例：[{sc}]{short_pct:.2f}%[/{sc}]  "
                      f"[dim]（截至 {si.get('update_date','N/A')}）[/dim]")
        if short_chg is not None:
            cc = "green" if short_chg < 0 else "red"
            arrow = "↓空头撤退" if short_chg < -5 else ("↑空头增加" if short_chg > 5 else "→持平")
            console.print(f"  较上期变化：[{cc}]{short_chg:+.2f}%  {arrow}[/{cc}]")
        sr = si.get("short_ratio")
        if sr:
            console.print(f"  空头回补天数（Short Ratio）：[dim]{sr:.1f} 天[/dim]")
    else:
        console.print("  [dim]做空比例数据不可用[/dim]")

    # ── 内部人交易（v5.1：过滤非市场操作）──
    console.print()
    insider_df  = get_insider_trades(ticker)
    insider_net = None   # 美元净买卖（正=净买，负=净卖）

    if insider_df is not None and not insider_df.empty:
        # [v5.1] 过滤非市场操作
        filtered_df, has_txn_col = _filter_insider_market_trades(insider_df)
        if not has_txn_col:
            console.print("  [dim]⚠ 未找到 Transaction 列，含期权行权等非市场操作，数据仅供参考[/dim]")
            filtered_df = insider_df.copy()

        # 近30天
        cutoff  = pd.Timestamp.now() - pd.Timedelta(days=30)
        date_col = next((c for c in ["Start Date", "Date"] if c in filtered_df.columns), None)
        try:
            if date_col:
                filtered_df[date_col] = pd.to_datetime(filtered_df[date_col], errors="coerce")
                recent = filtered_df[filtered_df[date_col] >= cutoff].copy()
            else:
                recent = filtered_df.head(10).copy()
        except Exception:
            recent = filtered_df.head(10).copy()

        # [v5.1] 净买卖：优先 Value 列（美元），用 Transaction 列判断方向
        if "Value" in filtered_df.columns and not recent.empty:
            vals    = pd.to_numeric(recent["Value"], errors="coerce").fillna(0)
            txn_col = next((c for c in ["Transaction", "Text"] if c in recent.columns), None)
            if txn_col and (vals >= 0).all():
                # Value 全正，用 Transaction 字符串判断正负
                signs = recent[txn_col].apply(
                    lambda t: -1 if isinstance(t, str) and
                    any(k in t.lower() for k in ["sale", "sell", "s"]) else 1
                )
                vals = vals * signs
            insider_net = float(vals.sum())

        # 展示
        show_cols = [c for c in ["Insider", "Title", date_col or "Date",
                                  "Transaction", "Shares", "Value"]
                     if c and c in filtered_df.columns]
        if not recent.empty:
            nc  = "green" if (insider_net or 0) >= 0 else "red"
            lbl = "净买入" if (insider_net or 0) >= 0 else "净卖出"
            console.print(f"  内部人近30天（公开市场）：[{nc}]{lbl} "
                          f"${abs(insider_net or 0):,.0f}[/{nc}]  "
                          f"[dim]{len(recent)} 笔[/dim]")

            if show_cols:
                tbl = Table(box=box.SIMPLE_HEAD, header_style="bold cyan")
                for c in show_cols:
                    tbl.add_column(c, style="dim" if c == "Title" else "")
                for _, row in recent.head(8).iterrows():
                    txn_v = str(row.get("Transaction", row.get("Text", "")))
                    is_sale = any(k in txn_v.lower() for k in ["sale","sell"])
                    cells = []
                    for c in show_cols:
                        v = str(row.get(c, "N/A"))[:30]
                        if c in ["Transaction","Text"]:
                            v = f"[red]{v}[/red]" if is_sale else f"[green]{v}[/green]"
                        cells.append(v)
                    tbl.add_row(*cells)
                console.print(tbl)

            # 过滤数量提示
            if has_txn_col and date_col:
                raw_recent_count = len(insider_df[
                    pd.to_datetime(insider_df.get(date_col, pd.Series(dtype="object")),
                                   errors="coerce") >= cutoff
                ])
                skipped = raw_recent_count - len(recent)
                if skipped > 0:
                    console.print(f"  [dim]（已过滤 {skipped} 笔非市场操作：期权行权/赠予等）[/dim]")
        else:
            console.print("  [dim]近30天无公开市场买卖记录[/dim]")
    else:
        console.print("  [dim]内部人交易数据不可用[/dim]")
        console.print(f"  [cyan]https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=4[/cyan]")

    if engine:
        engine.score_short_insider(short_pct, short_chg, insider_net)

    console.print()


# ═══════════════════════════════════════════════════════════════════════════════
# ⑥ 相对强弱（RS）vs 标普500 / 同类股（新增）
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_relative_strength(ticker: str, compare_list: list, period_days: int = 30):
    if not compare_list:
        return
    console.rule("[bold cyan]⑥ 相对强弱（Relative Strength）[/bold cyan]")
    console.print(f"[dim]vs 标的：{', '.join(compare_list)}  周期：{period_days}天[/dim]\n")

    tbl = Table(box=box.SIMPLE_HEAD, header_style="bold cyan")
    tbl.add_column("对比标的", style="dim")
    tbl.add_column(f"{ticker}涨跌", justify="right")
    tbl.add_column("对比涨跌",      justify="right")
    tbl.add_column("超额收益",      justify="right")
    tbl.add_column("RS趋势",        justify="center")
    tbl.add_column("结论",          style="dim")

    for comp in compare_list:
        rs = get_relative_strength(ticker, comp, period_days)
        if not rs:
            tbl.add_row(comp, "N/A", "N/A", "N/A", "N/A", "数据不可用")
            continue
        tc   = "green" if rs["ticker_ret"] >= 0 else "red"
        cc   = "green" if rs["compare_ret"] >= 0 else "red"
        rc   = "green" if rs["rs"] >= 0 else "red"
        tr   = "↑" if rs["trend"] == "上升" else "↓"
        tc2  = "green" if rs["trend"] == "上升" else "red"
        note = ("跑赢，RS上升——强势" if rs["rs"] > 0 and rs["trend"] == "上升" else
                "跑赢，RS下降——动能减弱" if rs["rs"] > 0 and rs["trend"] == "下降" else
                "跑输，RS上升——在追赶" if rs["rs"] < 0 and rs["trend"] == "上升" else
                "跑输，RS下降——持续弱势")
        tbl.add_row(
            comp,
            f"[{tc}]{rs['ticker_ret']:+.1f}%[/{tc}]",
            f"[{cc}]{rs['compare_ret']:+.1f}%[/{cc}]",
            f"[{rc}]{rs['rs']:+.1f}%[/{rc}]",
            f"[{tc2}]{tr} {rs['trend']}[/{tc2}]",
            note,
        )
    console.print(tbl)
    console.print()


# ═══════════════════════════════════════════════════════════════════════════════
# ⑦ 财报日历联动（新增）
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_earnings(ticker: str):
    console.rule("[bold cyan]⑦ 财报日历[/bold cyan]")

    # [v5.1] 使用支持区间的新函数
    info     = get_earnings_dates(ticker)
    dt_start = info.get("date_start")
    dt_end   = info.get("date_end")
    is_range = info.get("is_range", False)

    if dt_start is None:
        console.print("  [dim]财报日期数据不可用（可手动查询：finance.yahoo.com）[/dim]\n")
        return None

    now     = datetime.now()
    days_to = (dt_start.date() - now.date()).days   # [v5.2] 按日历日：财报日当天凌晨跑显示0天而非"-1天"

    if days_to < 0:
        ds = dt_start.strftime("%Y-%m-%d")
        console.print(f"  上次财报：[dim]{ds}[/dim]（{-days_to} 天前）\n")
        render_pead(ticker)   # [v5.2]
        console.print()
        return None

    # [v5.1] 区间 vs 单点显示
    if is_range:
        date_display = f"{dt_start.strftime('%Y-%m-%d')} ~ {dt_end.strftime('%Y-%m-%d')}"
        range_note   = "  [dim]⚠ 市场预估区间，具体日期待官宣，误差约±3天[/dim]"
    else:
        date_display = dt_start.strftime("%Y-%m-%d")
        range_note   = "  [dim]（市场预估单点，仍有误差可能）[/dim]"

    if days_to <= 7:
        color = "bold red"
        note  = "⚠ 极近财报！IV通常急升，谨慎开新仓"
    elif days_to <= 21:
        color = "yellow"
        note  = "△ 进入财报窗口期，IV开始上升，当前IV若仍低位可考虑布局"
    elif days_to <= 45:
        color = "green"
        note  = "✓ 距财报尚早，当前期权定价通常不含财报溢价"
    else:
        color = "dim"
        note  = "财报较远，短期IV受财报影响小"

    console.print(f"  预估财报：[{color}]{date_display}[/{color}]  距今约 [{color}]{days_to} 天[/{color}]")
    console.print(range_note)
    console.print(f"  [{color}]{note}[/{color}]")
    render_pead(ticker)   # [v5.2]
    console.print()
    return {"earnings_date": dt_start.strftime("%Y-%m-%d"), "days_to": days_to, "is_range": is_range}


# ═══════════════════════════════════════════════════════════════════════════════
# ⑧ 综合研判 + 离场量化预警
# ═══════════════════════════════════════════════════════════════════════════════

def print_summary(ticker, score_engine: ScoreEngine,
                  vp_res, avwap_res, iv_res, earnings_res):
    console.rule("[bold magenta]⑧ 综合研判[/bold magenta]")

    # 评分渲染
    total_score = score_engine.render()

    # ── 离场条件量化预警 ──────────────────────────────────────────
    console.print("[bold]离场触发条件（三者叠加确认才算真信号）：[/bold]")

    warns = []

    # 条件①：AVWAP跌破
    if avwap_res:
        cur_p = avwap_res.get("current_price", 0)
        avwap = avwap_res.get("avwap", 0)
        if cur_p < avwap:
            console.print(f"  [bold red]🚨 ① AVWAP已跌破！收盘 ${cur_p:.2f} < AVWAP ${avwap:.2f}[/bold red]")
            warns.append("AVWAP跌破")
        else:
            gap  = cur_p - avwap
            gpct = gap / avwap * 100
            console.print(f"  [green]✓ ① AVWAP未跌破（高出 +${gap:.2f} / +{gpct:.1f}%）[/green]")

    # 条件②：VP密集区失守
    if vp_res and vp_res.get("hvn_lo"):
        cur_p  = vp_res.get("current_price", 0)
        hvn_lo = vp_res.get("hvn_lo", 0)
        if cur_p < hvn_lo:
            console.print(f"  [bold red]🚨 ② VP密集区下沿已失守！当前 ${cur_p:.2f} < 密集区 ${hvn_lo:.2f}[/bold red]")
            warns.append("VP失守")
        else:
            console.print(f"  [green]✓ ② VP密集区未失守（当前 ${cur_p:.2f} > 密集区下沿 ${hvn_lo:.2f}）[/green]")

    # 条件③：IV + C/P
    if iv_res:
        first = list(iv_res.values())[0]
        iv, cp = first["atm_iv"], first["cp_ratio"]
        if iv > 50 and cp < 0.8:
            console.print(f"  [bold red]🚨 ③ IV={iv:.1f}%>50 且 C/P={cp:.2f}<0.8，期权市场预警[/bold red]")
            warns.append("IV+CP预警")
        else:
            console.print(f"  [green]✓ ③ IV={iv:.1f}% C/P={cp:.2f}，期权结构正常[/green]")

    console.print()
    if len(warns) >= 2:
        console.print(Panel(
            f"[bold red]⛔ 离场预警触发！已满足 {len(warns)}/3 个条件：{' + '.join(warns)}\n"
            f"建议立即复查仓位，设置止损。[/bold red]",
            border_style="red"
        ))
    elif len(warns) == 1:
        console.print(Panel(
            f"[yellow]⚠ 早期预警（{warns[0]}）：仅满足1个条件，持续观察，可设置提醒阈值。[/yellow]",
            border_style="yellow"
        ))
    else:
        console.print(Panel(
            "[green]✓ 三个离场条件均未触发，筹码结构完整。[/green]",
            border_style="green"
        ))

    # 财报期权策略建议
    if earnings_res and iv_res:
        first  = list(iv_res.values())[0]
        iv     = first["atm_iv"]
        days_e = earnings_res.get("days_to", 99)
        console.print()
        console.print("[bold]期权策略参考（结合财报+IV）：[/bold]")
        if days_e <= 21 and iv < 35:
            console.print("  [cyan]IV低位 + 财报临近 → 可考虑买入跨式/宽跨式（Long Straddle）博弈财报波动[/cyan]")
        elif days_e <= 45 and iv < 35:
            console.print("  [cyan]IV低位 + 财报尚早 → 可考虑卖出价外PUT收租（Sell OTM Put），赚时间价值[/cyan]")
        elif days_e <= 7 and iv >= 35:
            console.print("  [yellow]IV已升 + 财报极近 → 期权溢价高，买方成本贵；偏向卖方策略（Iron Condor）[/yellow]")
        else:
            console.print("  [dim]暂无明确期权策略信号，继续观察IV走势[/dim]")

    console.print("\n[dim]本脚本仅作筹码结构分析参考，不构成投资建议。[/dim]\n")


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global USE_CACHE

    p = argparse.ArgumentParser(description="美股筹码四维分析 v5.2")
    p.add_argument("--ticker",    default="HPE",   help="股票代码（默认HPE）")
    p.add_argument("--anchor",    default=None,    help="AVWAP主锚点 YYYY-MM-DD")
    p.add_argument("--anchor2",   default=None,    help="AVWAP第二锚点 YYYY-MM-DD")
    p.add_argument("--period",    default=365, type=int, help="历史天数，默认365")
    p.add_argument("--period2",   default=60,  type=int, help="VP近期周期，默认60")
    p.add_argument("--bins",      default=20,  type=int, help="VP分箱数，默认20")
    p.add_argument("--compare",  default="SPY,SOXX",  help="RS对比标的，逗号分隔，默认SPY,SOXX（[v5.2]加SOXX科技情绪）。硬件股建议: DELL,SMCI,XLK; 半导体建议: AMD,INTC")
    p.add_argument("--rs-days",   default=30,  type=int, help="相对强弱周期天数，默认30")
    p.add_argument("--no-cache",  action="store_true",   help="禁用本地缓存")
    args = p.parse_args()

    USE_CACHE  = not args.no_cache
    ticker     = args.ticker.upper()
    compare_list = [c.strip().upper() for c in args.compare.split(",") if c.strip()]

    cache_status = "[dim]缓存：关闭[/dim]" if not USE_CACHE else f"[dim]缓存：开启（TTL {CACHE_TTL//60}min）[/dim]"

    console.print()
    console.print(Panel(
        f"[bold white]美股筹码四维分析仪 v5.2[/bold white]\n"
        f"标的：[bold cyan]{ticker}[/bold cyan]  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"[dim]VP周期：{args.period}天（全局）/ {args.period2}天（近期）[/dim]\n"
        f"[dim]AVWAP：{'手动锚' if args.anchor else '自动锚（优先近90天大涨日）'}  "
        f"RS对比：{', '.join(compare_list)}（{args.rs_days}天）[/dim]\n"
        f"{cache_status}  [dim]数据源：yfinance→curl_cffi→akshare→CDP[/dim]",
        border_style="cyan"
    ))

    engine = ScoreEngine()

    if USE_CACHE:
        console.print(f"[dim]缓存目录：{CACHE_DIR}[/dim]")
    console.print("[dim]预拉取主数据...[/dim]")
    get_history(ticker, period_days=args.period)   # 预热缓存

    analyze_13f(ticker, engine)
    vp_res       = analyze_vp(ticker, period_days=args.period, period2=args.period2,
                               bins=args.bins, engine=engine)
    avwap_res    = analyze_avwap(ticker, anchor_date=args.anchor, anchor_date2=args.anchor2,
                                  period_days=args.period, engine=engine)
    iv_res       = analyze_iv(ticker, engine=engine)
    analyze_short_insider(ticker, engine=engine)
    analyze_relative_strength(ticker, compare_list, period_days=args.rs_days)
    print_execution_panel(ticker)   # [v5.2] ATR+均线+挂单参考
    earnings_res = analyze_earnings(ticker)

    print_summary(ticker, engine, vp_res, avwap_res, iv_res, earnings_res)


if __name__ == "__main__":
    main()
