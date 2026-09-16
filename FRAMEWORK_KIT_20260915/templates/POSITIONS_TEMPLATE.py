# -*- coding: utf-8 -*-
"""持仓单一事实源模板（改持仓只改这里；引擎脚本共同读取）"""
TICKER = "TICKER"
TOTAL_SHARES = 0
AVG_COST = 0.0            # 摊薄成本（用户券商口径；审计链写注释）
OPEN_OPTIONS = []          # short/long 腿；每腿注明开仓日/K/权利金/张数
def cc_covered_used():
    return sum(o["qty"]*100 for o in OPEN_OPTIONS if o["side"]=="short" and o["type"]=="CALL")
def cc_shares_available():
    return max(0, TOTAL_SHARES - cc_covered_used())
CHANGELOG = ["✓ 初始化模板"]
