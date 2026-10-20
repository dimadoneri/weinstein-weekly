import io, math, os, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

FINVIZ_EXPORT="https://elite.finviz.com/export/screener"
TOKEN=os.getenv("FINVIZ_API_TOKEN","").strip()
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36"
VIEWS={"overview":"111","performance":"141","technical":"171"}
_CACHE={}; _UNIVERSE={"ts":0,"rows":None}; _PRICE={}; _MARKET={"ts":0,"data":None}

class FinvizError(RuntimeError): pass

def _norm(s): return re.sub(r"[^a-z0-9]+","",str(s).strip().lower())

def _safe(v):
    try:
        if v is None: return None
        if isinstance(v,(int,float,np.number)):
            x=float(v); return None if math.isnan(x) or math.isinf(x) else x
        s=str(v).strip().replace(",","").replace("$","")
        if not s or s in {"-","—","N/A","None","nan"}: return None
        if s.endswith("%"): s=s[:-1]
        mult=1
        if s[-1:].upper()=="K": mult,s=1e3,s[:-1]
        elif s[-1:].upper()=="M": mult,s=1e6,s[:-1]
        elif s[-1:].upper()=="B": mult,s=1e9,s[:-1]
        elif s[-1:].upper()=="T": mult,s=1e12,s[:-1]
        x=float(s)*mult
        return None if math.isnan(x) or math.isinf(x) else x
    except Exception: return None

def _r(v,n=2):
    x=_safe(v); return None if x is None else round(x,n)

def _mcap(v):
    if v is None: return None
    raw=str(v).strip(); x=_safe(v)
    if x is None: return None
    if raw[-1:].upper() in {"K","M","B","T"}: return x
    return x*1_000_000 if x<100_000_000 else x

def _pick(d,*names):
    for n in names:
        k=_norm(n)
        if k in d and pd.notna(d[k]): return d[k]
    return None

def _fetch(view,ticker=None,filters=None,timeout=40):
    if not TOKEN: raise FinvizError("FINVIZ_API_TOKEN غير موجود في Render.")
    params={"v":str(view),"auth":TOKEN}
    if ticker: params["t"]=ticker
    if filters: params["f"]=filters
    try:
        r=requests.get(FINVIZ_EXPORT,params=params,headers={"User-Agent":UA,"Accept":"text/csv,*/*","Referer":"https://elite.finviz.com/"},timeout=timeout)
    except Exception as e:
        raise FinvizError(f"فشل الاتصال بـ Finviz: {str(e)[:120]}")
    if r.status_code in (401,403): raise FinvizError("Finviz رفض التوكن.")
    if not r.ok: raise FinvizError(f"Finviz HTTP {r.status_code}")
    txt=r.text or ""
    if txt.lstrip().lower().startswith(("<html","<!doctype")): raise FinvizError("Finviz أعاد HTML بدل CSV.")
    try: df=pd.read_csv(io.StringIO(txt))
    except Exception as e: raise FinvizError(f"تعذر قراءة CSV: {str(e)[:100]}")
    if df.empty: raise FinvizError("Finviz لم يرجع بيانات.")
    return df

def _rowmap(row): return {_norm(k):v for k,v in row.to_dict().items()}

def finviz_ticker(ticker):
    key=ticker.upper()
    if key in _CACHE and time.time()-_CACHE[key]["ts"]<90: return dict(_CACHE[key]["data"])
    merged={}
    for view in VIEWS.values():
        df=_fetch(view,ticker=key)
        tc=next((c for c in df.columns if _norm(c)=="ticker"),None)
        row=df[df[tc].astype(str).str.upper().str.strip()==key].iloc[0] if tc and not df[df[tc].astype(str).str.upper().str.strip()==key].empty else df.iloc[0]
        merged.update(_rowmap(row))
    if not str(_pick(merged,"Ticker") or "").strip(): raise FinvizError(f"لم أجد {key}.")
    _CACHE[key]={"ts":time.time(),"data":dict(merged)}
    return merged

def _fm(d):
    return {
        "ticker":str(_pick(d,"Ticker") or "").upper(),
        "company":_pick(d,"Company"),"sector":_pick(d,"Sector"),"industry":_pick(d,"Industry"),
        "price":_safe(_pick(d,"Price")),"market_cap":_mcap(_pick(d,"Market Cap")),
        "perf_month":_safe(_pick(d,"Perf Month")),"perf_quarter":_safe(_pick(d,"Perf Quarter")),
        "perf_half":_safe(_pick(d,"Perf Half Y","Perf Half")),"perf_year":_safe(_pick(d,"Perf Year")),
        "sma50_pct":_safe(_pick(d,"SMA50")),"sma200_pct":_safe(_pick(d,"SMA200")),
    }

