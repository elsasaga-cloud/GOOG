# -*- coding: utf-8 -*-
"""美股筹码四维分析 v5.1-sandbox（改编自用户v5.1，沙箱直连yfinance版）
改动：删akshare/Playwright/CDP层（沙箱yfinance直连）；输出改为纯文本报告便于读取；核心算法/评分引擎/权重完全保留"""
import argparse, os, sys, warnings
from datetime import datetime, timedelta
import numpy as np, pandas as pd, yfinance as yf
warnings.filterwarnings("ignore")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTDIR = os.path.join(REPO_ROOT, "data")

W = {"vp":25,"avwap":30,"iv":25,"inst":15,"short_insider":5}

class Engine:
    def __init__(self): self.s={}; self.d={}; self.missing=[]
    def vp(self,g):
        if g is None: self.missing.append("vp"); return
        s = 90+min(10,(g-20)*.5) if g>20 else 70+(g-10)*2 if g>10 else 50+g*2 if g>0 else 30+(g+5)*4 if g>-5 else max(0,30+g*2)
        self.s["vp"]=round(min(100,max(0,s)),1); self.d["vp"]=f"安全垫{g:+.1f}%"
    def avwap(self,dp,days):
        if dp is None: self.missing.append("avwap"); return
        s = 90+min(10,(dp-15)*.67) if dp>15 else 80+(dp-10) if dp>10 else 70+(dp-5)*2 if dp>5 else 55+dp*3 if dp>0 else 30+(dp+5)*5 if dp>-5 else max(0,30+dp*2)
        disc = .85 if days>90 else .92 if days>60 else 1.0
        self.s["avwap"]=round(min(100,max(0,s*disc)),1); self.d["avwap"]=f"偏离{dp:+.1f}%(x{disc})"
    def iv(self,iv,cp):
        if iv is None: self.missing.append("iv"); return
        ivs = 95 if iv<20 else 90 if iv<25 else 75+(35-iv) if iv<35 else 40+(50-iv)*2.33 if iv<50 else max(0,40-(iv-50)*1.5)
        cp=cp or 1.0
        cps = 95 if cp>1.5 else 85 if cp>1.2 else 75 if cp>1.0 else 60 if cp>0.8 else 40 if cp>0.6 else 20
        self.s["iv"]=round(min(100,max(0,ivs*.5+cps*.5)),1); self.d["iv"]=f"IV={iv:.1f}% C/P={cp:.2f}"
    def inst(self,df):
        if df is None or df.empty: self.s["inst"]=60.0; self.d["inst"]="缺失,中性分"; return
        pc = next((c for c in ["pctHeld","% Out"] if c in df.columns),None)
        if pc:
            tp=df[pc].head(10).fillna(0).astype(float).sum(); tp*=100 if tp<1 else 1; tp=min(tp,100)
            s = 90 if tp>60 else 75 if tp>40 else 60 if tp>20 else 50
            self.s["inst"]=float(s); self.d["inst"]=f"Top10机构{tp:.1f}%"
        else: self.s["inst"]=65.0; self.d["inst"]="有数据无比例列"
    def short_ins(self,sp,chg,net):
        spv=sp or 5.0
        ss = 95 if spv<2 else 80 if spv<5 else 65 if spv<8 else 45 if spv<12 else 25
        if chg is not None:
            if chg<-10: ss=min(100,ss+10)
            elif chg>10: ss=max(0,ss-10)
        ins = 60 if net is None else 85 if net>0 else 65 if net>-5e5 else 45 if net>-2e6 else 25
        self.s["short_insider"]=round(ss*.5+ins*.5,1)
        self.d["short_insider"]=f"做空{sp if sp else 'N/A'}% 内部人净{'买' if (net or 0)>=0 else '卖'}${abs(net or 0):,.0f}"
    def total(self):
        if not self.s: return 0
        tw=sum(W[k] for k in self.s)
        return round(sum(self.s[k]*W[k] for k in self.s)/tw,1)
    def grade(self,t):
        return ("强势健康" if t>=85 else "温和偏多" if t>=70 else "中性观望" if t>=55 else "偏弱警惕" if t>=40 else "危险区间")

