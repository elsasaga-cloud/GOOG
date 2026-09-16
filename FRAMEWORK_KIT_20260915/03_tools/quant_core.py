# -*- coding: utf-8 -*-
"""
HPE 量化底座（PART A共享模块）—— options_engine与satellite_quote共同调用
数据源: 本地Excel(同花顺导出, 含筹码列, 倒抓3000天) —— 这是整条期权线的量化依据
逻辑与原融合版v2.0完全一致: State十均线/筹码(含周K锁仓)/GBM/HMM三重修复/GARCH/方向投票
"""
import os, warnings
from typing import Optional, Tuple, Dict, List
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

DATA_DIR = os.environ.get("HPE_DATA_DIR", r"C:\Users\simon\Desktop\Stock Data Retrivel\US_stocks_Data")
TICKER = "HPE"
MA_LIST_DAILY = [5, 15, 30, 45, 60, 75, 90, 105, 120, 150]
GBM_N_SIMULATIONS = 10000
GBM_LOOKBACK = 252
HMM_N_STATES = 3
HMM_STATE_LABELS = {0: "熊市", 1: "震荡", 2: "牛市"}
HMM_FEATURE_SMOOTH_WINDOW = 5
HMM_DIAG_INIT = 0.9
HMM_MIN_SELFTRANS = 0.85
ABNORMAL_RETURN_THRESHOLD = 0.20
CHIP_LOOK = 5


def find_excel() -> Optional[str]:
    if not os.path.exists(DATA_DIR):
        print(f"❌ 数据目录不存在：{DATA_DIR}"); return None
    files = [f for f in os.listdir(DATA_DIR) if f.endswith(".xlsx") and TICKER.upper() in f.upper()]
    if not files:
        print(f"❌ 未找到包含'{TICKER}'的Excel"); return None
    if len(files) > 1:
        print(f"⚠️ 多个匹配，使用：{files[0]}")
    return os.path.join(DATA_DIR, files[0])


def load_stock_data(filepath: str) -> Optional[pd.DataFrame]:
    req = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]
    try:
        prev = pd.read_excel(filepath, engine="openpyxl", header=None, nrows=10)
        hdr = next((i for i in range(len(prev))
                    if "日期" in [str(v).strip() for v in prev.iloc[i]] and "开盘" in [str(v).strip() for v in prev.iloc[i]]), None)
        if hdr is None:
            print("❌ 未定位表头"); return None
        df = pd.read_excel(filepath, engine="openpyxl", header=hdr)
        df.columns = [str(c).strip() for c in df.columns]
    except Exception as e:
        print(f"❌ 读取失败：{repr(e)[:100]}"); return None
    if [c for c in req if c not in df.columns]:
        print("❌ 缺少必需列"); return None
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期"]).sort_values("日期").reset_index(drop=True)
    for c in [c for c in df.columns if c != "日期"]:
        if df[c].dtype.kind in ("O", "U"):
            df[c] = (df[c].astype(str).str.replace(",", "", regex=False).str.strip()
                     .replace({"-": np.nan, "": np.nan, "nan": np.nan}))
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["开盘", "收盘", "最高", "最低", "成交量"])
    df = df[df["成交量"] > 0].reset_index(drop=True)
    if len(df) == 0:
        print("❌ 清洗后无数据"); return None
    print(f"✓ Excel加载：{len(df)}行 {df['日期'].iloc[0].date()}~{df['日期'].iloc[-1].date()}")
    return df


def _ma_d(df, n):
    col = f"日MA{n}"
    return df[col] if col in df.columns else None


def _ma_w(df, n):
    col = f"周MA{n}"
    return df[col] if col in df.columns else None


def detect_state(df) -> Tuple[str, str, Optional[bool]]:
    """果论State（仅日线MA判定）"""
    if len(df) < 150:
        return ("数据不足", "K线不足150根", None)
    P = df["收盘"].iloc[-1]
    mas = {n: (_ma_d(df, n).iloc[-1] if _ma_d(df, n) is not None else None) for n in MA_LIST_DAILY}
    ma5, ma15, ma30, ma45 = mas.get(5), mas.get(15), mas.get(30), mas.get(45)
    ma60, ma75, ma90, ma105 = mas.get(60), mas.get(75), mas.get(90), mas.get(105)
    if any(v is None or pd.isna(v) for v in [ma30, ma60, ma90]):
        return ("数据不足", "核心均线缺失", None)
    if all(v is not None and not pd.isna(v) for v in [ma5, ma15, ma45, ma75, ma105]):
        if P > ma5 > ma15 > ma30 > ma45 > ma60 > ma75 > ma90 > ma105:
            return ("3+", "完整多头排列", True)
    if P > ma30 > ma60 > ma90:
        return ("3", "结构性多头 P>MA30>MA60>MA90", True)
    if P < ma30 < ma60 < ma90:
        return ("0", "结构性空头", False)
    valid = [v for v in [ma15, ma30, ma45, ma60] if v is not None and not pd.isna(v)]
    if valid and P > 0 and max(abs(P - m) / P for m in valid) < 0.03:
        return ("粘合", "均线高度收敛，方向待选择", None)
    return ("X", "均线结构混乱或横盘", None)


