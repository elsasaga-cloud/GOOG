# -*- coding: utf-8 -*-
"""
HPE 100股卫星仓 · 盘前盘后流动性脉冲套利 v5.0（价格源重构版）
================================================================
v5.0关键变化（基于v4.4）:
  1. 价格源重排: yfinance fast_info → yfinance history收盘 → 手动输入(10秒超时) → Excel回退
     (原第1级Yahoo v7/finance/quote已401失效, 2026-08实测, 弃用)
  2. 持仓/财报日历改读positions.py单一事实源(与options_engine共用, 不再各存一份)
  3. Excel加载复用quant_core(同一个3000天筹码底座)
策略逻辑与v4.4完全一致: 振幅统计三档报价 / 筹码区位置 / 场景识别 / 双向评分
注意: 卫星仓100股与备兑CC共用同一批正股——卖出卫星仓后在场CALL75将变为裸卖(naked call),
     执行卫星仓卖出前必须检查在场期权(脚本已内置该硬风控)。
"""
import os, sys, json, threading, warnings
from pathlib import Path
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import positions as POS
from quant_core import find_excel, load_stock_data

OUTPUT_DIR = Path(os.environ.get("HPE_OUT_DIR", r"C:\Users\simon\Desktop\Stock Data Retrivel"))
REPORT_TXT = OUTPUT_DIR / "HPE_盘前盘后套利策略.txt"
REPORT_JSON = OUTPUT_DIR / "HPE_盘前盘后套利策略.json"
LOG_PATH = OUTPUT_DIR / "HPE_arbitrage_log.json"

SATELLITE_SHARES = 100
MAX_ROUNDS_PER_MONTH = 3
K_SELL, K_BUY = 1.0, 0.7
VOL_EXCLUDE_PCT, MIN_SAMPLE = 0.95, 200
ABN = 0.20
MANUAL_TIMEOUT = 10
NY = ZoneInfo("America/New_York")


def clamp(x, a, b):
    return max(a, min(b, x))


def fp(x):
    return "N/A" if x is None or (isinstance(x, float) and np.isnan(x)) else f"${float(x):.2f}"


# ══════════ 价格获取(重排后三级+回退) ══════════
def _try_yf_fast():
    try:
        import yfinance as yf
        v = yf.Ticker(POS.TICKER).fast_info.get("last_price")
        if v and float(v) > 0:
            return float(v), "yfinance fast_info(准实时)", True
    except Exception:
        pass
    return None, None, False


def _try_yf_history():
    try:
        import yfinance as yf
        h = yf.Ticker(POS.TICKER).history(period="2d")
        if h is not None and len(h):
            return float(h["Close"].iloc[-1]), f"yfinance日K收盘({h.index[-1].date()})", False
    except Exception:
        pass
    return None, None, False


def _try_manual():
    res = {"v": None}

    def ask():
        try:
            raw = input(f"\n  行情获取失败, 手动输入价格({MANUAL_TIMEOUT}秒超时, 回车跳过): ").strip()
            if raw:
                v = float(raw.replace("$", "").replace(",", ""))
                if v > 0:
                    res["v"] = v
        except Exception:
            pass
    t = threading.Thread(target=ask, daemon=True)
    t.start(); t.join(MANUAL_TIMEOUT)
    if res["v"]:
        return res["v"], "手动输入", True
    return None, None, False


def get_price(excel_close, excel_date):
    for fn in (_try_yf_fast, _try_yf_history, _try_manual):
        v, src, fresh = fn()
        if v:
            print(f"  ✓ {src}: {fp(v)}")
            return {"price": v, "source": src, "is_fresh": fresh,
                    "warning": None if fresh else "非近实时价格, 下单前以券商App确认"}
    print(f"  ⚠️ 全部失败, 回退Excel收盘 {fp(excel_close)}({excel_date})")
    return {"price": excel_close, "source": f"Excel收盘({excel_date})", "is_fresh": False,
            "warning": "实时行情全部失败, 回退Excel收盘价"}


# ══════════ 振幅统计(与v4.4一致) ══════════
def filter_earnings(df):
    ds = df["日期"]
    blocked = set()
    for ed in POS.EARNINGS_DATES + ["2025-03-06", "2025-06-03", "2025-09-03", "2025-12-04"]:
        d = pd.to_datetime(ed)
        idx = ds[ds >= d].index
        if len(idx) == 0 or abs((ds.iloc[idx[0]] - d).days) > 30:
            continue
        ai = idx[0]
        blocked.update(range(max(0, ai - 7), min(len(df) - 1, ai + 7) + 1))
    return df.drop(index=list(blocked)).reset_index(drop=True), len(blocked)