def vp_calc(h,cur,bins):
    h=h.copy(); h["mid"]=(h["High"]+h["Low"])/2
    vp=h.groupby(pd.cut(h["mid"],bins=bins),observed=True)["Volume"].sum().reset_index()
    vp.columns=["b","v"]; vp=vp.dropna().sort_values("b")
    vp["pct"]=vp["v"]/vp["v"].sum()*100; vp["hvn"]=vp["v"]>=vp["v"].quantile(.70)
    hvn=vp[vp["hvn"]]
    if hvn.empty: return None
    lo=min(r.left for r in hvn["b"]); hi=max(r.right for r in hvn["b"])
    gp=(cur-hi)/cur*100
    hw=hvn.apply(lambda r:r["b"].right-r["b"].left,axis=1).sum()
    return {"lo":float(lo),"hi":float(hi),"gap":gp,"pct":hvn["pct"].sum(),"conc":round(hw/cur*100,1),
            "rows":[(float(r["b"].left),float(r["b"].right),float(r["pct"]),bool(r["hvn"])) for _,r in vp.iterrows()]}

def find_anchor(h,days=90,th=.08):
    h=h.copy(); h["ret"]=h["Close"].pct_change()
    cut=pd.Timestamp.now()-pd.Timedelta(days=days)
    r=h[(h.index>=cut)&(h["ret"]>th)]
    if not r.empty: return r.index[-1],f"近{days}天大涨日(+{r['ret'].iloc[-1]*100:.1f}%)"
    a=h[h["ret"]>th]
    if not a.empty: return a.index[-1],f"全局大涨日(+{a['ret'].iloc[-1]*100:.1f}%)"
    return h.index[max(0,len(h)-180)],"180天起点"

def avwap_calc(h,dt):
    a=h[h.index>=dt].copy()
    if a.empty: return None
    tp=(a["High"]+a["Low"]+a["Close"])/3
    return float((tp*a["Volume"]).cumsum().iloc[-1]/a["Volume"].cumsum().iloc[-1])

def max_pain(calls,puts):
    try:
        toi=int(calls["openInterest"].fillna(0).sum()+puts["openInterest"].fillna(0).sum())
        strikes=sorted(set(calls["strike"].tolist()+puts["strike"].tolist()))
        pain={s:calls[calls["strike"]<s].apply(lambda r:(s-r["strike"])*(r.get("openInterest") or 0),axis=1).sum()
               +puts[puts["strike"]>s].apply(lambda r:(r["strike"]-s)*(r.get("openInterest") or 0),axis=1).sum() for s in strikes}
        return (max(pain,key=pain.get) if pain else None), toi>=5000, toi
    except: return None,False,0