def _segments(states):
    if not states:
        return []
    segs, cur, cnt = [], states[0], 1
    for s in states[1:]:
        if s == cur:
            cnt += 1
        else:
            segs.append((cur, cnt)); cur, cnt = s, 1
    segs.append((cur, cnt))
    return segs


def analyze_state_history(df) -> Dict:
    if len(df) < 250:
        return {"错误": "数据不足250行"}
    states = [detect_state(df.iloc[:i + 1])[0] for i in range(150, len(df))]
    segs = _segments(states)
    s3 = [l for s, l in segs if s in ("3", "3+")]
    cur = segs[-1][1] if segs and segs[-1][0] in ("3", "3+") else 0
    hist = s3[:-1] if (cur and s3) else s3
    if not hist and cur == 0:
        return {"平均持续天数": 0, "最长持续天数": 0, "历史进入次数": 0, "当前持续天数": 0}
    return {"平均持续天数": int(np.mean(hist)) if hist else cur,
            "最长持续天数": int(max(hist)) if hist else cur,
            "历史进入次数": len(hist) + (1 if cur else 0), "当前持续天数": cur}


def analyze_chips(df) -> Dict:
    if "获利比例" not in df.columns or "平均成本" not in df.columns:
        return {"错误": "缺少筹码数据列"}
    last = df.iloc[-1]
    cp = last["收盘"]
    sf = lambda v, d=0: d if pd.isna(v) else float(v)
    r = {"获利比例": round(sf(last.get("获利比例")), 2), "平均成本": round(sf(last.get("平均成本")), 2)}
    if r["平均成本"] > 0:
        r["价格vs成本"] = round((cp / r["平均成本"] - 1) * 100, 2)
    for k in ["日K筹码平均成本", "周K筹码平均成本", "日K筹码偏离率", "周K筹码偏离率"]:
        v = last.get(k)
        if pd.notna(v):
            r[k] = round(float(v), 2)
    dk, wk = r.get("日K筹码偏离率"), r.get("周K筹码偏离率")
    if dk is not None and wk is not None and ((dk > 0) != (wk > 0)):
        r["日周筹码背离提示"] = "日K与周K筹码偏离方向不一致，短中期筹码结构存在真实分歧"
    if "周K筹码平均成本" in df.columns:
        s = df["周K筹码平均成本"].dropna().tail(100)
        if len(s) >= 20 and s.mean():
            amp = (s.max() - s.min()) / s.mean() * 100
            slope = (s.iloc[-1] - s.iloc[0]) / s.iloc[0] * 100 if s.iloc[0] else 999
            r["周K成本振幅占比"], r["周K成本斜率占比"] = round(amp, 2), round(slope, 2)
            if amp <= 22 and slope <= 25:
                r["筹码稳定性判断"] = "周K成本相对锁死，符合机构锁仓特征"
            elif amp > 40 or slope > 40:
                r["筹码稳定性判断"] = "周K成本波动较大，警惕假锁仓/派发"
            else:
                r["筹码稳定性判断"] = "周K成本中等稳定"
    lo70, hi70 = sf(last.get("70%成本-低")), sf(last.get("70%成本-高"))
    lo90, hi90 = sf(last.get("90%成本-低")), sf(last.get("90%成本-高"))
    if hi70 > lo70:
        r["70%成本区间"] = f"${lo70:.2f}~${hi70:.2f}"
        r["70%成本-低"], r["70%成本-高"] = lo70, hi70
        r["70%筹码中枢"] = (lo70 + hi70) / 2
    if hi90 > lo90 and cp < hi90:
        r["解套盘压力"] = min(round((hi90 - cp) / (hi90 - lo90) * 90, 1), 90)
    return r


