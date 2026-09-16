# -*- coding: utf-8 -*-
"""
HPE 量化分析研究脚本（整合优化版 · 多Sheet清晰输出）
================================================================
【归档说明】2026-09-05 由用户口述提供原稿，仓库补档（#29）。
  定位：用户本地（Windows）研究层工具——用本地 Excel 筹码底座（HPE N=3000.xlsx）
  做偏离度分桶胜率 / 回调支撑 / 获利比例 / 集中度 / 综合信号回测。
  血统：薅羊毛体系三代中的"统计父辈"——K_SELL/K_BUY(1.0/0.7) 与振幅锚的依据来源；
        后裔 = options/satellite_quote.py v5.0（正股卫星仓）→ PFE AAWS → HPE-AAWS（#26/#27）。
  运行环境：用户本地（pandas numpy openpyxl）；沙箱无 Excel 数据源，不跑。
  注意：归档保持用户原稿一字未改（research 层以用户本地版本为准）。
================================================================

- 合并两个脚本，消除重复代码
- 统一读取、清洗、计算流程
- 输出：控制台统计 + CSV原始数据 + Excel多Sheet结论
"""

import os
import numpy as np
import pandas as pd
from pathlib import Path

# ============================================================
# 配置区（只需改这里）
# ============================================================

DATA_DIR      = r"C:\Users\simon\Desktop\Stock Data Retrivel\US_stocks_Data"
DATA_FILENAME = "HPE N=3000.xlsx"
OUTPUT_DIR    = Path(r"C:\Users\simon\Desktop\Stock Data Retrivel")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_CSV  = OUTPUT_DIR / "HPE_量化分析结果.csv"
OUTPUT_EXCEL  = OUTPUT_DIR / "HPE_量化分析结论.xlsx"

CHIP_COLS = ["获利比例", "90%成本-低", "90%成本-高", "90%集中度",
             "70%成本-低", "70%成本-高", "70%集中度"]

# ============================================================
# 1. 数据读取 & 清洗
# ============================================================

def load_data(data_dir: str, filename: str) -> pd.DataFrame:
    filepath = os.path.join(data_dir, filename)
    print(f"读取文件：{filepath}")

    preview = pd.read_excel(filepath, engine="openpyxl", header=None, nrows=10)
    hdr_row = None
    for i, row in preview.iterrows():
        vals = [str(v).strip() for v in row]
        if "日期" in vals and "收盘" in vals:
            hdr_row = i
            break
    if hdr_row is None:
        raise ValueError('未找到包含"日期"和"收盘"的表头行，请检查文件格式。')

    df = pd.read_excel(filepath, engine="openpyxl", header=hdr_row)
    df.columns = [str(c).strip() for c in df.columns]

    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期"]).sort_values("日期").reset_index(drop=True)

    for col in ["开盘", "收盘", "最高", "最低", "成交量", "成交额",
                "5日涨幅", "振幅", "换手率", "量比"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "", regex=False),
                errors="coerce"
            )

    for col in CHIP_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["开盘", "收盘", "最高", "最低", "成交量"]).reset_index(drop=True)
    df["成交量"] = df["成交量"].astype(float)

    print(f"有效数据：{len(df)} 行")
    print(f"时间范围：{df['日期'].min().date()} ~ {df['日期'].max().date()}\n")
    return df


# ============================================================
# 2. 计算技术指标
# ============================================================

def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    print("计算技术指标...")

    for n in [5, 10, 15, 20, 30, 60, 120]:
        df[f"MA{n}"] = df["收盘"].rolling(n).mean()

    for n in [5, 15, 30, 60]:
        df[f"偏离MA{n}"] = (df["收盘"] / df[f"MA{n}"] - 1) * 100

    for d in [1, 3, 5]:
        df[f"收益{d}D"] = df["收盘"].shift(-d) / df["收盘"] - 1

    df["波动5D"] = df["收盘"].pct_change().rolling(5).std().shift(-5)

    tr = pd.concat([
        df["最高"] - df["最低"],
        (df["最高"] - df["收盘"].shift(1)).abs(),
        (df["最低"] - df["收盘"].shift(1)).abs()
    ], axis=1).max(axis=1)
    df["ATR14"] = tr.rolling(14).mean()

    return df


# ============================================================
# 3. 汇总表构建（一个函数管一种分析，返回一个 DataFrame → 一个 Sheet）
# ============================================================

def _win_rate(x):
    return round((x > 0).sum() / len(x) * 100, 1) if len(x) else 0

def _mean_pct(x):
    return round(x.mean() * 100, 2) if len(x.dropna()) else None