def main():
    p=argparse.ArgumentParser(description="美股筹码四维分析 v5.1-sandbox")
    p.add_argument("--ticker",default="NVDA",help="标的代码")
    p.add_argument("--anchor",default=None,help="手动指定主锚点日期 YYYY-MM-DD（默认自动选近90天最后一个大涨日）")
    p.add_argument("--extra-anchors",default=None,
                   help="附加对照锚，格式 '标签:YYYY-MM-DD' 逗号分隔，例：'Q1财报锚:2026-05-20,年初锚:2026-01-02'")
    p.add_argument("--period",default=365,type=int,help="全局VP窗口天数")
    p.add_argument("--period2",default=60,type=int,help="近期VP窗口天数")
    p.add_argument("--compare",default="SPY,AMD,AVGO",help="RS对照标的，逗号分隔")
    p.add_argument("--outdir",default=None,help=f"输出目录（默认 {DEFAULT_OUTDIR}/<TICKER>/）")
    a=p.parse_args(); tk=a.ticker.upper()
    outdir=a.outdir or os.path.join(DEFAULT_OUTDIR,tk)
    os.makedirs(outdir,exist_ok=True)
    L=[]; P=L.append
    P(f"===== 美股筹码四维分析 v5.1-sandbox | {tk} | {datetime.now():%Y-%m-%d %H:%M} =====\n")
    t=yf.Ticker(tk)
    h=t.history(period=f"{a.period}d"); h.index=h.index.tz_localize(None)
    cur=float(h["Close"].iloc[-1]); P(f"当前价格: ${cur:.2f}  数据: {len(h)}根K线\n")
    eng=Engine()

    # 1 13F
    P("--- ① 13F机构持仓 ---")
    ih=t.institutional_holders; eng.inst(ih)
    if ih is not None:
        for _,r in ih.head(10).iterrows():
            pct=r.get("pctHeld",r.get("% Out",0)); pct=float(pct)*100 if pct and float(pct)<1 else float(pct or 0)
            P(f"  {str(r.get('Holder','?'))[:35]:38s} {int(r.get('Shares',0)):>15,d}  {str(r.get('Date Reported',''))[:10]}  {pct:.2f}%")
    P("")

    # 2 VP dual
    P(f"--- ② Volume Profile 双周期 ---")
    v1=vp_calc(h,cur,20)
    if v1:
        P(f"  [全局{a.period}天] 主力密集区 ${v1['lo']:.1f}-${v1['hi']:.1f} 占{v1['pct']:.1f}% 集中度{v1['conc']}% 安全垫{v1['gap']:+.1f}%")
        for lo,hi,pct,hvn in v1["rows"]:
            mark=" *HVN" if hvn else ""; cm=" <== 当前" if lo<=cur<=hi else ""
            P(f"    ${lo:7.1f}-{hi:7.1f} {'#'*int(pct*2):<36s}{pct:5.1f}%{mark}{cm}")
    hs=h[h.index>=h.index[-1]-pd.Timedelta(days=a.period2)]
    v2=vp_calc(hs,cur,10) if len(hs)>=10 else None
    if v2:
        P(f"  [近期{a.period2}天] 主力密集区 ${v2['lo']:.1f}-${v2['hi']:.1f} 占{v2['pct']:.1f}% 安全垫{v2['gap']:+.1f}%")
        for lo,hi,pct,hvn in v2["rows"]:
            mark=" *HVN" if hvn else ""; cm=" <== 当前" if lo<=cur<=hi else ""
            P(f"    ${lo:7.1f}-{hi:7.1f} {'#'*int(pct*1.2):<30s}{pct:5.1f}%{mark}{cm}")
    # 口径警告：暴涨股的全局VP主筹码区会停在低位，安全垫虚高，须改用近期窗口
    if v1 and v2 and v1["gap"]>35:
        P(f"  [!] 口径警告: 全局安全垫{v1['gap']:+.1f}%源自暴涨前的低位堆积,已失去支撑意义;"
          f"请以近期{a.period2}天口径{v2['gap']:+.1f}%(主筹码区${v2['lo']:.1f}-${v2['hi']:.1f})为准")
    eng.vp(v1["gap"] if v1 else None); P("")

    # 3 AVWAP dual anchor
    P("--- ③ Anchored VWAP ---")
    if a.anchor: dt1,rs1=pd.Timestamp(a.anchor),"手动"
    else: dt1,rs1=find_anchor(h)
    av1=avwap_calc(h,dt1); days1=(pd.Timestamp.now()-dt1).days
    dp1=(cur-av1)/av1*100 if av1 else None
    P(f"  锚1 {dt1:%Y-%m-%d}({days1}天前,{rs1}): AVWAP=${av1:.2f} 偏离{dp1:+.1f}%")
    # 附加对照锚：优先用 --extra-anchors，否则按数据自动推导（不硬编码日期）
    if a.extra_anchors:
        extra=[]
        for item in a.extra_anchors.split(","):
            if ":" in item:
                nm,ds=item.rsplit(":",1); extra.append((nm.strip(),ds.strip()))
    else:
        extra=[("52周低点锚",f"{h['Low'].idxmin():%Y-%m-%d}"),
               ("年初锚",f"{h.index[-1].year}-01-02"),
               ("半年锚",f"{h.index[-1]-pd.Timedelta(days=182):%Y-%m-%d}")]
    for name,ds in extra:
        try:
            av=avwap_calc(h,pd.Timestamp(ds))
            if av: P(f"  {name} {ds}: AVWAP=${av:.2f} 偏离{(cur-av)/av*100:+.1f}%")
        except Exception: pass
    eng.avwap(dp1,days1); P("")

    # 4 IV + MaxPain
    P("--- ④ IV结构 + Max Pain ---")
    iv_first=None
    try:
        exps=list(t.options); tgt=[("近月",exps[0])]+([("次月",exps[1])] if len(exps)>1 else [])+([("季度",exps[3])] if len(exps)>3 else [])
        for lb,exp in tgt:
            ch=t.option_chain(exp); c,pu=ch.calls.copy(),ch.puts.copy()
            c["d"]=(c["strike"]-cur).abs(); pu["d"]=(pu["strike"]-cur).abs()
            civ=c.nsmallest(3,"d")["impliedVolatility"].mean()*100; piv=pu.nsmallest(3,"d")["impliedVolatility"].mean()*100
            atm=(civ+piv)/2; cp=c["volume"].fillna(0).sum()/(pu["volume"].fillna(0).sum() or 1)
            mp,rel,toi=max_pain(c,pu)
            P(f"  {lb} {exp}: ATM_IV={atm:.1f}% CallIV={civ:.1f} PutIV={piv:.1f} 偏斜{piv-civ:+.1f} C/P={cp:.2f} C_OI={int(c['openInterest'].fillna(0).sum()):,} P_OI={int(pu['openInterest'].fillna(0).sum()):,}"+(f" MaxPain=${mp:.0f}({(cur-mp)/mp*100:+.1f}%){'' if rel else ' [OI不足]'}" if mp else ""))
            if iv_first is None: iv_first=(atm,cp)
    except Exception as e: P(f"  期权失败:{repr(e)[:60]}")
    # HV trend
    rets=h["Close"].pct_change().dropna()
    hv14=float(rets.iloc[-14:].std()*252**.5*100); hv7=float(rets.iloc[-7:].std()*252**.5*100)
    tr="上升" if hv7/hv14>1.08 else "下降" if hv7/hv14<0.92 else "平稳"
    P(f"  HV趋势: 14日{hv14:.1f}% -> 7日{hv7:.1f}% [{tr}]")
    if iv_first: eng.iv(*iv_first)
    else: eng.missing.append("iv")
    P("")

    # 5 short + insider (v5.1 filter)
    P("--- ⑤ 做空+内部人 ---")
    sp=chg=None; net=None
    try:
        info=t.info; spv=info.get("shortPercentOfFloat"); sp=round(spv*100,2) if spv else None
        ss,ssp=info.get("sharesShort"),info.get("sharesShortPriorMonth")
        if ss and ssp: chg=round((ss-ssp)/ssp*100,2)
        P(f"  做空比例: {sp}%  较上月{chg:+.1f}%  回补天数{info.get('shortRatio','N/A')}")
    except Exception as e: P(f"  做空数据失败:{repr(e)[:50]}")
    try:
        ins=t.insider_transactions
        if ins is not None and not ins.empty:
            tc=next((c for c in ["Transaction","Text","Type"] if c in ins.columns),None)
            f=ins.copy()
            if tc:
                skip=["gift","option exercise","tax","exercise","conversion","award"]
                f=f[f[tc].astype(str).str.lower().apply(lambda s:not any(k in s for k in skip) and any(k in s for k in ["sale","sell","purchase","buy"]))]
            dc=next((c for c in ["Start Date","Date"] if c in f.columns),None)
            if dc:
                f[dc]=pd.to_datetime(f[dc],errors="coerce")
                rec=f[f[dc]>=pd.Timestamp.now()-pd.Timedelta(days=30)]
            else: rec=f.head(10)
            if "Value" in rec.columns and not rec.empty:
                vals=pd.to_numeric(rec["Value"],errors="coerce").fillna(0)
                if tc is not None and (vals>=0).all():
                    signs=rec[tc].astype(str).str.lower().apply(lambda s:-1 if ("sale" in s or "sell" in s) else 1)
                    vals=vals*signs
                net=float(vals.sum())
            P(f"  内部人近30天(公开市场): 净{'买入' if (net or 0)>=0 else '卖出'} ${abs(net or 0):,.0f}  {len(rec)}笔")
            for _,r in rec.head(6).iterrows():
                P(f"    {str(r.get('Insider','?'))[:25]:28s} {str(r.get(tc,''))[:28]:30s} {str(r.get('Value','')):>15s}")
    except Exception as e: P(f"  内部人失败:{repr(e)[:50]}")
    eng.short_ins(sp,chg,net); P("")

    # 6 RS
    P("--- ⑥ 相对强弱(30天) ---")
    for comp in [c.strip().upper() for c in a.compare.split(",") if c.strip()]:
        try:
            h2=yf.Ticker(comp).history(period="35d"); h2.index=h2.index.tz_localize(None)
            cb=pd.DataFrame({"t":h["Close"],"c":h2["Close"]}).dropna()
            tr_=(cb["t"].iloc[-1]/cb["t"].iloc[0]-1)*100; cr=(cb["c"].iloc[-1]/cb["c"].iloc[0]-1)*100
            mid=len(cb)//2
            rh1=(cb["t"].iloc[mid]/cb["t"].iloc[0]-1)-(cb["c"].iloc[mid]/cb["c"].iloc[0]-1)
            rh2=(cb["t"].iloc[-1]/cb["t"].iloc[mid]-1)-(cb["c"].iloc[-1]/cb["c"].iloc[mid]-1)
            P(f"  vs {comp:6s}: {tk}{tr_:+.1f}% vs {cr:+.1f}%  超额{tr_-cr:+.1f}%  RS{'上升' if rh2>rh1 else '下降'}")
        except Exception as e: P(f"  vs {comp}: 失败")
    P("")

    # 7 earnings
    P("--- ⑦ 财报日历 ---")
    try:
        cal=t.calendar; ed=cal.get("Earnings Date",[]) if isinstance(cal,dict) else []
        if ed:
            d0=pd.to_datetime(ed[0]); dt_=(d0.to_pydatetime().replace(tzinfo=None)-datetime.now()).days
            P(f"  预估财报: {d0:%Y-%m-%d}  距今{dt_}天" + ("  [!!极近,IV升,谨慎开新仓]" if 0<=dt_<=7 else "  [财报窗口期]" if dt_<=21 else ""))
    except Exception as e: P(f"  失败:{repr(e)[:50]}")
    P("")

    # 8 summary
    tot=eng.total(); gr=eng.grade(tot)
    P("--- ⑧ 综合评分 ---")
    nm={"vp":"VolumeProfile","avwap":"AnchoredVWAP","iv":"IV结构","inst":"机构13F","short_insider":"做空+内部人"}
    for k,w in W.items():
        P(f"  {nm[k]:16s} 权重{w:2d}%  得分{eng.s.get(k,0):5.1f}  {eng.d.get(k,'缺失')}")
    P(f"\n  ====== 综合评分: {tot}/100  [{gr}] ======\n")
    # exit conditions
    warns=[]
    if av1 and cur<av1: warns.append("AVWAP跌破")
    if v1 and cur<v1["lo"]: warns.append("VP下沿失守")
    if iv_first and iv_first[0]>50 and iv_first[1]<0.8: warns.append("IV+CP预警")
    P(f"  离场条件触发: {len(warns)}/3  {warns if warns else '(全部安全)'}")
    txt="\n".join(L)
    print(txt)
    fp=os.path.join(outdir,f"chip_scan_{datetime.now():%Y%m%d}.txt")
    with open(fp,"w",encoding="utf-8") as f: f.write(txt)
    print(f"\n[已存档: {fp}]")

if __name__=="__main__": main()
