import pandas as pd, numpy as np, sys, os, json, math
from pathlib import Path
from datetime import datetime, timedelta

_ROOT=Path(__file__).resolve().parents[2]
csv_path=str(_ROOT/"GOOG N=3000.csv")
# read with utf-8-sig, handle footer
df=pd.read_csv(csv_path, encoding='utf-8-sig', dtype=str)
# Filter valid date rows: 日期 like YYYY-MM-DD
import re
pattern=re.compile(r'^\d{4}-\d{2}-\d{2}$')
mask=df['日期'].astype(str).str.match(pattern)
df=df[mask].copy()
# Convert numeric cols
num_cols=[c for c in df.columns if c!='日期']
for c in num_cols:
    df[c]=pd.to_numeric(df[c].astype(str).str.replace(',',''), errors='coerce')
df['日期']=pd.to_datetime(df['日期'])
df=df.sort_values('日期').reset_index(drop=True)
print(f"Loaded {len(df)} valid rows {df['日期'].iloc[0].date()} -> {df['日期'].iloc[-1].date()}")

# Last row
last=df.iloc[-1]
P=float(last['收盘'])
print(f"Last close {P} date {last['日期'].date()}")

# 52W high/low (252 trading days)
last252=df.tail(252)
high52=float(last252['最高'].max())
low52=float(last252['最低'].min())
print(f"52W high {high52} low {low52} dist to high {(P/high52-1)*100:.2f}%")

# ATH
ath=float(df['最高'].max())
ath_date=df.loc[df['最高'].idxmax(),'日期'].date()
print(f"ATH {ath} date {ath_date} dist {(P/ath-1)*100:.2f}%")

# MA list daily
MA_DAILY=[5,15,30,45,60,75,90,105,120,150]
MA_WEEKLY=[5,10,15,20,30,40,50,60,70,80,90]
ma_vals={}
for n in MA_DAILY:
    col=f"日MA{n}"
    if col in df.columns:
        ma_vals[f"MA{n}"]=float(df[col].iloc[-1]) if pd.notna(df[col].iloc[-1]) else None
for n in MA_WEEKLY:
    col=f"周MA{n}"
    if col in df.columns:
        ma_vals[f"WMA{n}"]=float(df[col].iloc[-1]) if pd.notna(df[col].iloc[-1]) else None

# Gap calc
gaps={}
for k,v in ma_vals.items():
    if v and v!=0:
        gaps[k]= (P - v)/v*100

# State detection logic from quant_core.py
def detect_state(df):
    if len(df)<150:
        return ("数据不足","K线不足150根",None)
    P=df['收盘'].iloc[-1]
    def get_ma(n):
        col=f"日MA{n}"
        return df[col].iloc[-1] if col in df.columns else None
    mas={n:get_ma(n) for n in MA_DAILY}
    ma5, ma15, ma30, ma45 = mas.get(5), mas.get(15), mas.get(30), mas.get(45)
    ma60, ma75, ma90, ma105 = mas.get(60), mas.get(75), mas.get(90), mas.get(105)
    if any(v is None or pd.isna(v) for v in [ma30, ma60, ma90]):
        return ("数据不足","核心均线缺失",None)
    if all(v is not None and not pd.isna(v) for v in [ma5, ma15, ma45, ma75, ma105]):
        if P > ma5 > ma15 > ma30 > ma45 > ma60 > ma75 > ma90 > ma105:
            return ("3+","完整多头排列",True)
    if P > ma30 > ma60 > ma90:
        return ("3","结构性多头 P>MA30>MA60>MA90",True)
    if P < ma30 < ma60 < ma90:
        return ("0","结构性空头",False)
    valid=[v for v in [ma15, ma30, ma45, ma60] if v is not None and not pd.isna(v)]
    if valid and P>0 and max(abs(P-m)/P for m in valid)<0.03:
        return ("粘合","均线高度收敛，方向待选择",None)
    return ("X","均线结构混乱或横盘",None)

state, desc, gate = detect_state(df)
print(f"State {state} {desc} gate {gate}")