def build_deviation_sheet(df: pd.DataFrame, col_name: str) -> pd.DataFrame:
    """单个偏离度分析 → 独立Sheet"""
    bins   = [-100, -15, -10, -5, -3, 0, 3, 5, 10, 15, 100]
    labels = ['<-15%', '-15~-10%', '-10~-5%', '-5~-3%', '-3~0%',
              '0~3%', '3~5%', '5~10%', '10~15%', '>15%']
    tmp = df.copy()
    tmp['偏离区间'] = pd.cut(tmp[col_name], bins=bins, labels=labels)
    grp = tmp.groupby('偏离区间', observed=False).agg(
        样本数        =('收益5D', 'count'),
        平均1D收益_pct=('收益1D', _mean_pct),
        胜率1D_pct    =('收益1D', _win_rate),
        平均3D收益_pct=('收益3D', _mean_pct),
        胜率3D_pct    =('收益3D', _win_rate),
        平均5D收益_pct=('收益5D', _mean_pct),
        胜率5D_pct    =('收益5D', _win_rate),
    ).reset_index()
    return grp


def build_chip_profit_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """获利比例分析"""
    if "获利比例" not in df.columns or len(df["获利比例"].dropna()) <= 50:
        return pd.DataFrame({"说明": ["获利比例数据不足"]})
    bins_pr = [0, 20, 40, 60, 80, 100]
    lbl_pr  = ['0-20%', '20-40%', '40-60%', '60-80%', '80-100%']
    tmp = df.copy()
    tmp['获利比例区间'] = pd.cut(tmp["获利比例"], bins=bins_pr, labels=lbl_pr)
    grp = tmp.groupby('获利比例区间', observed=False).agg(
        样本数        =('收益5D', 'count'),
        平均3D收益_pct=('收益3D', _mean_pct),
        胜率3D_pct    =('收益3D', _win_rate),
        平均5D收益_pct=('收益5D', _mean_pct),
        胜率5D_pct    =('收益5D', _win_rate),
        波动5D_pct    =('波动5D', _mean_pct),
    ).reset_index()
    return grp


def build_pullback_sheet(df: pd.DataFrame, threshold: float = 0.03) -> pd.DataFrame:
    """回调至均线支撑（三根均线合并到一个Sheet，行少内容全）"""
    rows = []
    df_trend = df[df["MA60"] > df["MA60"].shift(5)]

    for ma_col in ["MA15", "MA30", "MA60"]:
        df_near = df_trend[abs(df_trend["收盘"] / df_trend[ma_col] - 1) <= threshold]
        if len(df_near) == 0:
            rows.append({"均线": ma_col, "分类": "合计", "样本数": 0,
                         "3D收益_pct": None, "3D胜率_pct": None,
                         "5D收益_pct": None, "5D胜率_pct": None})
            continue

        # 合计
        r3, r5 = df_near["收益3D"].dropna(), df_near["收益5D"].dropna()
        rows.append({
            "均线": ma_col, "分类": "合计", "样本数": len(df_near),
            "3D收益_pct": _mean_pct(r3), "3D胜率_pct": _win_rate(r3),
            "5D收益_pct": _mean_pct(r5), "5D胜率_pct": _win_rate(r5),
        })

        # 细分
        diff = df_near["收盘"] / df_near[ma_col] - 1
        for tag, mask in [("回调中(偏离<0)", diff < 0),
                          ("刚突破(偏离≥0)", diff >= 0)]:
            sub = df_near[mask]
            if len(sub) < 5:
                continue
            r3, r5 = sub["收益3D"].dropna(), sub["收益5D"].dropna()
            rows.append({
                "均线": ma_col, "分类": tag, "样本数": len(sub),
                "3D收益_pct": _mean_pct(r3), "3D胜率_pct": _win_rate(r3),
                "5D收益_pct": _mean_pct(r5), "5D胜率_pct": _win_rate(r5),
            })
    return pd.DataFrame(rows)


def build_concentration_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """筹码集中度分析"""
    if "90%集中度" not in df.columns or len(df["90%集中度"].dropna()) <= 50:
        return pd.DataFrame({"说明": ["筹码集中度数据不足"]})
    med = df["90%集中度"].median()
    tmp = df.copy()
    tmp['集中度分组'] = np.where(tmp["90%集中度"] <= med, '低（筹码集中）', '高（筹码分散）')
    grp = tmp.groupby('集中度分组').agg(
        样本数     =('收益5D', 'count'),
        平均5D收益_pct=('收益5D', _mean_pct),
        胜率5D_pct =('收益5D', _win_rate),
        波动5D_pct =('波动5D', _mean_pct),
    ).reset_index()
    # 追加一行说明中位数
    note = pd.DataFrame([{"集中度分组": f"中位数={med:.2f}", "样本数": None,
                          "平均5D收益_pct": None, "胜率5D_pct": None, "波动5D_pct": None}])
    return pd.concat([grp, note], ignore_index=True)


