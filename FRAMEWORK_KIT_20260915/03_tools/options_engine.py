# -*- coding: utf-8 -*-
"""
HPE 智能期权策略引擎 v3.0（CC/CSP自动切换 · yfinance数据源 · 持仓感知版）
================================================================
相比v2.0的关键变化：
  1. 【数据源】Playwright无头浏览器 → yfinance期权链API（bid/ask/OI/IV全字段，
     零浏览器依赖，2026-08实测HPE期权链完整可用；v7/quote接口已401失效，一并弃用）
  2. 【持仓感知】读取positions.py单一事实源：
     - 自动计算可新开CC张数（扣除在场CALL75占用的备兑）
     - 显示在场PUT44/CALL75状态与距财报/到期天数
     - 可开=0时CC自动降级为"仅供参考"，并提示需先buy-to-close
  3. 【策略选择】沿用v2.0智能切换逻辑：看涨→CSP / 看跌→CC / 无股备兑→强制CSP
  4. 【联动】沿用：方向信号调OTM区间 + GARCH vs 期权链IV比价
用法: python options_engine.py            # 自动选择CC/CSP
      python options_engine.py --mode csp # 强制CSP
      python options_engine.py --mode cc  # 强制CC
"""
import os, sys, json, warnings
from pathlib import Path
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import positions as POS
from quant_core import run_quant_analysis