# State history
def analyze_state_history(df):
    if len(df)<250:
        return {"错误":"数据不足250行"}
    states=[detect_state(df.iloc[:i+1])[0] for i in range(150, len(df))]
    # segments
    segs=[]
    cur=states[0]; cnt=1
    for s in states[1:]:
        if s==cur:
            cnt+=1
        else:
            segs.append((cur,cnt)); cur,cnt=s,1
    segs.append((cur,cnt))
    s3=[l for s,l in segs if s in ("3","3+")]
    cur_cont=segs[-1][1] if segs and segs[-1][0] in ("3","3+") else 0
    hist=s3[:-1] if (cur_cont and s3) else s3
    if not hist and cur_cont==0:
        return {"平均持续天数":0,"最长持续天数":0,"历史进入次数":0,"当前持续天数":0,"segments":segs}
    return {"平均持续天数":int(np.mean(hist)) if hist else cur_cont,
            "最长持续天数":int(max(hist)) if hist else cur_cont,
            "历史进入次数":len(hist)+(1 if cur_cont else 0),
            "当前持续天数":cur_cont,
            "segments":segs}

state_hist=analyze_state_history(df)

# Chip analysis
def analyze_chips(df):
    last=df.iloc[-1]
    cp=float(last['收盘'])
    def sf(v,d=0):
        return d if pd.isna(v) else float(v)
    r={}
    if '获利比例' in df.columns:
        r['获利比例']=round(sf(last.get('获利比例')),2)
    if '平均成本' in df.columns:
        r['平均成本']=round(sf(last.get('平均成本')),2)
        if r['平均成本']>0:
            r['价格vs成本']=round((cp/r['平均成本']-1)*100,2)
    for k in ["日K筹码平均成本","周K筹码平均成本","日K筹码偏离率","周K筹码偏离率","90%集中度","70%集中度","收盘价相对周MAX位置","日周筹码成本差值","日周筹码成本差值率"]:
        if k in df.columns and pd.notna(last.get(k)):
            r[k]=round(float(last.get(k)),2)
    # 70%区间
    lo70=sf(last.get('70%成本-低')); hi70=sf(last.get('70%成本-高'))
    lo90=sf(last.get('90%成本-低')); hi90=sf(last.get('90%成本-高'))
    if hi70>lo70:
        r['70%成本区间']=f"${lo70:.2f}~${hi70:.2f}"
        r['70%成本-低']=lo70; r['70%成本-高']=hi70
        r['70%筹码中枢']=(lo70+hi70)/2
    if hi90>lo90:
        r['90%成本区间']=f"${lo90:.2f}~${hi90:.2f}"
        r['90%成本-低']=lo90; r['90%成本-高']=hi90
        if cp<hi90:
            r['解套盘压力']=min(round((hi90-cp)/(hi90-lo90)*90,1),90)
    # 周K成本稳定性
    if "周K筹码平均成本" in df.columns:
        s=df["周K筹码平均成本"].dropna().tail(100)
        if len(s)>=20 and s.mean()!=0:
            amp=(s.max()-s.min())/s.mean()*100
            slope=(s.iloc[-1]-s.iloc[0])/s.iloc[0]*100 if s.iloc[0]!=0 else 999
            r['周K成本振幅占比']=round(amp,2)
            r['周K成本斜率占比']=round(slope,2)
            if amp<=22 and slope<=25:
                r['筹码稳定性判断']="周K成本相对锁死，符合机构锁仓特征"
            elif amp>40 or slope>40:
                r['筹码稳定性判断']="周K成本波动较大，警惕假锁仓/派发"
            else:
                r['筹码稳定性判断']="周K成本中等稳定"
    return r

chip=analyze_chips(df)

# Volume Profile dual cycle
def vp_analysis(df, period_days, bins=20):
    hist=df.tail(period_days) if len(df)>=period_days else df
    cur=float(df['收盘'].iloc[-1])
    hist=hist.copy()
    hist['mid']=(hist['最高']+hist['最低'])/2
    # cut
    try:
        hist['bin']=pd.cut(hist['mid'], bins=bins)
        vp=hist.groupby('bin', observed=True)['成交量'].sum().reset_index()
        vp['pct']=vp['成交量']/vp['成交量'].sum()*100
        # HVN threshold 70% quantile
        q=vp['成交量'].quantile(0.70)
        vp['is_hvn']=vp['成交量']>=q
        hvn=vp[vp['is_hvn']]
        if not hvn.empty:
            # get price range
            lows=[interval.left for interval in hvn['bin']]
            highs=[interval.right for interval in hvn['bin']]
            lo=min(lows); hi=max(highs)
            gap=(cur-hi)/cur*100
            return {"hvn_lo":lo,"hvn_hi":hi,"gap_pct":gap,"concentration":float(hvn['pct'].sum()),"vp":vp,"current":cur,"period":period_days}
    except Exception as e:
        print(f"VP error {e}")
    return None