def _period_days(p):
    p=str(p).lower()
    try:
        if p.endswith("y"): return int(float(p[:-1])*366)
        if p.endswith("mo"): return int(float(p[:-2])*31)
    except: pass
    return 1100

def _yahoo_daily(ticker,period="3y"):
    now=int(time.time()); start=now-_period_days(period)*86400; errs=[]
    for host in ("query1.finance.yahoo.com","query2.finance.yahoo.com"):
        try:
            r=requests.get(f"https://{host}/v8/finance/chart/{ticker}",params={"period1":start,"period2":now+86400,"interval":"1d","includeAdjustedClose":"true","events":"div,splits"},headers={"User-Agent":UA,"Accept":"application/json,*/*"},timeout=15)
            if not r.ok: errs.append(str(r.status_code)); continue
            res=((r.json().get("chart") or {}).get("result") or [])
            if not res: errs.append("no result"); continue
            x=res[0]; ts=x.get("timestamp") or []; q=(((x.get("indicators") or {}).get("quote") or [{}])[0] or {})
            if not ts: continue
            df=pd.DataFrame({"Open":q.get("open") or [],"High":q.get("high") or [],"Low":q.get("low") or [],"Close":q.get("close") or [],"Volume":q.get("volume") or []},index=pd.to_datetime(ts,unit="s",utc=True).tz_convert(None))
            adj=(((x.get("indicators") or {}).get("adjclose") or [{}])[0] or {}).get("adjclose")
            if adj and len(adj)==len(df):
                a=pd.Series(adj,index=df.index,dtype="float64"); rc=pd.to_numeric(df["Close"],errors="coerce")
                f=(a/rc).replace([np.inf,-np.inf],np.nan).fillna(1)
                for c in ("Open","High","Low","Close"): df[c]=pd.to_numeric(df[c],errors="coerce")*f
            df=df.dropna(subset=["High","Low","Close","Volume"]).sort_index()
            if len(df)>=300: df.attrs["source"]="Yahoo Chart API"; return df
        except Exception as e: errs.append(str(e)[:80])
    raise ValueError("Yahoo failed: "+" | ".join(errs[-3:]))

def _stooq_daily(ticker,period="3y"):
    end=pd.Timestamp.utcnow().date(); start=end-pd.Timedelta(days=_period_days(period)+60)
    sym=ticker.lower() if "." in ticker else ticker.lower()+".us"
    r=requests.get("https://stooq.com/q/d/l/",params={"s":sym,"d1":pd.Timestamp(start).strftime("%Y%m%d"),"d2":pd.Timestamp(end).strftime("%Y%m%d"),"i":"d"},headers={"User-Agent":UA},timeout=15)
    if not r.ok or "No data" in (r.text or ""): raise ValueError("Stooq no data")
    df=pd.read_csv(io.StringIO(r.text)); df["Date"]=pd.to_datetime(df["Date"],errors="coerce")
    df=df.dropna(subset=["Date","High","Low","Close","Volume"]).set_index("Date").sort_index()
    if len(df)<300: raise ValueError("Stooq history too short")
    df.attrs["source"]="Stooq"; return df[["Open","High","Low","Close","Volume"]]

def _daily(ticker,period="3y"):
    key=f"{ticker}:{period}"
    if key in _PRICE and time.time()-_PRICE[key]["ts"]<300:
        out=_PRICE[key]["df"].copy(); out.attrs.update(_PRICE[key]["attrs"]); return out
    errs=[]
    for fn in (_yahoo_daily,_stooq_daily):
        try:
            df=fn(ticker,period); _PRICE[key]={"ts":time.time(),"df":df.copy(),"attrs":dict(df.attrs)}; return df
        except Exception as e: errs.append(str(e)[:120])
    raise ValueError("تعذر تحميل الأسعار التاريخية. "+" | ".join(errs))

def _weekly(ticker):
    d=_daily(ticker,"3y")
    w=pd.DataFrame({"Open":d["Open"].resample("W-FRI").first(),"High":d["High"].resample("W-FRI").max(),"Low":d["Low"].resample("W-FRI").min(),"Close":d["Close"].resample("W-FRI").last(),"Volume":d["Volume"].resample("W-FRI").sum()}).dropna()
    w.attrs.update(d.attrs)
    if len(w)<70: raise ValueError("التاريخ الأسبوعي غير كافٍ.")
    return w