OUTPUT_DIR = Path(os.environ.get("HPE_OUT_DIR", r"C:\Users\simon\Desktop\Stock Data Retrivel"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_TXT = OUTPUT_DIR / "HPE_量化分析及期权策略.txt"
REPORT_JSON = OUTPUT_DIR / "HPE_量化分析及期权策略.json"

# 过滤/打分参数（与v2.0一致）
MAX_ITM_PCT, MAX_OTM_PCT = 1.0, 8.0
PREFERRED_OTM_MIN, PREFERRED_OTM_MAX, TARGET_OTM_PCT = 1.0, 5.0, 3.5
MIN_MID, MIN_OI, MIN_VOL, MAX_SPREAD_PCT_HARD = 0.05, 20, 10, 40
STRONG_UP, MILD_UP, STRONG_DOWN, MILD_DOWN = 1.5, 0.6, -1.5, -0.6


# ══════════ 数据层：yfinance ══════════
def fetch_quote_and_chain(target_expiry_min: date) -> Tuple[Dict, str, List[Dict], List[Dict]]:
    """返回 (quote, expiry_str, calls, puts)。expiry选择>=目标周五的最近到期日。"""
    import yfinance as yf
    t = yf.Ticker(POS.TICKER)
    h = t.history(period="5d")
    if h is None or len(h) < 2:
        raise RuntimeError("行情获取失败")
    cp = float(h["Close"].iloc[-1])
    pc = float(h["Close"].iloc[-2])
    quote = {"current_price": cp, "prev_close": pc,
             "change_pct": round((cp / pc - 1) * 100, 2),
             "quote_date": str(h.index[-1].date()),
             "note": "yfinance日K收盘价(盘中运行时为延迟价, 下单前以券商实时价确认)"}
    expiries = [date.fromisoformat(e) for e in t.options]
    future = [e for e in expiries if e >= target_expiry_min]
    chosen = future[0] if future else expiries[-1]
    oc = t.option_chain(chosen.isoformat())

    def rows(df_):
        out = []
        def _i(v):
            try:
                f = float(v)
                return 0 if (f != f) else int(f)  # NaN→0
            except Exception:
                return 0
        def _f(v):
            try:
                f = float(v)
                return 0.0 if (f != f) else f
            except Exception:
                return 0.0
        for _, r in df_.iterrows():
            bid, ask, last = _f(r.get("bid")), _f(r.get("ask")), _f(r.get("lastPrice"))
            if bid > 0 and ask > 0:
                mid = round((bid + ask) / 2, 2)
                sp = round((ask - bid) / mid * 100, 2) if mid else 999
            elif last > 0:
                mid, sp = last, 999.0
            else:
                continue
            out.append({"contract": str(r.get("contractSymbol", "")), "strike": _f(r["strike"]),
                        "last_price": last, "bid": bid, "ask": ask, "mid": mid,
                        "spread_pct": sp, "volume": _i(r.get("volume")),
                        "oi": _i(r.get("openInterest")),
                        "iv": round(_f(r.get("impliedVolatility")) * 100, 2)})
        return out
    return quote, chosen.isoformat(), rows(oc.calls), rows(oc.puts)


# ══════════ 策略选择（持仓感知版） ══════════
def choose_strategy(sig: Dict, mode: str) -> Tuple[str, str]:
    if mode == "cc":
        return "CC", "手动强制CC"
    if mode == "csp":
        return "CSP", "手动强制CSP"
    avail = POS.cc_shares_available()
    if avail < POS.SELL_QTY * 100:
        return "CSP", (f"可备兑股数仅{avail}股(总持仓{POS.TOTAL_SHARES}股, 其中{POS.cc_covered_used()}股已被在场CALL占用), "
                       f"不足新开{POS.SELL_QTY}张CC → 强制CSP。如需新开CC须先buy-to-close在场CALL75")
    d, s, v = sig.get("direction", "neutral"), sig.get("strength", "无"), sig.get("vote_sum", 0)
    if d == "bullish":
        return "CSP", f"量化{s}看涨(投票{v:+.1f})：上行中Put被行权概率低, 卖CC易被叫走错失涨幅 → CSP"
    if d == "bearish":
        return "CC", f"量化{s}看跌(投票{v:+.1f})：下行中CSP接飞刀风险高, CC收租不加下行敞口 → CC"
    return "CC", f"方向不明(投票{v:+.1f})：默认对存量持仓收益增强(CC)"


# ══════════ 环境评估 ══════════
def eval_price_state(cp, pc, strategy):
    if not pc:
        return {"name": "unknown", "label": "未知/中性", "change_pct": None, "note": "无昨收, 按中性"}
    chg = round((cp / pc - 1) * 100, 2)
    table = {
        "strong_up": ("明显上涨", {"CC": "偏强, 可贴近现价卖CC", "CSP": "偏强, CSP有利, 可贴近现价"}),
        "mild_up": ("温和上涨", {"CC": "偏强, 正常卖周内CC", "CSP": "偏强, 正常卖周内CSP"}),
        "strong_down": ("明显下跌", {"CC": "偏弱, 卖CC易卖低, 倾向跳过", "CSP": "偏弱, 行权风险升, 倾向跳过或深OTM"}),
        "mild_down": ("温和下跌", {"CC": "偏弱, 用更保守OTM", "CSP": "偏弱, 选更低执行价"}),
        "flat": ("横盘/中性", {"CC": "标准OTM 1~5%", "CSP": "标准OTM 1~5%"}),
    }
    name = ("strong_up" if chg >= STRONG_UP else "mild_up" if chg >= MILD_UP
            else "strong_down" if chg <= STRONG_DOWN else "mild_down" if chg <= MILD_DOWN else "flat")
    lbl, notes = table[name]
    return {"name": name, "label": lbl, "change_pct": chg, "note": notes[strategy]}


def eval_timing(now, strategy):
    wd = now.weekday()
    m = {(5, 6): ("weekend", "周末预案", "只做下周预案; 默认下周三主操作日"),
         (0, 1): ("early_week", "周一/周二观察期", "先观察; 除非股价明显上涨且权利金厚才提前做"),
         (2,): ("main_day", "周三主操作日", f"最适合决定是否卖周内{strategy}"),
         (3,): ("backup_day", "周四补单日", "周三没做可补看; 仍要求权利金与位置合理"),
         (4,): ("friday", "周五关闭新仓", f"默认不在周五新开{strategy}")}
    for k, v in m.items():
        if wd in k:
            return {"weekday": wd, "phase": v[0], "label": v[1], "note": v[2]}


def in_earnings_window():
    today = date.today()
    for ed in POS.EARNINGS_DATES:
        d = date.fromisoformat(ed)
        if abs((today - d).days) <= POS.EARNINGS_WINDOW_DAYS:
            return True, f"距财报日{ed}仅{abs((today-d).days)}天, 处于禁开新仓窗口"
    return False, ""


# ══════════ OTM调整（CC/CSP同构, 语义不同） ══════════
def adjust_otm(sig, strategy):
    d, s, v = sig.get("direction"), sig.get("strength"), sig.get("vote_sum", 0)
    lo, hi, tg = PREFERRED_OTM_MIN, PREFERRED_OTM_MAX, TARGET_OTM_PCT
    if d == "bullish" and s == "强":
        if strategy == "CSP":
            return max(0, lo - 0.5), hi, tg, f"强看涨({v:+.1f}): CSP可贴近现价, OTM下限降0.5%"
        return max(0, lo - 0.5), hi, tg, f"强看涨({v:+.1f}): CC可贴近现价博更高权利金"
    if d == "bearish":
        step = 1.0 if s == "强" else 0.5
        note = (f"{s}看跌({v:+.1f}): {'CSP行权风险升' if strategy=='CSP' else 'CC避开现价'}, OTM上移{step}%")
        return lo + step, hi + (step if s == "强" else 0), tg + step, note
    return lo, hi, tg, "方向不明, 维持原OTM区间"


# ══════════ 合约打分（CC/CSP统一, otm定义按策略切换） ══════════
def rank_contracts(contracts, cp, strategy, dte, ps, ts, lo, hi, tg):
    ranked = []
    for c in contracts:
        mid, oi, vol, sp = c["mid"], c["oi"], c["volume"], c["spread_pct"]
        if mid < MIN_MID:
            continue
        prem = round(mid * 100 * POS.SELL_QTY, 2)
        if prem < POS.MIN_TOTAL_PREMIUM_CONSIDER:
            continue
        otm = round(((c["strike"] - cp) if strategy == "CC" else (cp - c["strike"])) / cp * 100, 2)
        if otm < -MAX_ITM_PCT or otm > MAX_OTM_PCT:
            continue
        if oi < MIN_OI and vol < MIN_VOL:
            continue
        if sp > MAX_SPREAD_PCT_HARD and oi < 100:
            continue
        score = max(0, 45 - abs(otm - tg) * 8)
        score += 18 if lo <= otm <= hi else (10 if 0 <= otm < lo else 4 if otm < 0 else 0)
        score += min(25, prem / 20) + min(12, oi / 400 * 12) + min(10, vol / 500 * 10)
        score -= 10 if sp > 25 else 5 if sp > 15 else 0
        if otm < 0:
            score -= abs(otm) * 6
        ph = ts["phase"]
        if ph == "early_week":
            score += 3 if 2 <= otm <= 5 else (-6 if otm < 1 else 0)
        elif ph == "main_day":
            score += 4 if 1 <= otm <= 4.5 else 0
        elif ph == "backup_day":
            score += 5 if 0 <= otm <= 3.5 else (-4 if otm > 6 else 0)
        elif ph == "friday":
            score -= 20
        pn = ps["name"]
        bias = {"strong_up": [(0.5 if strategy == "CSP" else -0.5, 3.0, 8), (3.0, 5.5, 4)],
                "mild_up": [(0.5 if strategy == "CSP" else 0.0, 4.0, 5)],
                "flat": [(1.0, 5.0, 3)],
                "mild_down": [(3.0 if strategy == "CSP" else 2.5, 6.0, 4)],
                "strong_down": [(4.0 if strategy == "CSP" else 3.0, 7.0 if strategy == "CSP" else 6.0, 5)]}
        for a, b, pts in bias.get(pn, []):
            if a <= otm <= b:
                score += pts
        if pn == "mild_down" and otm < (2.0 if strategy == "CSP" else 1.0):
            score -= 8
        if pn == "strong_down" and otm < (2.5 if strategy == "CSP" else 1.5):
            score -= 12
        typ = ("ITM Put(危险)" if strategy == "CSP" and otm < 0 else "轻微ITM" if otm < 0
               else "ATM附近" if otm <= 1 else ("OTM Put" if strategy == "CSP" else "OTM"))
        ranked.append({**c, "dte": dte, "otm_pct": otm, "total_premium": prem,
                       "type": typ, "score": round(score, 2)})
    ranked.sort(key=lambda x: -x["score"])
    return ranked


def choose_primary(ranked, ps, strategy, lo, hi):
    if not ranked:
        return None
    pn = ps["name"]
    if pn in ("strong_up", "mild_up"):
        pools = [lambda r: (0.5 if strategy == "CSP" else -0.5) <= r["otm_pct"] <= 3.5,
                 lambda r: 0 <= r["otm_pct"] <= 5]
    elif pn in ("strong_down", "mild_down"):
        pools = [lambda r: 3 <= r["otm_pct"] <= 7, lambda r: 2 <= r["otm_pct"] <= 7, lambda r: 1 <= r["otm_pct"] <= 7]
    else:
        pools = [lambda r: lo <= r["otm_pct"] <= hi, lambda r: 0 <= r["otm_pct"] <= hi]
    for fn in pools:
        pool = [r for r in ranked if fn(r)]
        if pool:
            return pool[0]
    return ranked[0]


def choose_backup(ranked, primary, strategy):
    if not primary:
        return None
    pool = [r for r in ranked if r["contract"] != primary["contract"]
            and r["otm_pct"] > primary["otm_pct"] + (1.0 if strategy == "CSP" else 0)
            and 2.5 <= r["otm_pct"] <= 7 and r["total_premium"] >= POS.MIN_TOTAL_PREMIUM_CONSIDER]
    pool.sort(key=lambda x: (x["score"], x["otm_pct"]), reverse=True)
    return pool[0] if pool else None


def make_decision(primary, ps, ts, dte, sig, strategy, cc_blocked_note=None):
    if cc_blocked_note:
        return {"decision": "SKIP", "title": "本次CC不可执行", "reason": cc_blocked_note}
    if primary is None:
        return {"decision": "SKIP", "title": "本周不做", "reason": "过滤后无符合条件合约"}
    if dte <= 1:
        return {"decision": "SKIP", "title": "本周不做", "reason": "距到期太近"}
    if primary["total_premium"] < POS.MIN_TOTAL_PREMIUM_DO:
        return {"decision": "SKIP", "title": "本周不做",
                "reason": f"最佳候选权利金${primary['total_premium']:.2f}<门槛${POS.MIN_TOTAL_PREMIUM_DO}"}
    note = ""
    if sig.get("direction") == "bearish" and sig.get("strength") == "强":
        note = "; ⚠️量化强看跌, 已选更保守OTM" + ("(CSP建议评估是否本周暂停)" if strategy == "CSP" else "")
    ph, pn = ts["phase"], ps["name"]
    if ph == "weekend":
        return {"decision": "WAIT", "title": "周末预案", "reason": "只做下周预案, 默认周三主操作日" + note}
    if ph == "early_week":
        if pn in ("strong_up", "mild_up") and primary["total_premium"] >= max(POS.MIN_TOTAL_PREMIUM_DO, 25):
            return {"decision": "DO", "title": "今天做", "reason": "股价偏强且权利金足够, 可提前执行" + note}
        return {"decision": "WAIT", "title": "今天不做, 周三再看", "reason": "周一/周二默认观察" + note}
    if ph in ("main_day", "backup_day"):
        if pn == "strong_down":
            return {"decision": "SKIP", "title": "本周不做", "reason": f"股价明显偏弱, {strategy}不宜开仓" + note}
        return {"decision": "DO", "title": "今天做", "reason": f"{ts['label']}, 条件满足可执行" + note}
    return {"decision": "SKIP", "title": "本周不做", "reason": f"周五不新开{strategy}"}


def compare_iv(garch, contracts, cp, label):
    if "错误" in garch:
        return {"错误": garch["错误"]}
    valid = [c for c in contracts if c.get("iv", 0) > 0]
    if not valid:
        return {"错误": f"{label}链无有效IV"}
    atm = min(valid, key=lambda c: abs(c["strike"] - cp))
    g = garch["平均波动率"]
    d = atm["iv"] - g
    j = (f"ATM {label} IV({atm['iv']:.1f}%)明显高于GARCH({g:.1f}%), 期权贵, 卖方有利" if d > 5
         else f"ATM {label} IV({atm['iv']:.1f}%)明显低于GARCH({g:.1f}%), 权利金偏薄" if d < -5
         else f"ATM {label} IV({atm['iv']:.1f}%)≈GARCH({g:.1f}%), 定价合理")
    return {"ATM合约": atm["contract"], "ATM行权价": atm["strike"], "ATM_IV": atm["iv"],
            "GARCH预测": g, "差值": round(d, 2), "判断": j}


# ══════════ 主流程 ══════════
def main():
    mode = "auto"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1].lower()
    print(f"HPE智能期权策略 v3.0 (yfinance版) | {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 60)
    print("\n[持仓] " + f"{POS.TOTAL_SHARES}股@成本${POS.AVG_COST} | 可新开CC={POS.cc_shares_available()//100}张")
    for o in POS.OPEN_OPTIONS:
        dte_o = (o["expiry"] - date.today()).days
        print(f"  在场: {o['side'].upper()} {o['type']}{o['strike']:.0f} x{o['qty']} 到期{o['expiry']}(DTE{dte_o}) | {o['note']}")
    for n in POS.POSITION_NOTES:
        print(f"  {n}")

    print("\n[PART A] 量化分析层(本地Excel)...")
    quant = run_quant_analysis()
    df_hidden = quant.pop("_df", None)
    if "致命错误" in quant:
        print(f"❌ {quant['致命错误']}")
        sig = {"direction": "neutral", "strength": "无", "vote_sum": 0, "conclusion": "数据缺失", "reasons": []}
        garch = {"错误": "数据缺失"}
    else:
        sig, garch = quant["direction_signal"], quant["garch_result"]
        print(f"✓ 方向: {sig['conclusion']} (投票{sig['vote_sum']:+.1f})")

    print("\n[PART B] 策略选择...")
    strategy, why = choose_strategy(sig, mode)
    print(f"✓ {strategy} | {why}")

    in_earn, earn_msg = in_earnings_window()
    if in_earn:
        print(f"⚠️ {earn_msg}")

    print(f"\n[PART C] 抓取期权链(yfinance)...")
    now = datetime.now()
    target_fri = date.today() + timedelta(days=(4 - date.today().weekday()) % 7)
    try:
        quote, expiry, calls, puts = fetch_quote_and_chain(target_fri)
    except Exception as e:
        print(f"❌ 期权链获取失败: {e}")
        return
    cp = quote["current_price"]
    dte = (date.fromisoformat(expiry) - date.today()).days
    print(f"✓ 现价${cp:.2f}({quote['quote_date']}) | 到期{expiry}(DTE{dte}) | Call{len(calls)}/Put{len(puts)}")

    ps = eval_price_state(cp, quote["prev_close"], strategy)
    ts = eval_timing(now, strategy)
    lo, hi, tg, adj_note = adjust_otm(sig, strategy)
    contracts = calls if strategy == "CC" else puts
    ranked = rank_contracts(contracts, cp, strategy, dte, ps, ts, lo, hi, tg)
    primary = choose_primary(ranked, ps, strategy, lo, hi)
    backup = choose_backup(ranked, primary, strategy)

    cc_block = None
    if strategy == "CC" and POS.cc_shares_available() < POS.SELL_QTY * 100:
        cc_block = f"可备兑股数不足(在场CALL75已占用{POS.cc_covered_used()}股), 需先buy-to-close"
    if in_earn:
        cc_block = cc_block or earn_msg

    decision = make_decision(primary, ps, ts, dte, sig, strategy, cc_block)
    iv_cmp = compare_iv(garch, contracts, cp, "Call" if strategy == "CC" else "Put")

    # ── 报告 ──
    L = ["=" * 78, f"HPE 智能期权策略报告 v3.0 | 本次策略: {strategy}",
         f"生成: {datetime.now():%Y-%m-%d %H:%M:%S}", "=" * 78]
    L.append(f"\n【持仓状态】{POS.TOTAL_SHARES}股@${POS.AVG_COST}(负成本=零风险底仓) | 可新开CC {POS.cc_shares_available()//100}张")
    for o in POS.OPEN_OPTIONS:
        L.append(f"  在场: {o['type']}{o['strike']:.0f} 到期{o['expiry']} | {o['note']}")
    for n in POS.POSITION_NOTES:
        L.append(f"  {n}")
    if "致命错误" not in quant:
        L.append(f"\n【PART A 量化】数据截止{quant['数据截止日']} 收盘${quant['当前价_本地数据']}")
        L.append(f"  State={quant['state']} {quant['state_desc']}")
        if quant.get("state_window_warning"):
            L.append(f"  ⚠️ {quant['state_window_warning']}")
        chip = quant["chip_result"]
        if "错误" not in chip:
            L.append(f"  筹码: 获利{chip.get('获利比例',0):.1f}% 成本${chip.get('平均成本',0):.2f} "
                     f"日K偏离{chip.get('日K筹码偏离率',0):+.1f}%")
            if "筹码稳定性判断" in chip:
                L.append(f"  周K稳定性: {chip['筹码稳定性判断']}")
        for k, lbl in [("gbm_5d", "GBM5天"), ("gbm_10d", "GBM10天")]:
            g = quant.get(k, {})
            if "错误" not in g:
                L.append(f"  {lbl}: 中位${g['中位数']}({g['方向标签']}{g['上涨空间']:+.1f}%) 区间[${g['10%分位数']},${g['90%分位数']}]")
        hmm = quant["hmm_result"]
        if "错误" not in hmm:
            L.append(f"  HMM: {hmm['状态标签']}(持续{hmm['当前持续天数']}天) 保持概率{hmm['30天后状态概率']['保持当前']}%")
        if "错误" not in garch:
            L.append(f"  GARCH: 7日平均年化波动{garch['平均波动率']:.1f}%({garch['波动率趋势']})")
        L.append(f"  方向投票: {' / '.join(sig['reasons'])}")
        L.append(f"  ➤ {sig['conclusion']} (合计{sig['vote_sum']:+.1f})")
    L.append(f"\n【PART B 策略选择】{strategy} | {why}")
    L.append(f"\n【PART C {strategy}执行】现价${cp:.2f} 相对昨收{ps['change_pct']:+.2f}% | {ps['label']}({ps['note']})")
    L.append(f"  时间窗口: {ts['label']}({ts['note']})")
    L.append(f"  OTM联动: {adj_note} → 区间{lo:.1f}%~{hi:.1f}%(中枢{tg:.1f}%)")
    if "错误" not in iv_cmp:
        L.append(f"  IV比价: {iv_cmp['判断']}")
    L.append(f"\n  候选前5(共{len(ranked)}):")
    L.append(f"  {'Strike':>7} {'Mid':>6} {'权利金':>8} {'OTM%':>7} {'OI':>6} {'IV%':>6} {'分数':>6}")
    for r in ranked[:5]:
        L.append(f"  {r['strike']:>7.1f} {r['mid']:>6.2f} {r['total_premium']:>8.2f} "
                 f"{r['otm_pct']:>6.1f}% {r['oi']:>6} {r['iv']:>6.1f} {r['score']:>6.1f}")
    L.append(f"\n【结论】{decision['title']} | {decision['reason']}")
    if primary and decision["decision"] == "DO":
        L.append(f"\n  主策略: Sell to Open {primary['contract']} x{POS.SELL_QTY} Limit ${primary['mid']:.2f}")
        L.append(f"  执行价${primary['strike']:.2f} OTM{primary['otm_pct']:+.1f}% 预计收${primary['total_premium']:.2f}")
        if strategy == "CSP":
            net = primary["strike"] - primary["mid"]
            L.append(f"  被行权净成本=${net:.2f}/股 (对比正股负成本, 接股只是低价加仓)")
        if backup:
            L.append(f"  备选: {backup['contract']} ${backup['mid']:.2f} OTM{backup['otm_pct']:+.1f}% (更保守)")
    elif primary:
        L.append(f"\n  参考预案(非操作建议): {primary['contract']} Limit ${primary['mid']:.2f} 预计${primary['total_premium']:.2f}")
    L.append("\n" + "=" * 78)
    L.append("· 期权线证伪从属于HPE库thesis: 若9/2财报破坏基本面论点, 期权线整体停摆重评")
    L.append("· 本报告仅供研究, 不构成投资建议")
    report = "\n".join(L)

    REPORT_TXT.write_text(report, encoding="utf-8")
    jd = {"run_time": datetime.now().isoformat(), "strategy": strategy, "strategy_reason": why,
          "position": {"shares": POS.TOTAL_SHARES, "avg_cost": POS.AVG_COST,
                       "cc_available": POS.cc_shares_available() // 100,
                       "open_options": [{**o, "expiry": str(o["expiry"])} for o in POS.OPEN_OPTIONS]},
          "quant": {k: v for k, v in quant.items() if k != "_df"},
          "quote": quote, "expiry": expiry, "dte": dte,
          "decision": decision, "primary": primary, "backup": backup,
          "top5": ranked[:5], "iv_vs_garch": iv_cmp,
          "otm_adjust": {"lo": lo, "hi": hi, "target": tg, "note": adj_note}}
    REPORT_JSON.write_text(json.dumps(jd, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n" + report)
    print(f"\n✓ TXT: {REPORT_TXT}\n✓ JSON: {REPORT_JSON}")


if __name__ == "__main__":
    main()