def compute_amp(df):
    if "振幅" not in df.columns:
        df = df.copy()
        df["振幅"] = (df["最高"] - df["最低"]) / df["收盘"].shift(1) * 100
    vc = df["成交量"].quantile(VOL_EXCLUDE_PCT)
    dc = df[df["成交量"] <= vc].copy().reset_index(drop=True)
    dc["r"] = dc["收盘"].pct_change()
    dc = dc[dc["r"].abs() < ABN].reset_index(drop=True)
    amp = dc["振幅"].dropna()
    if len(amp) < 50:
        return {"错误": f"样本不足({len(amp)})"}
    dc["pc"] = dc["收盘"].shift(1)
    dc["hi%"] = (dc["最高"] - dc["pc"]) / dc["pc"] * 100
    dc["lo%"] = (dc["最低"] - dc["pc"]) / dc["pc"] * 100
    dn = (-dc["lo%"]).where(lambda x: x > 0).dropna()
    p75 = float(amp.quantile(0.75))
    pidx = dc.index[dc["hi%"] >= p75]
    rv = sum(1 for i in pidx if i + 3 < len(dc) and dc.loc[i, "pc"] > 0
             and dc.loc[i + 1:i + 3, "最低"].min() <= dc.loc[i, "pc"] * 0.99)
    r = {"amp_median": round(float(amp.median()), 3), "amp_p75": round(p75, 3),
         "amp_p90": round(float(amp.quantile(0.90)), 3),
         "amp_p50_return": round(float(dn.median()) if len(dn) >= 20 else float(amp.median()) * 0.6, 3),
         "sample_size": len(amp),
         "revert_prob_3d": round(rv / len(pidx) * 100, 1) if len(pidx) else 0}
    if len(amp) < MIN_SAMPLE:
        r["warning"] = f"样本{len(amp)}<建议{MIN_SAMPLE}"
    return r


# ══════════ 技术/筹码上下文 ══════════
def build_ctx(df, cp):
    d = df.copy()
    for n in [5, 10, 20, 60, 120]:
        d[f"MA{n}"] = d["收盘"].rolling(n).mean()
    delta = d["收盘"].diff()
    rs = delta.clip(lower=0).rolling(14).mean() / (-delta.clip(upper=0)).rolling(14).mean().replace(0, np.nan)
    d["RSI"] = 100 - 100 / (1 + rs)
    last = d.iloc[-1]
    gv = lambda c: float(last[c]) if pd.notna(last.get(c)) else None
    t = {f"ma{n}": gv(f"MA{n}") for n in [5, 10, 20, 60, 120]}
    t["rsi14"] = gv("RSI")
    t["h20"] = float(d["最高"].tail(20).max())
    t["l20"] = float(d["最低"].tail(20).min())
    t["range_pos"] = clamp((cp - t["l20"]) / (t["h20"] - t["l20"]) * 100, 0, 100) if t["h20"] > t["l20"] else None
    t["vs_ma20"] = (cp - t["ma20"]) / t["ma20"] * 100 if t.get("ma20") else None
    t["vs_ma60"] = (cp - t["ma60"]) / t["ma60"] * 100 if t.get("ma60") else None
    t["trend"] = ("偏强" if t.get("ma20") and t.get("ma60") and cp > t["ma20"] > t["ma60"]
                  else "偏弱" if t.get("ma20") and t.get("ma60") and cp < t["ma20"] < t["ma60"] else "震荡/过渡")
    last_row = df.iloc[-1]
    sfv = lambda v: None if pd.isna(v) else float(v)
    c = {"profit_ratio": sfv(last_row.get("获利比例")),
         "lo70": sfv(last_row.get("70%成本-低")), "hi70": sfv(last_row.get("70%成本-高"))}
    if c["lo70"] and c["hi70"] and c["hi70"] > c["lo70"]:
        c["chip_pos"] = clamp((cp - c["lo70"]) / (c["hi70"] - c["lo70"]) * 100, 0, 100)
        c["in_band"] = c["lo70"] <= cp <= c["hi70"]
        c["mid70"] = (c["lo70"] + c["hi70"]) / 2
    else:
        c["chip_pos"], c["in_band"], c["mid70"] = None, None, None
    return t, c