def _market_context():
    if _MARKET["data"] and time.time()-_MARKET["ts"]<300: return dict(_MARKET["data"])
    def one(t):
        try:
            w=_weekly(t); c=w["Close"].astype(float); ma10=c.rolling(10).mean(); ma30=c.rolling(30).mean()
            return {"price":float(c.iloc[-1]),"ma10":float(ma10.iloc[-1]),"ma30":float(ma30.iloc[-1]),"slope5":(float(ma30.iloc[-1])/float(ma30.iloc[-6])-1)*100}
        except: return {}
    spy,qqq=one("SPY"),one("QQQ"); score=0
    for x in (spy,qqq):
        if x:
            if x["price"]>x["ma30"]: score+=20
            if x["ma10"]>x["ma30"]: score+=15
            if x["slope5"]>0: score+=15
    label="داعم جدًا" if score>=85 else "داعم" if score>=65 else "مختلط" if score>=45 else "دفاعي"
    data={"score":score,"label":label,"spy":spy,"qqq":qqq}; _MARKET["ts"]=time.time(); _MARKET["data"]=data
    return dict(data)

def _ret(s,n):
    if len(s)<=n: return None
    return (float(s.iloc[-1])/float(s.iloc[-1-n])-1)*100

def _rs(w,spy):
    c=w["Close"].astype(float); s=spy["Close"].astype(float)
    sr={13:_ret(c,13),26:_ret(c,26),52:_ret(c,52)}
    mr={13:_ret(s,13),26:_ret(s,26),52:_ret(s,52)}
    edge={k:(sr[k]-mr[k] if sr[k] is not None and mr[k] is not None else None) for k in sr}
    weighted=(edge[13] or 0)*.25+(edge[26] or 0)*.35+(edge[52] or 0)*.40
    score=100 if weighted>=25 else 90 if weighted>=15 else 80 if weighted>=8 else 70 if weighted>=3 else 60 if weighted>=0 else 45 if weighted>=-5 else 20
    return {"score":score,"weighted_edge":weighted,"stock":sr,"edge":edge}

def _base(w):
    def rng(x):
        lo=float(x["Low"].min()); hi=float(x["High"].max()); return (hi/lo-1)*100 if lo>0 else None
    r20,r12,t4=rng(w.tail(20)),rng(w.tail(12)),rng(w.tail(4))
    lows1=float(w["Low"].iloc[-12:-6].min()); lows2=float(w["Low"].tail(6).min()); higher=lows2>=lows1*.98
    x=w.tail(12); pos=((x["Close"]-x["Low"])/(x["High"]-x["Low"]).replace(0,np.nan)).fillna(.5); upper=float((pos>=.5).mean())
    score=0
    if r12<=15: score+=40
    elif r12<=20: score+=34
    elif r12<=25: score+=26
    elif r12<=35: score+=15
    else: score+=5
    score+=25 if t4<=6 else 20 if t4<=9 else 12 if t4<=13 else 4
    if higher: score+=15
    score+=20 if upper>=.67 else 10 if upper>=.5 else 0
    return {"score":min(100,score),"range20":r20,"range12":r12,"tight4":t4,"higher_lows":higher,"upper":upper,"pivot":float(w["High"].iloc[-13:-1].max()),"base_low":float(w["Low"].iloc[-13:-1].min())}

def _volume(w,pivot):
    v=w["Volume"].astype(float); price=float(w["Close"].iloc[-1]); avg=float(v.iloc[-11:-1].mean()); ratio=float(v.iloc[-1])/avg if avg>0 else None
    x=w.tail(8); up=down=0.0; prev=None
    for _,row in x.iterrows():
        c=float(row["Close"]); vv=float(row["Volume"])
        if prev is not None:
            if c>=prev: up+=vv
            else: down+=vv
        prev=c
    ud=up/down if down>0 else (3.0 if up>0 else None); breakout=price>=pivot
    if breakout and ratio is not None: score=100 if ratio>=1.5 else 85 if ratio>=1.2 else 70 if ratio>=1 else 55
    elif ratio is not None and ratio<=.8: score=78
    elif ud is not None and ud>=1.5: score=68
    else: score=45
    return {"score":score,"ratio":ratio,"ud":ud,"breakout":breakout}

