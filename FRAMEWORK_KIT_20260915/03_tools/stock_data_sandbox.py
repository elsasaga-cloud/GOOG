# -*- coding: utf-8 -*-
"""美股倒抓筹码数据 v5-sandbox（改编自用户v5，yfinance直连版）
保留：47列结构 / compute_chip三角分布筹码算法 / 日K250窗口 / 周K全历史+换手衰减0.30+上限0.02+半衰期100周 / merge_asof周线回填
改动：数据源仅yfinance；输出CSV到 data/<TICKER>/；换手率=成交量/流通股本"""
import argparse, os, sys, warnings
import numpy as np, pandas as pd, yfinance as yf
warnings.filterwarnings("ignore")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTDIR = os.path.join(REPO_ROOT, "data")

CHIP_K=1.0; WK_K=0.30; WK_MAXT=0.02; WK_HL=100; RANGE_D=250; NBINS=2000
DMA=[5,15,30,45,60,75,90,105,120,150]; WMA=[5,10,15,20,30,40,50,60,70,80,90]
CHIP_COLS=["获利比例","平均成本","90%成本-低","90%成本-高","90%集中度","70%成本-低","70%成本-高","70%集中度"]

def compute_chip(df,range_days=RANGE_D,decay_k=CHIP_K,max_t=None,half_life=None):
    n=len(df)
    for c in CHIP_COLS: df[c]=np.nan
    if n<10: return df
    H=df["最高"].fillna(0).values.astype(float); L=df["最低"].fillna(0).values.astype(float)
    C=df["收盘"].fillna(0).values.astype(float)
    V=df["_vwap"].fillna(0).values.astype(float) if "_vwap" in df else (H+L+C)/3
    cap=max_t if max_t else 1.0
    T=np.clip(df["换手率"].fillna(0).values.astype(float)/100*decay_k,0,cap)
    if not (H>0).any(): return df
    pmin=L[L>0].min()*.9 if (L>0).any() else C.min()*.9; pmax=H.max()*1.1
    grid=np.linspace(pmin,pmax,NBINS)
    tri=np.zeros((n,NBINS))
    for j in range(n):
        lo,hi,pk=L[j],H[j],min(max(V[j],L[j]),H[j])
        if hi<=0 or lo<=0 or hi<=lo:
            tri[j,int(np.clip(np.searchsorted(grid,C[j]),0,NBINS-1))]=1; continue
        w=np.zeros(NBINS); lm=(grid>=lo)&(grid<=pk); rm=(grid>pk)&(grid<=hi)
        w[lm]=(grid[lm]-lo)/(pk-lo) if pk>lo else 1; w[rm]=(hi-grid[rm])/(hi-pk) if hi>pk else 1
        s=w.sum(); tri[j]=w/s if s>0 else w
    td=0.5**(1/half_life) if half_life else 1.0
    out={c:np.full(n,np.nan) for c in CHIP_COLS}
    # incremental chip for full-history mode; windowed re-calc otherwise
    if range_days is None:
        chip=np.zeros(NBINS)
        for i in range(n):
            chip=chip*td*(1-T[i])+tri[i]*T[i]
            if C[i]<=0: continue
            s=chip.sum()
            if s<=0: continue
            ch=chip/s; _fill(out,i,ch,grid,C[i])
    else:
        for i in range(n):
            if C[i]<=0: continue
            chip=np.zeros(NBINS)
            for j in range(max(0,i-range_days+1),i+1):
                chip=chip*td*(1-T[j])+tri[j]*T[j]
            s=chip.sum()
            if s<=0: continue
            _fill(out,i,chip/s,grid,C[i])
    for c in CHIP_COLS: df[c]=np.round(out[c],2)
    return df