# ══════════ 评分+三档(与v4.4逻辑一致, 精简实现) ══════════
def score_side(side, chip, tech):
    s, notes = 50, []
    pos = chip.get("chip_pos")
    if pos is not None:
        if side == "SELL":
            s += 20 if pos >= 90 else 12 if pos >= 75 else (-15 if pos <= 35 else 0)
        else:
            s += 20 if pos <= 20 else 12 if pos <= 35 else (-18 if pos >= 85 else -10 if pos >= 70 else 0)
        notes.append(f"筹码区第{pos:.0f}%分位")
    v20 = tech.get("vs_ma20")
    if v20 is not None:
        s += (8 if v20 >= 0 else -8) if side == "SELL" else (10 if v20 <= 0 else -6)
    rp = tech.get("range_pos")
    if rp is not None:
        if side == "SELL":
            s += 10 if rp >= 80 else (-10 if rp <= 35 else 0)
        else:
            s += 12 if rp <= 20 else 6 if rp <= 35 else (-12 if rp >= 75 else 0)
    rsi = tech.get("rsi14")
    if rsi is not None:
        if side == "SELL":
            s += 8 if rsi >= 62 else (-6 if rsi <= 42 else 0)
        else:
            s += 8 if rsi <= 40 else (-8 if rsi >= 60 else 0)
    return clamp(int(round(s)), 0, 100), notes


def three_prices(side, cp, amp, chip, tech):
    p75, p90, med, a_dn = amp["amp_p75"], amp["amp_p90"], amp["amp_median"], amp["amp_p50_return"]
    if side == "SELL":
        base = cp * (1 + p75 * K_SELL / 100)
        anchors = [v for v in [tech.get("h20"), chip.get("hi70")] if v]
        ra = max(anchors) if anchors else None
        main = max(base, ra * 0.998) if ra else base
        main = min(main, cp * (1 + min(p90 * 0.92, p75 * 1.22) / 100))
        cons = max(cp * (1 + max(med, p75 * 0.88) / 100), (ra * 0.995) if ra else 0)
        aggr = min(cp * (1 + min(p75 * 1.2, p90 * 0.85) / 100), cp * (1 + p90 * 0.95 / 100))
        if ra:
            aggr = max(aggr, ra * 1.004)
        vals = sorted({round(cons, 2), round(main, 2), round(aggr, 2)})
        while len(vals) < 3:
            vals.append(round(vals[-1] + 0.01, 2))
        return vals[0], vals[1], vals[2]
    else:
        stat = cp * (1 - a_dn * K_BUY / 100)
        sup = [v for v in [tech.get("ma10"), tech.get("ma20"), tech.get("ma60"),
                            chip.get("mid70"), tech.get("l20")] if v and v < cp]
        sp = max(sup) if sup else stat
        main = (stat + sp) / 2
        cons = max(stat, sp, main)
        aggr = min(cp * (1 - min(a_dn * 1.2, med * 0.85) / 100), stat, sp, main)
        vals = sorted({round(cons, 2), round(main, 2), round(aggr, 2)}, reverse=True)
        while len(vals) < 3:
            vals.append(round(vals[-1] - 0.01, 2))
        return vals[0], vals[1], vals[2]