def _dist_count(w):
    avg=w["Volume"].rolling(10).mean(); x=w.tail(8); count=0
    for i in range(1,len(x)):
        idx=x.index[i]; row=x.iloc[i]; prev=x.iloc[i-1]
        if float(row["Close"])<float(prev["Close"]) and pd.notna(avg.loc[idx]) and float(row["Volume"])>float(avg.loc[idx]): count+=1
    return count

def _stage(w,base,rs):
    c=w["Close"].astype(float); ma10s=c.rolling(10).mean(); ma30s=c.rolling(30).mean()
    price=float(c.iloc[-1]); ma10=float(ma10s.iloc[-1]); ma30=float(ma30s.iloc[-1]); slope5=(ma30/float(ma30s.iloc[-6])-1)*100; slope10=(ma30/float(ma30s.iloc[-11])-1)*100
    high52=float(w["High"].tail(52).max()); low52=float(w["Low"].tail(52).min()); draw=(price/high52-1)*100; dist=_dist_count(w)
    reasons=[]
    if price<ma30 and slope5<-1:
        n,ss,label=4,0,"Stage 4 — هبوط"; reasons=["السعر تحت متوسط 30 أسبوع.","متوسط 30 أسبوع يميل للأسفل."]
    elif price>ma30 and ma10>ma30 and slope5>.7:
        n,ss,label=2,100,"Stage 2 — صعود"; reasons=["السعر فوق 30W.","30W صاعد.","10W فوق 30W."]
    elif (draw<=-8 and dist>=2 and (rs["weighted_edge"] or 0)<5) or (price<ma10 and slope5<=0 and dist>=2):
        n,ss,label=3,25,"Stage 3 — توزيع"; reasons=["الاتجاه الأسبوعي فقد زخمه.","ظهرت علامات توزيع/ضعف."]
    else:
        n,ss,label=1,45,"Stage 1 — تجميع"; reasons=["30W مسطح أو غير مؤكد.","السهم يبني قاعدة أكثر من كونه في اتجاه صاعد مؤكد."]
    return {"stage":n,"stage_score":ss,"label":label,"reasons":reasons,"price":price,"ma10":ma10,"ma30":ma30,"slope5":slope5,"slope10":slope10,"distance30":(price/ma30-1)*100,"high52":high52,"low52":low52,"draw52":draw,"distribution":dist}

def _distance_score(st):
    d=st["distance30"]
    if st["stage"]!=2: return 40 if abs(d)<=10 else 20
    return 100 if 0<=d<=10 else 85 if d<=15 else 65 if d<=20 else 40 if d<=30 else 15

def _timing(st,base,vol,rs):
    price=st["price"]; pivot=base["pivot"]; pp=(price/pivot-1)*100; d=st["distance30"]
    if st["stage"]==4: status,label="AVOID","تجنب — Stage 4"
    elif st["stage"]==3: status,label="DISTRIBUTION","توزيع — لا دخول جديد"
    elif st["stage"]==1: status,label=("WATCH","مراقبة — قريب من الاختراق") if -3<=pp<0 else ("TOO EARLY","مبكر — انتظر Stage 2")
    else:
        if 0<=pp<=5 and (vol["ratio"] or 0)>=1.15: status,label="BUY ZONE","منطقة شراء — اختراق أسبوعي"
        elif 0<=d<=5 and rs["score"]>=70: status,label="BUY ZONE","منطقة شراء — Pullback قرب 30W"
        elif -3<=pp<0: status,label="WATCH","مراقبة — قريب من Pivot"
        elif pp>8 or d>25: status,label="EXTENDED","ممتد — انتظر Pullback/Base جديدة"
        else: status,label="HOLD","احتفاظ/مراقبة — Stage 2 قائم"
    stop=max(st["ma30"]*.96,base["base_low"]*.995)
    if stop>=price: stop=price*.93
    return {"status":status,"label":label,"pivot":pivot,"pivot_pct":pp,"stop":stop,"risk_pct":(price/stop-1)*100}

def _overall(st,rs,base,vol,market):
    ds=_distance_score(st)
    total=round(st["stage_score"]*.35+rs["score"]*.20+base["score"]*.15+vol["score"]*.10+ds*.10+market["score"]*.10)
    return total,ds

def _grade(x): return "ممتاز" if x>=85 else "قوي" if x>=75 else "جيد" if x>=65 else "متوسط" if x>=50 else "ضعيف"


