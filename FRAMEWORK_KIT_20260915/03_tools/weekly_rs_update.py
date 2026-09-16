# -*- coding: utf-8 -*-
"""RS 周线例行更新（#57，约束20 落地）
用法（每周五收盘后）:
  1. 追加本周日收盘到 data/HPE/HPE_daily_ohlcv_20260909.csv（或其后续命名文件，改下方 HPE_CSV）
  2. 同样追加 SOXX/SPY 日线（SOXX_CSV / SPY_CSV）
  3. python3 scripts/weekly_rs_update.py
输出: data/HPE/rs_relative_strength_weekly_latest.csv（W-FRI 周线 + RS 归一100）
判据见 reports/HPE/2026-09-10/rs_line_study_20260910.md（判词16: RS<100 或连续4周下行且<95=降温）
"""
import csv, io
from datetime import datetime, timedelta
HPE_CSV='data/HPE/HPE_daily_ohlcv_20260909.csv'
SOXX_CSV='data/HPE/SOXX_daily_close_20260910.csv'
SPY_CSV='data/HPE/SPY_daily_close_20260910.csv'
OUT='data/HPE/rs_relative_strength_weekly_latest.csv'
def load(path, dcol, ccol):
    return {r[dcol]: float(r[ccol]) for r in csv.DictReader(io.open(path,encoding='utf-8-sig'))}
hpe=load(HPE_CSV,'date','close'); soxx=load(SOXX_CSV,'date','close'); spy=load(SPY_CSV,'date','close')
def wk(ds):
    dt=datetime.strptime(ds,'%Y-%m-%d'); return (dt-timedelta(days=dt.weekday())).strftime('%Y-%m-%d')
def weekly(d):  # W-FRI: 每周取最后可得收盘
    out={}
    for ds,c in sorted(d.items()): out[wk(ds)]=c
    return out
hw,sw,pw=weekly(hpe),weekly(soxx),weekly(pw if False else spy)
keys=sorted(set(hw)&set(sw)&set(pw))
rs_s=[hw[k]/sw[k]/(hw[keys[0]]/sw[keys[0]])*100 for k in keys]
rs_p=[hw[k]/pw[k]/(hw[keys[0]]/pw[keys[0]])*100 for k in keys]
io.open(OUT,'w',encoding='utf-8').write('week_monday,HPE,SOXX,SPY,RS_vs_SOXX,RS_vs_SPY\n'+
  '\n'.join(f'{k},{hw[k]:.2f},{sw[k]:.2f},{pw[k]:.2f},{a:.1f},{b:.1f}' for k,a,b in zip(keys,rs_s,rs_p))+'\n')
print(f"OK {len(keys)} 周 → {OUT} | RS_SOXX={rs_s[-1]:.1f} RS_SPY={rs_p[-1]:.1f} | 降温判据: {'触发⚠️' if (rs_s[-1]<100 or (len(rs_s)>4 and all(rs_s[i]>rs_s[i+1] for i in range(-4,0)) and rs_s[-1]<95)) else '未触发'}")