vp_long=vp_analysis(df, 365, 20)
vp_short=vp_analysis(df, 60, 10)

# AVWAP anchor: last >8% rise in last 90 days
df['prev_close']=df['收盘'].shift(1)
df['daily_ret']=df['收盘']/df['prev_close']-1
last90=df.tail(90)
big=last90[last90['daily_ret']>0.08]
if not big.empty:
    anchor_date=big.iloc[-1]['日期']
    anchor_price=float(big.iloc[-1]['收盘'])
    anchor_reason=f"近90天最近大涨日 +{big.iloc[-1]['daily_ret']*100:.1f}%"
else:
    # fallback: last 180 days start
    anchor_date=df.iloc[max(0,len(df)-180)]['日期']
    anchor_price=float(df.iloc[max(0,len(df)-180)]['收盘'])
    anchor_reason="未找到>8%大涨日，使用近180天起点"

# calc AVWAP from anchor
after=df[df['日期']>=anchor_date].copy()
if not after.empty:
    after['tp']=(after['最高']+after['最低']+after['收盘'])/3
    after['tp_vol']=after['tp']*after['成交量']
    after['cum_tpv']=after['tp_vol'].cumsum()
    after['cum_vol']=after['成交量'].cumsum()
    after['avwap']=after['cum_tpv']/after['cum_vol']
    avwap_last=float(after['avwap'].iloc[-1])
    diff_pct=(P-avwap_last)/avwap_last*100
    days_ago=(df['日期'].iloc[-1]-anchor_date).days
else:
    avwap_last=None; diff_pct=None; days_ago=None

# ATR14 Wilder
def calc_atr(df,n=14):
    h=df['最高']; l=df['最低']; c=df['收盘']
    pc=c.shift(1)
    tr=pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()], axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/n, adjust=False).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else None

atr=calc_atr(df,14)

# Amplitude stats
df['振幅_calc']=(df['最高']-df['最低'])/df['收盘'].shift(1)*100
amp_all=df['振幅'].dropna()
amp_recent=df.tail(252)['振幅'].dropna()
amp_stats={
    "all_mean":float(amp_all.mean()),
    "all_median":float(amp_all.median()),
    "all_p75":float(amp_all.quantile(0.75)),
    "all_p90":float(amp_all.quantile(0.90)),
    "all_max":float(amp_all.max()),
    "recent_mean":float(amp_recent.mean()),
    "recent_median":float(amp_recent.median()),
    "recent_p75":float(amp_recent.quantile(0.75)),
    "recent_p90":float(amp_recent.quantile(0.90)),
    "recent_max":float(amp_recent.max()),
}

# Turnover stats
turn_all=df['换手率'].dropna()
turn_recent=df.tail(252)['换手率'].dropna()

# GBM prediction
def predict_gbm(df,days):
    if len(df)<100:
        return {"错误":"数据不足"}
    cp=df['收盘'].iloc[-1]
    look=min(len(df),252)
    rets=np.log(df['收盘'].tail(look)/df['收盘'].tail(look).shift(1)).dropna()
    rets=rets[abs(rets)<0.20]
    if len(rets)<20:
        return {"错误":"有效数据不足"}
    mu=rets.mean()*252
    sig=rets.std()*np.sqrt(252)
    n=max(int(days),1)
    sims=10000
    paths=np.zeros((sims,n))
    paths[:,0]=cp
    for t in range(1,n):
        Z=np.random.standard_normal(sims)
        paths[:,t]=paths[:,t-1]*(1+(mu-0.5*sig**2)/252 + sig/np.sqrt(252)*Z)
    fin=paths[:,-1]
    p10,p50,p90=np.percentile(fin,[10,50,90])
    up=(p50/cp-1)*100
    dn=(p10/cp-1)*100
    return {"days":days,"current":round(float(cp),2),"p10":round(float(p10),2),"p50":round(float(p50),2),"p90":round(float(p90),2),"up":round(float(up),2),"down":round(float(dn),2),"mu":round(float(mu*100),2),"sigma":round(float(sig*100),2)}

gbm5=predict_gbm(df,5)
gbm10=predict_gbm(df,10)
gbm30=predict_gbm(df,30)

# Direction signal simplified
votes=[]
reasons=[]
if state in ("3","3+"):
    votes.append(1); reasons.append(f"State={state}多头放行(+1)")