def analyze_ticker(ticker,min_cap_b=50):
    ticker=ticker.upper().strip(); fin=finviz_ticker(ticker); m=_fm(fin)
    w=_weekly(ticker); spy=_weekly("SPY"); rs=_rs(w,spy); base=_base(w); vol=_volume(w,base["pivot"]); st=_stage(w,base,rs); market=_market_context(); tm=_timing(st,base,vol,rs); raw,ds=_overall(st,rs,base,vol,market)
    cap=m.get("market_cap"); eligible=cap is not None and cap>=min_cap_b*1e9; score=raw if eligible else max(0,raw-15)
    warnings=[]
    if not eligible: warnings.append(f"السهم خارج الكون المستهدف: المطلوب قيمة سوقية ≥ ${min_cap_b:.0f}B.")
    if st["stage"]!=2: warnings.append("الدخول الأسبوعي المثالي عندنا يحتاج تأكيد Stage 2.")
    if tm["status"]=="EXTENDED": warnings.append("السهم ممتد؛ لا تطارد السعر.")
    if market["score"]<65: warnings.append("السوق العام ليس داعمًا بالكامل؛ خفّض المخاطرة.")
    return {
        "ticker":ticker,"company":m.get("company") or ticker,"sector":m.get("sector"),"industry":m.get("industry"),
        "market_cap":_r(cap,0),"eligible":eligible,"min_cap_b":min_cap_b,"score":score,"raw_score":raw,"grade":_grade(score),
        "timestamp":datetime.now(timezone.utc).isoformat(),
        "source":{"market_and_company":"Finviz Elite","weekly_prices":w.attrs.get("source","Yahoo Chart / Stooq")},
        "stage":{"stage":st["stage"],"stage_score":st["stage_score"],"label":st["label"],"reasons":st["reasons"],"price":_r(st["price"],2),"ma10":_r(st["ma10"],2),"ma30":_r(st["ma30"],2),"ma30_slope5":_r(st["slope5"],2),"ma30_slope10":_r(st["slope10"],2),"distance_30w":_r(st["distance30"],1),"high52":_r(st["high52"],2),"low52":_r(st["low52"],2),"drawdown_52h":_r(st["draw52"],1),"distribution_weeks_8":st["distribution"]},
        "relative_strength":{"score":rs["score"],"weighted_edge":_r(rs["weighted_edge"],1),"stock_returns":{str(k):_r(v,1) for k,v in rs["stock"].items()},"relative_vs_spy":{str(k):_r(v,1) for k,v in rs["edge"].items()},"note":"درجة RS هنا نموذج شفاف مقابل SPY وليست Rating مملوكًا لطرف ثالث."},
        "base":{"score":base["score"],"range20":_r(base["range20"],1),"range12":_r(base["range12"],1),"tight4":_r(base["tight4"],1),"higher_lows":base["higher_lows"],"upper_half_ratio":_r(base["upper"]*100,0),"pivot":_r(base["pivot"],2),"base_low":_r(base["base_low"],2)},
        "volume":{"score":vol["score"],"current_vs_10w":_r(vol["ratio"],2),"up_down_ratio_8w":_r(vol["ud"],2),"breakout":vol["breakout"]},
        "distance_score":ds,"market":market,
        "timing":{"status":tm["status"],"label_ar":tm["label"],"pivot":_r(tm["pivot"],2),"pivot_distance_pct":_r(tm["pivot_pct"],1),"stop":_r(tm["stop"],2),"risk_pct":_r(tm["risk_pct"],1)},
        "warnings":warnings,
        "method_note":"نموذج أسبوعي مستوحى من Stage Analysis: 30-week MA، Stage 1–4، القوة النسبية، القاعدة الأسبوعية، الفوليوم، وعدم المطاردة.",
        "disclaimer":"أداة بحث تعليمية وليست توصية شراء أو بيع. التصنيف آلي ويحتاج مراجعة الشارت قبل التنفيذ."
    }