def _fill(out,i,ch,grid,c):
    out["获利比例"][i]=ch[grid<c].sum()*100; out["平均成本"][i]=np.dot(grid,ch)
    cdf=np.cumsum(ch)
    for pct,pre in [(0.90,"90%"),(0.70,"70%")]:
        lq=(1-pct)/2; li=int(np.clip(np.searchsorted(cdf,lq),0,NBINS-1)); hi_=int(np.clip(np.searchsorted(cdf,1-lq),0,NBINS-1))
        lp,hp=grid[li],grid[hi_]
        out[f"{pre}成本-低"][i]=lp; out[f"{pre}成本-高"][i]=hp
        out[f"{pre}集中度"][i]=(hp-lp)/(hp+lp)*100 if hp+lp>0 else 0

def main():
    p=argparse.ArgumentParser(description="美股倒抓筹码数据 v5-sandbox（47列）")
    p.add_argument("ticker",nargs="?",default=None,help="标的代码（也可用 --ticker）")
    p.add_argument("n_days",nargs="?",type=int,default=None,help="输出行数（也可用 --days）")
    p.add_argument("--ticker",dest="ticker_opt",default=None)
    p.add_argument("--days",dest="days_opt",type=int,default=None)
    p.add_argument("--outdir",default=None,help=f"输出目录（默认 {DEFAULT_OUTDIR}/<TICKER>/）")
    a=p.parse_args()
    tk=(a.ticker_opt or a.ticker or "NVDA").upper()
    n_days=a.days_opt or a.n_days or 1700
    outdir=a.outdir or os.path.join(DEFAULT_OUTDIR,tk)
    os.makedirs(outdir,exist_ok=True)
    t=yf.Ticker(tk)
    h=t.history(period="max",auto_adjust=True); h.index=h.index.tz_localize(None)
    h=h.tail(n_days+1300)
    try: fs=float(t.fast_info["shares"] or 0)
    except Exception: fs=0
    if fs<=0:
        print("[警告] 未取到流通股本，换手率退化为『成交量/60日均量』近似值，筹码绝对水平仅供参考")
    df=pd.DataFrame({"日期":h.index.strftime("%Y-%m-%d"),"开盘":h["Open"].values,"收盘":h["Close"].values,
                     "最高":h["High"].values,"最低":h["Low"].values,"成交量":h["Volume"].values}).reset_index(drop=True)
    df["成交额"]=(df["收盘"]*df["成交量"]).round(2)
    df["_vwap"]=np.where(df["成交量"]>0,df["成交额"]/df["成交量"],df["收盘"])
    p5=df["成交量"].shift(1).rolling(5).mean().replace(0,np.nan)
    df["量比"]=(df["成交量"]/p5).fillna(0).round(2)
    pc=df["收盘"].shift(1).replace(0,np.nan)
    df["振幅"]=((df["最高"]-df["最低"])/pc*100).round(2).fillna(0)
    c5=df["收盘"].shift(5).replace(0,np.nan)
    df["5日涨幅"]=((df["收盘"]-c5)/c5*100).round(2).fillna(0)
    df["换手率"]=(df["成交量"]/fs*100).round(4) if fs>0 else (df["成交量"]/df["成交量"].rolling(60).mean()).fillna(0).clip(0,5).round(4)
    for m in DMA: df[f"日MA{m}"]=df["收盘"].rolling(m).mean()
    print(f"{tk}: {len(df)}日K, 流通股{fs/1e9:.1f}B, 计算日K筹码(250窗口)...")
    df=compute_chip(df)
    # weekly aggregate
    d=df[["日期","开盘","收盘","最高","最低","成交量","成交额","换手率"]].copy()
    d["_dt"]=pd.to_datetime(d["日期"]); d=d.set_index("_dt")
    ag=d.resample("W-FRI",label="right",closed="right").agg({"开盘":"first","收盘":"last","最高":"max","最低":"min","成交量":"sum","成交额":"sum","换手率":"sum"}).dropna(subset=["收盘"]).reset_index()
    if len(ag)>1: ag=ag.iloc[1:].reset_index(drop=True)
    wk=pd.DataFrame({"日期":ag["_dt"].dt.strftime("%Y-%m-%d"),"开盘":ag["开盘"],"收盘":ag["收盘"],"最高":ag["最高"],"最低":ag["最低"],"成交量":ag["成交量"].fillna(0),"成交额":ag["成交额"].fillna(0),"换手率":ag["换手率"].fillna(0)})
    wk["_vwap"]=np.where(wk["成交量"]>0,wk["成交额"]/wk["成交量"],wk["收盘"])
    for m in WMA: wk[f"周MA{m}"]=wk["收盘"].rolling(m).mean()
    print(f"周K {len(wk)}根, 计算周K筹码(全历史+半衰期100周)...")
    wc=wk[["日期","最高","最低","收盘","换手率","_vwap"]].copy()
    wc=compute_chip(wc,range_days=None,decay_k=WK_K,max_t=WK_MAXT,half_life=WK_HL)
    wk["周K筹码平均成本"]=wc["平均成本"]
    bf=wk[["日期"]+[f"周MA{m}" for m in WMA]+["周K筹码平均成本"]].copy()
    bf["_w"]=pd.to_datetime(bf["日期"]); bf=bf.sort_values("_w")
    df["_d"]=pd.to_datetime(df["日期"]); df=df.sort_values("_d")
    df=pd.merge_asof(df,bf.drop(columns=["日期"]),left_on="_d",right_on="_w",direction="backward").drop(columns=["_d","_w"])
    df["日K筹码平均成本"]=df["平均成本"]
    cl=df["收盘"]; dc=df["日K筹码平均成本"]; wcost=df["周K筹码平均成本"]
    df["日K筹码偏离率"]=np.where(dc.notna()&(dc!=0),(cl-dc)/dc*100,np.nan)
    df["周K筹码偏离率"]=np.where(wcost.notna()&(wcost!=0),(cl-wcost)/wcost*100,np.nan)
    wmx=df[[f"周MA{m}" for m in WMA]].max(axis=1)
    df["收盘价相对周MAX位置"]=np.where(wmx.notna()&(wmx!=0),(cl-wmx)/wmx*100,np.nan)
    df["日周筹码成本差值"]=np.where(dc.notna()&wcost.notna(),dc-wcost,np.nan)
    df["日周筹码成本差值率"]=np.where(dc.notna()&wcost.notna()&(wcost!=0),(dc-wcost)/wcost*100,np.nan)
    cols=(["日期","开盘","收盘","最高","最低","5日涨幅","振幅","换手率","量比","成交量","成交额"]
          +[f"日MA{m}" for m in DMA]+[f"周MA{m}" for m in WMA]+CHIP_COLS
          +["日K筹码平均成本","周K筹码平均成本","日K筹码偏离率","周K筹码偏离率","收盘价相对周MAX位置","日周筹码成本差值","日周筹码成本差值率"])
    out=df[[c for c in cols if c in df.columns]].tail(n_days).round(2)
    fp=os.path.join(outdir,f"{tk}_N{n_days}_chipdata.csv")
    out.to_csv(fp,index=False,encoding="utf-8-sig")
    last=out.iloc[-1]
    print(f"\n[已保存 {fp}] {len(out)}行47列")
    print(f"最新({last['日期']}): 收盘${last['收盘']} 获利盘{last['获利比例']}% 日K成本${last['日K筹码平均成本']} 周K成本${last['周K筹码平均成本']} 日K偏离{last['日K筹码偏离率']}% 周K偏离{last['周K筹码偏离率']}%")
    print(f"90%成本带: ${last['90%成本-低']}-${last['90%成本-高']} 集中度{last['90%集中度']}")
    print(f"70%成本带: ${last['70%成本-低']}-${last['70%成本-高']} 集中度{last['70%集中度']}")

if __name__=="__main__": main()