def main():
    print(f"HPE卫星仓报价 v5.0 | {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 60)

    # 月度轮次
    log = {}
    if LOG_PATH.exists():
        try:
            log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    cm = datetime.now().strftime("%Y-%m")
    rounds = log.get(cm, {}).get("rounds_completed", 0)
    print(f"[1/6] 月度轮次: {rounds}/{MAX_ROUNDS_PER_MONTH}")

    # 财报窗口
    today = date.today()
    in_earn = any(abs((today - date.fromisoformat(e)).days) <= 7 + 3 for e in POS.EARNINGS_DATES)
    print(f"[2/6] 财报窗口: {'⚠️ 在窗口内' if in_earn else '不在'}")

    # ⚠️ 卫星仓与备兑冲突硬风控
    naked_risk = POS.cc_covered_used() > 0 and POS.TOTAL_SHARES - SATELLITE_SHARES < POS.cc_covered_used()
    if naked_risk:
        print(f"[!] 硬风控: 卖出{SATELLITE_SHARES}股后剩余{POS.TOTAL_SHARES-SATELLITE_SHARES}股 < "
              f"在场CALL占用{POS.cc_covered_used()}股 → 卖出侧将导致裸CALL, 强制降级为仅参考")

    print("[3/6] 加载Excel...")
    fpath = find_excel()
    df = load_stock_data(fpath) if fpath else None
    if df is None:
        return
    excel_close = float(df["收盘"].iloc[-1])
    excel_date = str(df["日期"].iloc[-1].date())

    print("[4/6] 振幅统计...")
    df_f, removed = filter_earnings(df)
    amp = compute_amp(df_f)
    if "错误" in amp:
        print(f"❌ {amp['错误']}")
        return
    print(f"  A_up={amp['amp_p75']:.2f}% A_dn={amp['amp_p50_return']:.2f}% 样本{amp['sample_size']}")

    print("[5/6] 获取价格...")
    pi = get_price(excel_close, excel_date)
    cp = pi["price"]

    print("[6/6] 生成双向方案...")
    tech, chip = build_ctx(df, cp)
    ss, _ = score_side("SELL", chip, tech)
    bs, _ = score_side("BUY", chip, tech)
    sc, sm, sa = three_prices("SELL", cp, amp, chip, tech)
    bc, bm, ba = three_prices("BUY", cp, amp, chip, tech)

    sell_ok = not in_earn and not naked_risk and chip.get("in_band") is not False
    buy_ok = not in_earn and chip.get("in_band") is not False and (chip.get("profit_ratio") or 0) <= 95

    L = ["=" * 64, "HPE 100股卫星仓 · 套利策略报告 v5.0",
         f"生成: {datetime.now():%Y-%m-%d %H:%M:%S}", "=" * 64]
    L.append(f"\n持仓: {POS.TOTAL_SHARES}股@${POS.AVG_COST}(负成本)")
    if naked_risk:
        L.append("⚠️ 硬风控: 卖出卫星仓将令在场CALL75变裸卖 → 卖出侧仅供参考, 执行前须先平CALL腿")
    L.append(f"价格: {fp(cp)} | {pi['source']}" + (f" | ⚠️{pi['warning']}" if pi["warning"] else ""))
    L.append(f"\n卖出三档: {fp(sc)} / {fp(sm)} / {fp(sa)}  适配度{ss}分 {'✓可执行' if sell_ok else '✗仅参考'}")
    L.append(f"买入三档: {fp(bc)} / {fp(bm)} / {fp(ba)}  适配度{bs}分 {'✓可执行' if buy_ok else '✗仅参考'}")
    L.append(f"\n技术: 趋势{tech['trend']} MA20{fp(tech.get('ma20'))} MA60{fp(tech.get('ma60'))} "
             f"RSI{tech.get('rsi14') and round(tech['rsi14'],1)} 20日区间第{tech.get('range_pos') and round(tech['range_pos'])}%")
    if chip.get("chip_pos") is not None:
        L.append(f"筹码: 70%区[{fp(chip['lo70'])},{fp(chip['hi70'])}] 现价第{chip['chip_pos']:.0f}%分位 "
                 f"获利盘{chip.get('profit_ratio') or 0:.0f}%")
    L.append(f"振幅: 中位{amp['amp_median']:.2f}% P75={amp['amp_p75']:.2f}% 3日回归率{amp['revert_prob_3d']:.0f}%")
    L.append(f"月度: {cm}已用{rounds}/{MAX_ROUNDS_PER_MONTH}轮 | 财报窗口: {'是' if in_earn else '否'}")
    d = ss - bs
    bias = ("偏高抛" if d >= 15 and sell_ok else "偏低吸" if d <= -15 and buy_ok
            else "更偏等待" if max(ss, bs) < 60 else "中性(被动挂单)")
    L.append(f"\n今日偏向: {bias} (卖{ss}/买{bs})")
    L.append("\n下单模板: 限价单/当日有效/开启Extended Hours/不成交明日重算")
    L.append("=" * 64)
    report = "\n".join(L)

    REPORT_TXT.write_text(report, encoding="utf-8")
    REPORT_JSON.write_text(json.dumps({
        "run_time": datetime.now().isoformat(), "price_info": pi,
        "sell": {"ladder": [sc, sm, sa], "score": ss, "tradable": sell_ok},
        "buy": {"ladder": [bc, bm, ba], "score": bs, "tradable": buy_ok},
        "naked_call_risk": naked_risk, "in_earnings": in_earn,
        "amp": amp, "tech": {k: v for k, v in tech.items()},
        "chip": chip, "bias": bias, "monthly_rounds": rounds,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n" + report)
    print(f"\n✓ {REPORT_TXT}")


if __name__ == "__main__":
    main()