def predict_price_gbm(df, days: int) -> Dict:
    if len(df) < 100:
        return {"错误": "数据不足"}
    cp = df["收盘"].iloc[-1]
    look = min(len(df), GBM_LOOKBACK)
    rets = np.log(df["收盘"].tail(look) / df["收盘"].tail(look).shift(1)).dropna()
    rets = rets[abs(rets) < ABNORMAL_RETURN_THRESHOLD]
    if len(rets) < 20:
        return {"错误": "有效数据不足"}
    mu, sig = rets.mean() * 252, rets.std() * np.sqrt(252)
    n = max(int(days / 252 / (1 / 252)), 1)
    paths = np.zeros((GBM_N_SIMULATIONS, n)); paths[:, 0] = cp
    for t in range(1, n):
        Z = np.random.standard_normal(GBM_N_SIMULATIONS)
        paths[:, t] = paths[:, t - 1] * (1 + (mu - 0.5 * sig ** 2) / 252 + sig / np.sqrt(252) * Z)
    fin = paths[:, -1]
    p10, p50, p90 = np.percentile(fin, [10, 50, 90])
    up, dn = (p50 / cp - 1) * 100, (p10 / cp - 1) * 100
    return {"预测天数": days, "当前价": round(float(cp), 2), "10%分位数": round(p10, 2),
            "中位数": round(p50, 2), "90%分位数": round(p90, 2),
            "方向标签": "上涨预期" if up >= 0 else "下跌预期", "上涨空间": round(up, 1),
            "下跌风险": round(dn, 1), "对称赔率": round(abs((p90 / cp - 1) * 100 / dn), 2) if dn else 0,
            "GBM参数": {"mu": round(mu * 100, 1), "sigma": round(sig * 100, 1), "样本天数": len(rets)}}


def fit_hmm(df) -> Dict:
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError:
        return {"错误": "需要hmmlearn"}
    if len(df) < 500:
        return {"错误": "数据不足500行"}
    try:
        rr = df["收盘"].pct_change().fillna(0)
        rv = df["成交量"].pct_change().fillna(0)
        f = np.column_stack([rr.rolling(HMM_FEATURE_SMOOTH_WINDOW, min_periods=1).mean().values,
                             rv.rolling(HMM_FEATURE_SMOOTH_WINDOW, min_periods=1).mean().values])
        f = np.nan_to_num((f - f.mean(0)) / (f.std(0) + 1e-8))
        off = (1 - HMM_DIAG_INIT) / (HMM_N_STATES - 1)
        tm = np.full((HMM_N_STATES, HMM_N_STATES), off); np.fill_diagonal(tm, HMM_DIAG_INIT)
        m = GaussianHMM(n_components=HMM_N_STATES, n_iter=100, random_state=42, init_params="mc")
        m.startprob_ = np.full(HMM_N_STATES, 1 / HMM_N_STATES); m.transmat_ = tm
        m.fit(f)
        # 最小自转移约束
        t2 = m.transmat_.copy()
        for i in range(HMM_N_STATES):
            if t2[i, i] < HMM_MIN_SELFTRANS:
                o = t2[i].sum() - t2[i, i]
                if o > 0:
                    t2[i] *= (1 - HMM_MIN_SELFTRANS) / o
                t2[i, i] = HMM_MIN_SELFTRANS
        m.transmat_ = t2 / t2.sum(1, keepdims=True)
        raw = m.predict(f)
        means = {s: rr.values[raw == s].mean() if (raw == s).sum() else 0 for s in range(HMM_N_STATES)}
        remap = {old: new for new, old in enumerate(sorted(means, key=means.get))}
        st = np.array([remap[s] for s in raw])
        cur = st[-1]
        rt = np.zeros_like(m.transmat_)
        for i0, i1 in remap.items():
            for j0, j1 in remap.items():
                rt[i1, j1] = m.transmat_[i0, j0]
        segs = _segments(list(st))
        curd = segs[-1][1] if segs and segs[-1][0] == cur else 0
        hist = [l for s, l in segs if s == cur][:-1] if curd else [l for s, l in segs if s == cur]
        out = {"当前状态": int(cur), "状态标签": HMM_STATE_LABELS[cur], "当前持续天数": int(curd),
               "平均持续天数": int(np.mean(hist)) if hist else curd,
               "30天后状态概率": {"保持当前": round(rt[cur, cur] * 100, 1)}}
        for s, name in HMM_STATE_LABELS.items():
            if s != cur:
                out["30天后状态概率"][f"转{name}"] = round(rt[cur, s] * 100, 1)
        return out
    except Exception as e:
        return {"错误": f"HMM训练失败：{repr(e)[:80]}"}


