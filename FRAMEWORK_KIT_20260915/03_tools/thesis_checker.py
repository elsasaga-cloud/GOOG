#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HPE thesis 半自动核查器：只读数据，生成建议，不自动修改 thesis.md。"""
from __future__ import annotations
import csv
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data" / "HPE" / "fundamentals_quarterly.csv"
OUT_DIR = ROOT / "reports" / "HPE"

def load_rows():
    with CSV.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))

def latest(rows, metric, period=None):
    found = [r for r in rows if r["metric"] == metric and (period is None or r["period"] == period)]
    return found[-1] if found else None

def val(row):
    return float(row["value"]) if row else None

def main():
    rows = load_rows()
    periods = sorted({r["period"] for r in rows})
    current = periods[-1] if periods else "unknown"
    prior = periods[-2] if len(periods) > 1 else None
    nm = latest(rows, "networking_operating_margin", current)
    nm_prior = latest(rows, "networking_operating_margin", prior)
    fcf = latest(rows, "fy26_fcf_guidance", current)
    ai = latest(rows, "ai_system_orders", current)
    gm = latest(rows, "non_gaap_gross_margin", current)
    debt = latest(rows, "net_debt_to_ebitda", current)
    organic = latest(rows, "organic_revenue_growth", current)

    t1 = "✅" if val(nm) is not None and val(nm) >= 22.5 else "⚠️" if val(nm) is not None else "—"
    t3 = "✅" if val(fcf) is not None and val(fcf) >= 3.5 else "⚠️" if val(fcf) is not None else "—"
    t4 = "✅/待持续" if val(ai) is not None and val(gm) is not None and val(gm) >= 33 else "⚠️" if val(ai) is not None else "—"
    t5 = "✅" if val(debt) is not None and val(debt) < 3.0 else "➖" if val(debt) is not None else "—"
    t2 = "待补数据" if organic is None else ("✅" if val(organic) >= 10 else "➖")

    out = OUT_DIR / f"thesis_check_{datetime.now():%Y%m%d}.md"
    lines = [
        f"# HPE Thesis 半自动核查建议 · {current}",
        "",
        "> 本文件由脚本根据结构化数据生成，仅供人工审核；不会自动修改 `thesis.md`。缺数据不判定为利空。",
        "",
        "| 论点 | 建议状态 | 数据 | 说明 |",
        "|---|:---:|---:|---|",
        f"| T1 网络成为利润引擎 | {t1} | 网络margin {val(nm) if nm else '缺失'}%（前期 {val(nm_prior) if nm_prior else '缺失'}%） | 22.5%为本季度恢复参考线；连续低于20%才触发破坏 |",
        f"| T2 有机增长双位数 | {t2} | {val(organic) if organic else '缺失'}% | 需要剔除Juniper并表的数据，不能用总营收替代 |",
        f"| T3 FCF修复 | {t3} | FY26指引≥${val(fcf) if fcf else '缺失'}B | 只读指引，需结合实际现金流构成 |",
        f"| T4 AI订单且不牺牲毛利 | {t4} | AI订单${val(ai) if ai else '缺失'}B；毛利{val(gm) if gm else '缺失'}% | 订单需继续转收入、利润和现金流 |",
        f"| T5 去杠杆 | {t5} | 净债务/EBITDA {val(debt) if debt else '缺失'}x | 低于3x为改善参考，不覆盖原有thesis规则 |",
        "",
        "## 人工确认",
        "",
        "- 是否为一次性因素：",
        "- 管理层解释是否可信：",
        "- 是否需要补充10-Q/电话会/客户资料：",
        "- 最终是否更新 `thesis.md`：",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)

if __name__ == "__main__":
    main()