elif state=="0":
    votes.append(-1); reasons.append("State=0空头(-1)")
else:
    votes.append(0); reasons.append(f"State={state}方向不明(0)")

dk=chip.get("日K筹码偏离率")
if dk is not None:
    if dk>35:
        votes.append(-0.5); reasons.append(f"日K筹码偏离{dk:+.1f}%偏热(-0.5)")
    elif dk<-10:
        votes.append(0.5); reasons.append(f"日K筹码偏离{dk:+.1f}%低位(+0.5)")
    else:
        reasons.append(f"日K筹码偏离{dk:+.1f}%中性")

# 70% cost position
if chip.get("70%成本-高") and chip.get("70%成本-低"):
    lo=chip["70%成本-低"]; hi=chip["70%成本-高"]
    if P>hi:
        votes.append(0.5); reasons.append(f"价格站上70%成本上沿 {hi:.2f} (+0.5)")
    elif P<lo:
        votes.append(-0.5); reasons.append(f"价格跌入70%成本下沿 {lo:.2f} (-0.5)")

vote_sum=sum(votes)
if vote_sum>=1.5:
    direction="bullish"; strength="强"; conclusion="看涨倾向（趋势偏强）"
elif vote_sum>=0.5:
    direction="bullish"; strength="弱"; conclusion="弱看涨倾向"
elif vote_sum<=-1.5:
    direction="bearish"; strength="强"; conclusion="看跌倾向（趋势偏弱）"
elif vote_sum<=-0.5:
    direction="bearish"; strength="弱"; conclusion="弱看跌倾向"
else:
    direction="neutral"; strength="无"; conclusion="方向不明确"

# 5日涨幅 etc
last_5d_change=float(last['5日涨幅']) if pd.notna(last['5日涨幅']) else None
last_turn=float(last['换手率']) if pd.notna(last['换手率']) else None
last_vol_ratio=float(last['量比']) if pd.notna(last['量比']) else None

# Build summary json
summary={
    "ticker":"GOOG",
    "rows":len(df),
    "date_start":str(df['日期'].iloc[0].date()),
    "date_end":str(df['日期'].iloc[-1].date()),
    "last_close":P,
    "last_date":str(df['日期'].iloc[-1].date()),
    "52w_high":high52,
    "52w_low":low52,
    "ath":ath,
    "ath_date":str(ath_date),
    "ma":ma_vals,
    "gaps":gaps,
    "state":state,
    "state_desc":desc,
    "state_gate":gate,
    "state_history": {k:v for k,v in state_hist.items() if k!="segments"},
    "chip":chip,
    "vp_long": {"hvn_lo":float(vp_long['hvn_lo']) if vp_long else None, "hvn_hi":float(vp_long['hvn_hi']) if vp_long else None, "gap_pct":float(vp_long['gap_pct']) if vp_long else None, "concentration":float(vp_long['concentration']) if vp_long else None, "period":vp_long['period'] if vp_long else None} if vp_long else None,
    "vp_short": {"hvn_lo":float(vp_short['hvn_lo']) if vp_short else None, "hvn_hi":float(vp_short['hvn_hi']) if vp_short else None, "gap_pct":float(vp_short['gap_pct']) if vp_short else None, "concentration":float(vp_short['concentration']) if vp_short else None, "period":vp_short['period'] if vp_short else None} if vp_short else None,
    "avwap":{"anchor_date":str(anchor_date.date()),"anchor_price":anchor_price,"reason":anchor_reason,"avwap":avwap_last,"diff_pct":diff_pct,"days_ago":days_ago},
    "atr":atr,
    "atr_pct":atr/P*100 if atr else None,
    "amp_stats":amp_stats,
    "turnover":{"all_mean":float(turn_all.mean()),"recent_mean":float(turn_recent.mean())},
    "gbm":{"5d":gbm5,"10d":gbm10,"30d":gbm30},
    "direction":{"direction":direction,"strength":strength,"vote_sum":vote_sum,"conclusion":conclusion,"reasons":reasons},
    "last_5d":last_5d_change,
    "last_turnover":last_turn,
    "last_vol_ratio":last_vol_ratio,
}

# save
_RPT=_ROOT/"reports"/"GOOG"/"2026-09-16"
os.makedirs(_RPT, exist_ok=True)
with open(_RPT/"quant_summary.json","w",encoding="utf-8") as f:
    json.dump(summary,f,ensure_ascii=False,indent=2)

print(json.dumps(summary, ensure_ascii=False, indent=2))