def predict_garch(df, horizon=7) -> Dict:
    try:
        from arch import arch_model
    except ImportError:
        return {"错误": "需要arch"}
    if len(df) < 500:
        return {"错误": "数据不足500行"}
    try:
        r = df["收盘"].pct_change().dropna() * 100
        r = r[abs(r) < ABNORMAL_RETURN_THRESHOLD * 100]
        vr = df["成交量"] / df["成交量"].shift(1)
        r = r.drop(vr[(vr > 5) | (df["成交量"] == 0)].index, errors="ignore")
        if len(r) < 100:
            return {"错误": "清洗后数据不足"}
        res = arch_model(r, vol="Garch", p=1, q=1).fit(disp="off", show_warning=False)
        sig = np.sqrt(res.forecast(horizon=horizon).variance.values[-1]) * np.sqrt(252)
        return {"预测天数": horizon, "平均波动率": round(float(sig.mean()), 2),
                "波动率趋势": "上升" if sig[-1] > sig[0] else "下降"}
    except Exception as e:
        return {"错误": f"GARCH拟合失败：{repr(e)[:50]}"}


def compute_direction_signal(state, hmm, chip) -> Dict:
    votes, reasons = [], []
    if state in ("3", "3+"):
        votes.append(1); reasons.append(f"State={state}多头放行(+1)")
    elif state == "0":
        votes.append(-1); reasons.append("State=0空头(-1)")
    else:
        votes.append(0); reasons.append(f"State={state}方向不明(0)")
    if "错误" not in hmm:
        lbl = hmm["状态标签"]
        if lbl == "牛市":
            votes.append(1); reasons.append("HMM牛市(+1)")
        elif lbl == "熊市":
            votes.append(-1); reasons.append("HMM熊市(-1)")
        else:
            votes.append(0); reasons.append("HMM震荡(0)")
    dk = chip.get("日K筹码偏离率") if "错误" not in chip else None
    if dk is not None:
        if dk > 35:
            votes.append(-0.5); reasons.append(f"日K筹码偏离{dk:+.1f}%偏热(-0.5)")
        elif dk < -10:
            votes.append(0.5); reasons.append(f"日K筹码偏离{dk:+.1f}%低位(+0.5)")
        else:
            reasons.append(f"日K筹码偏离{dk:+.1f}%中性(不计)")
    v = sum(votes)
    if v >= 1.5:
        d, s, c = "bullish", "强", "看涨倾向（趋势偏强）"
    elif v >= 0.5:
        d, s, c = "bullish", "弱", "弱看涨倾向"
    elif v <= -1.5:
        d, s, c = "bearish", "强", "看跌倾向（趋势偏弱）"
    elif v <= -0.5:
        d, s, c = "bearish", "弱", "弱看跌倾向"
    else:
        d, s, c = "neutral", "无", "方向不明确"
    return {"direction": d, "strength": s, "vote_sum": v, "conclusion": c, "reasons": reasons}


def run_quant_analysis() -> Dict:
    """整套PART A，返回汇总dict（含direction_signal/garch_result等）"""
    out = {}
    fp = find_excel()
    if not fp:
        out["致命错误"] = "未找到本地Excel"; return out
    df = load_stock_data(fp)
    if df is None:
        out["致命错误"] = "Excel加载失败"; return out
    out["当前价_本地数据"] = round(float(df["收盘"].iloc[-1]), 2)
    out["数据截止日"] = str(df["日期"].iloc[-1].date())
    state, desc, gate = detect_state(df)
    out["state"], out["state_desc"], out["state_gate"] = state, desc, gate
    out["state_history"] = analyze_state_history(df)
    sh = out["state_history"]
    if state in ("3", "3+") and "错误" not in sh and sh.get("平均持续天数", 0) > 0:
        cur, avg = sh["当前持续天数"], sh["平均持续天数"]
        if cur >= avg:
            out["state_window_warning"] = f"多头State已持续{cur}天≥历史平均{avg}天，5-10日内变盘概率上升"
        elif avg - cur <= 5:
            out["state_window_warning"] = f"多头State持续{cur}天，距历史平均{avg}天仅剩{avg-cur}天，与期权周期重叠"
    out["chip_result"] = analyze_chips(df)
    out["gbm_5d"] = predict_price_gbm(df, 5)
    out["gbm_10d"] = predict_price_gbm(df, 10)
    out["hmm_result"] = fit_hmm(df)
    out["garch_result"] = predict_garch(df, 7)
    out["direction_signal"] = compute_direction_signal(state, out["hmm_result"], out["chip_result"])
    out["_df"] = df  # 供satellite_quote复用，报告输出时剔除
    return out