def build_combined_signal_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """综合信号回测"""
    cond_trend = df["MA60"] > df["MA60"].shift(10)
    cond_chip  = ((df["获利比例"] >= 30) & (df["获利比例"] <= 70)
                  if "获利比例" in df.columns else pd.Series(True, index=df.index))
    cond_dev   = abs(df["偏离MA15"]) <= 4
    cond_vol   = (df["成交量"] / df["成交量"].rolling(20).mean()) >= 0.6
    df["综合信号"] = cond_trend & cond_chip & cond_dev & cond_vol

    rows = []
    for label, mask in [("✅ 综合信号触发", df["综合信号"]),
                        ("📊 无信号对照",   ~df["综合信号"])]:
        sub = df[mask]["收益5D"].dropna()
        if len(sub) < 20:
            rows.append({"场景": label, "样本数": len(sub),
                         "5D收益_pct": None, "胜率_pct": None,
                         "最大收益_pct": None, "最大亏损_pct": None, "盈亏比": None})
            continue
        wins, loss = sub[sub > 0], sub[sub < 0]
        pr = wins.mean() / abs(loss.mean()) if len(loss) else None
        rows.append({
            "场景":        label,
            "样本数":      len(sub),
            "5D收益_pct":  round(sub.mean() * 100, 2),
            "胜率_pct":    round(len(wins) / len(sub) * 100, 1),
            "最大收益_pct": round(sub.max() * 100, 2),
            "最大亏损_pct": round(sub.min() * 100, 2),
            "盈亏比":      round(pr, 2) if pr else None,
        })
    return pd.DataFrame(rows)


def build_conclusion_sheet() -> pd.DataFrame:
    return pd.DataFrame({
        "结论": [
            "最佳买入信号",
            "最佳卖出信号",
            "回调支撑最有效",
            "获利比例最优区间",
            "综合信号是否有效",
        ],
        "内容": [
            "偏离MA15/-MA60在-15%~-10%时，5日胜率最高（~64-68%）",
            "偏离MA15 > +15% 或 MA30 < -15% 时，短期动量延续",
            "MA60刚突破（偏离≥0）时，5日收益约+1.35%，胜率约60.8%",
            "获利比例0-20%（极度套牢盘）时，5日收益约+1.96%，胜率60.3%",
            "综合信号 vs 无信号需查看【综合信号】Sheet对比",
        ],
    })


# ============================================================
# 4. 控制台打印（可选，快速看结果）
# ============================================================

def print_console_summary(df: pd.DataFrame):
    print("\n" + "="*60)
    print("控制台快速预览（详细数据见Excel）")
    print("="*60)

    for col in ["偏离MA15", "偏离MA30", "偏离MA60"]:
        print(f"\n【{col}】")
        print(build_deviation_sheet(df, col).to_string(index=False))

    print("\n【回调至均线支撑】")
    print(build_pullback_sheet(df).to_string(index=False))

    print("\n【获利比例】")
    print(build_chip_profit_sheet(df).to_string(index=False))

    print("\n【筹码集中度】")
    print(build_concentration_sheet(df).to_string(index=False))

    print("\n【综合信号】")
    print(build_combined_signal_sheet(df).to_string(index=False))


# ============================================================
# 5. 主流程
# ============================================================

def main():
    df = load_data(DATA_DIR, DATA_FILENAME)
    df = calc_indicators(df)

    # 控制台预览
    print_console_summary(df)

    # 导出 CSV（原始计算数据）
    export_cols = (
        ["日期", "收盘", "MA5", "MA15", "MA30", "MA60",
         "偏离MA5", "偏离MA15", "偏离MA30", "偏离MA60",
         "收益1D", "收益3D", "收益5D", "ATR14"]
        + [c for c in CHIP_COLS if c in df.columns]
    )
    df[export_cols].to_csv(ANALYSIS_CSV, index=False, encoding="utf-8-sig")
    print(f"\n✅ 原始分析数据已导出：{ANALYSIS_CSV}")

    # 导出 Excel（多Sheet结论）
    with pd.ExcelWriter(OUTPUT_EXCEL, engine="openpyxl") as writer:
        build_conclusion_sheet().to_excel(writer,          sheet_name="0_结论速查",     index=False)
        build_deviation_sheet(df, "偏离MA15").to_excel(writer, sheet_name="1_偏离MA15",   index=False)
        build_deviation_sheet(df, "偏离MA30").to_excel(writer, sheet_name="2_偏离MA30",   index=False)
        build_deviation_sheet(df, "偏离MA60").to_excel(writer, sheet_name="3_偏离MA60",   index=False)
        build_pullback_sheet(df).to_excel(writer,          sheet_name="4_回调支撑",     index=False)
        build_chip_profit_sheet(df).to_excel(writer,       sheet_name="5_获利比例",     index=False)
        build_concentration_sheet(df).to_excel(writer,     sheet_name="6_筹码集中度",   index=False)
        build_combined_signal_sheet(df).to_excel(writer,   sheet_name="7_综合信号",     index=False)

    print(f"✅ 分析结论已导出：{OUTPUT_EXCEL}")
    print("\n" + "="*60)
    print("全部分析完成！打开Excel按顺序看Sheet 0→7")


if __name__ == "__main__":
    main()