def _universe():
    if _UNIVERSE["rows"] is not None and time.time()-_UNIVERSE["ts"]<300: return list(_UNIVERSE["rows"])
    dfs=[]
    for name,view in VIEWS.items():
        df=_fetch(view,filters="cap_largeover,sh_price_o10",timeout=45)
        tc=next((c for c in df.columns if _norm(c)=="ticker"),None)
        if not tc: continue
        x=df.copy(); x["Ticker"]=x[tc].astype(str).str.upper().str.strip()
        ren={c:f"{name}__{c}" for c in x.columns if c not in {tc,"Ticker"}}
        x=x.drop(columns=[tc] if tc!="Ticker" else [],errors="ignore").rename(columns=ren); dfs.append(x)
    if not dfs: raise FinvizError("تعذر تحميل Large Cap Universe من Finviz.")
    merged=dfs[0]
    for x in dfs[1:]: merged=merged.merge(x,on="Ticker",how="outer")
    rows=[]
    for _,row in merged.iterrows():
        d={"ticker":row["Ticker"]}
        for c,v in row.items():
            if c=="Ticker" or pd.isna(v): continue
            d[_norm(c.split("__",1)[-1])]=v
        rows.append(d)
    _UNIVERSE["ts"]=time.time(); _UNIVERSE["rows"]=rows
    return list(rows)

def _pre(m):
    score=0
    for k,w,cap in (("perf_month",.25,20),("perf_quarter",.35,35),("perf_half",.25,30),("perf_year",.15,30)):
        v=m.get(k)
        if v is not None: score+=max(-10,min(cap,v))*w
    if (m.get("sma50_pct") or -999)>0: score+=10
    if (m.get("sma200_pct") or -999)>0: score+=15
    return score

def _scan_one(m,spy,market):
    try:
        w=_weekly(m["ticker"]); rs=_rs(w,spy); base=_base(w); vol=_volume(w,base["pivot"]); st=_stage(w,base,rs); tm=_timing(st,base,vol,rs); score,ds=_overall(st,rs,base,vol,market)
        return {"ticker":m["ticker"],"company":m.get("company") or m["ticker"],"sector":m.get("sector"),"market_cap":_r(m.get("market_cap"),0),"price":_r(st["price"],2),"score":score,"stage":st["stage"],"stage_label":st["label"],"status":tm["status"],"status_ar":tm["label"],"ma30":_r(st["ma30"],2),"ma30_slope5":_r(st["slope5"],2),"distance_30w":_r(st["distance30"],1),"rs_score":rs["score"],"rs_edge":_r(rs["weighted_edge"],1),"base_score":base["score"],"volume_score":vol["score"],"volume_ratio":_r(vol["ratio"],2),"pivot":_r(base["pivot"],2),"pivot_distance_pct":_r(tm["pivot_pct"],1),"stop":_r(tm["stop"],2),"risk_pct":_r(tm["risk_pct"],1)}
    except Exception as e:
        return {"ticker":m["ticker"],"error":str(e)[:120]}

def scan_market(min_cap_b=50,min_score=70,max_names=40):
    min_cap_b=max(10,min(1000,float(min_cap_b))); min_score=max(0,min(100,float(min_score))); max_names=max(10,min(60,int(max_names)))
    started=time.time(); market=_market_context(); spy=_weekly("SPY"); rows=_universe(); eligible=[]
    for d in rows:
        m=_fm(d)
        if not m.get("ticker") and d.get("ticker"): m["ticker"]=d["ticker"]
        if m.get("market_cap") is not None and m["market_cap"]>=min_cap_b*1e9: eligible.append((_pre(m),m))
    eligible.sort(key=lambda x:x[0],reverse=True); selected=[m for _,m in eligible[:max_names]]
    results=[]; failures=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        fs={ex.submit(_scan_one,m,spy,market):m["ticker"] for m in selected}
        for fut in as_completed(fs):
            out=fut.result()
            if out.get("error"): failures.append(out)
            else: results.append(out)
    results.sort(key=lambda x:(1 if x["stage"]==2 else 0,1 if x["status"] in {"BUY ZONE","WATCH"} else 0,x["score"],x["rs_score"]),reverse=True)
    qualified=[x for x in results if x["stage"]==2 and x["score"]>=min_score and x["status"]!="EXTENDED"]
    watch=[x for x in results if x not in qualified and x["stage"] in {1,2}][:20]
    return {"timestamp":datetime.now(timezone.utc).isoformat(),"scan_seconds":round(time.time()-started,1),"min_cap_b":min_cap_b,"min_score":min_score,"market":market,"finviz_universe_count":len(rows),"eligible_large_mega_count":len(eligible),"analyzed_count":len(results),"qualified_count":len(qualified),"qualified":qualified[:25],"watch":watch,"failures":failures[:12],"note":"المسح محصور في Large/Mega Caps فوق الحد المختار، ويعطي الأولوية لـ Stage 2 غير الممتدة."}
