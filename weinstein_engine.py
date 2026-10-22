import io, math, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36"
NASDAQ_SCREENER = "https://api.nasdaq.com/api/screener/stocks"

_UNIVERSE = {"ts": 0.0, "rows": None}
_PRICE = {}
_QUOTE = {}
_MARKET = {"ts": 0.0, "data": None}

class FinvizError(RuntimeError):
    # Kept for API compatibility with app.py; no Finviz dependency remains.
    pass

def _safe(v):
    try:
        if v is None:
            return None
        if isinstance(v, (int, float, np.number)):
            x = float(v)
            return None if math.isnan(x) or math.isinf(x) else x
        s = str(v).strip().replace(",", "").replace("$", "")
        if not s or s in {"-", "—", "N/A", "None", "nan"}:
            return None
        if s.endswith("%"):
            s = s[:-1]
        mult = 1.0
        if s[-1:].upper() == "K":
            mult, s = 1e3, s[:-1]
        elif s[-1:].upper() == "M":
            mult, s = 1e6, s[:-1]
        elif s[-1:].upper() == "B":
            mult, s = 1e9, s[:-1]
        elif s[-1:].upper() == "T":
            mult, s = 1e12, s[:-1]
        x = float(s) * mult
        return None if math.isnan(x) or math.isinf(x) else x
    except Exception:
        return None

def _r(v, n=2):
    x = _safe(v)
    return None if x is None else round(x, n)

def _norm_symbol(s):
    return str(s or "").strip().upper().replace("/", ".")

def _nasdaq_universe(cache_seconds=900):
    now = time.time()
    if _UNIVERSE["rows"] is not None and now - _UNIVERSE["ts"] < cache_seconds:
        return list(_UNIVERSE["rows"])

    headers = {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
    }
    params = {
        "tableonly": "true",
        "limit": "25",
        "offset": "0",
        "download": "true",
    }

    try:
        r = requests.get(NASDAQ_SCREENER, params=params, headers=headers, timeout=45)
        r.raise_for_status()
        payload = r.json()
        raw = ((payload.get("data") or {}).get("rows") or [])
    except Exception as e:
        raise FinvizError(f"تعذر تحميل قائمة الأسهم من Nasdaq: {str(e)[:160]}")

    if not raw:
        raise FinvizError("Nasdaq لم يرجع قائمة الأسهم الآن.")

    rows = []
    for x in raw:
        symbol = _norm_symbol(x.get("symbol"))
        if not symbol:
            continue
        cap = _safe(x.get("marketCap"))
        rows.append({
            "ticker": symbol,
            "company": str(x.get("name") or symbol).strip(),
            "sector": str(x.get("sector") or "").strip() or None,
            "industry": str(x.get("industry") or "").strip() or None,
            "market_cap": cap,
            "nasdaq_price": _safe(x.get("lastsale")),
            "volume": _safe(x.get("volume")),
        })

    _UNIVERSE["ts"] = now
    _UNIVERSE["rows"] = rows
    return list(rows)

def _company_meta(ticker):
    ticker = _norm_symbol(ticker)
    rows = _nasdaq_universe()
    exact = next((x for x in rows if x["ticker"] == ticker), None)
    if exact:
        return exact

    # Common Yahoo/Nasdaq class-share notation fallback, e.g. BRK.B / BRK-B.
    alts = {ticker, ticker.replace("-", "."), ticker.replace(".", "-")}
    exact = next((x for x in rows if x["ticker"] in alts), None)
    if exact:
        return exact

    return {
        "ticker": ticker,
        "company": ticker,
        "sector": None,
        "industry": None,
        "market_cap": None,
        "nasdaq_price": None,
        "volume": None,
    }



def _is_overnight_window():
    """
    Blue Ocean / 24H overnight window:
    8:00 PM-4:00 AM New York time, Sunday evening through Friday morning.
    Uses America/New_York so DST is handled automatically.
    """
    now = datetime.now(ZoneInfo("America/New_York"))
    wd = now.weekday()  # Mon=0 ... Sun=6
    mins = now.hour * 60 + now.minute

    evening = wd in {6, 0, 1, 2, 3} and mins >= 20 * 60
    early = wd in {0, 1, 2, 3, 4} and mins < 4 * 60
    return evening or early


def _tradingview_boats_quote(ticker):
    """
    Best-effort overnight quote from TradingView's BOATS symbol.
    TradingView exposes Blue Ocean ATS data on BOATS:* symbols.
    The scanner endpoint is keyless but unofficial/undocumented, so this
    function is isolated as an optional source and fails cleanly.
    """
    if not _is_overnight_window():
        return None

    canonical = _norm_symbol(ticker)
    candidates = []
    for sym in (canonical, canonical.replace("-", "."), canonical.replace(".", "-")):
        tv = f"BOATS:{sym}"
        if tv not in candidates:
            candidates.append(tv)

    payload = {
        "symbols": {"tickers": candidates, "query": {"types": []}},
        "columns": ["name", "close", "change", "change_abs", "volume"],
    }

    try:
        r = requests.post(
            "https://scanner.tradingview.com/america/scan",
            json=payload,
            headers={
                "User-Agent": UA,
                "Accept": "application/json,text/plain,*/*",
                "Content-Type": "application/json",
                "Origin": "https://www.tradingview.com",
                "Referer": "https://www.tradingview.com/",
            },
            timeout=10,
        )
        if not r.ok:
            return None

        data = (r.json() or {}).get("data") or []
        if not data:
            return None

        for item in data:
            values = item.get("d") or []
            if len(values) < 2:
                continue
            price = _safe(values[1])
            if price is None:
                continue

            return {
                "price": _r(price, 2),
                "session": "OVERNIGHT",
                "label_ar": "Overnight (BOATS)",
                "regular_market_price": None,
                "previous_close": None,
                "last_trade_utc": None,
                "source": "TradingView / Blue Ocean ATS (BOATS)",
                "overnight_change_pct": _r(values[2], 2) if len(values) > 2 else None,
                "overnight_change_abs": _r(values[3], 2) if len(values) > 3 else None,
                "overnight_volume": _r(values[4], 0) if len(values) > 4 else None,
            }
    except Exception:
        return None

    return None


def _current_quote(ticker, cache_seconds=20):
    """
    Best-effort display quote across the full 24H cycle.

    Priority:
    1) Overnight 8PM-4AM ET -> TradingView BOATS / Blue Ocean ATS.
    2) Pre-market / regular / after-hours -> Yahoo 1m includePrePost.
    3) Last available regular/weekly close.

    DISPLAY ONLY: Stage/30W/RS/Pivot keep using completed daily/weekly bars.
    """
    canonical = _norm_symbol(ticker)
    cache_key = f"quote:{canonical}"
    c = _QUOTE.get(cache_key)
    if c and time.time() - c["ts"] < cache_seconds:
        return dict(c["data"])

    if _is_overnight_window():
        overnight = _tradingview_boats_quote(canonical)
        if overnight and overnight.get("price") is not None:
            _QUOTE[cache_key] = {"ts": time.time(), "data": dict(overnight)}
            return overnight

    ticker = _yahoo_symbol(canonical)
    errors = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            r = requests.get(
                f"https://{host}/v8/finance/chart/{ticker}",
                params={
                    "range": "1d",
                    "interval": "1m",
                    "includePrePost": "true",
                    "events": "div,splits",
                },
                headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"},
                timeout=12,
            )
            if not r.ok:
                errors.append(f"{host}:{r.status_code}")
                continue

            result = ((r.json().get("chart") or {}).get("result") or [])
            if not result:
                errors.append(f"{host}:no-result")
                continue

            x = result[0]
            meta = x.get("meta") or {}
            ts = x.get("timestamp") or []
            q = (((x.get("indicators") or {}).get("quote") or [{}])[0] or {})
            closes = q.get("close") or []

            last_price = None
            last_ts = None
            for i in range(min(len(ts), len(closes)) - 1, -1, -1):
                px = _safe(closes[i])
                if px is not None:
                    last_price = px
                    last_ts = int(ts[i])
                    break

            regular_price = _safe(meta.get("regularMarketPrice"))
            previous_close = _safe(meta.get("chartPreviousClose") or meta.get("previousClose"))
            price = last_price if last_price is not None else regular_price
            if price is None:
                errors.append(f"{host}:no-price")
                continue

            periods = meta.get("currentTradingPeriod") or {}
            session = "CLOSED"
            if last_ts is not None:
                for key, name in (("pre", "PRE"), ("regular", "REGULAR"), ("post", "POST")):
                    p = periods.get(key) or {}
                    start, end = p.get("start"), p.get("end")
                    if start is not None and end is not None and int(start) <= last_ts <= int(end):
                        session = name
                        break

            labels = {
                "PRE": "Pre-market",
                "REGULAR": "السوق الرسمي",
                "POST": "After-hours",
                "CLOSED": "آخر سعر متاح",
            }

            label = labels.get(session, "آخر سعر متاح")
            if _is_overnight_window() and session != "OVERNIGHT":
                label = "آخر سعر متاح — تعذر BOATS"

            out = {
                "price": _r(price, 2),
                "session": session,
                "label_ar": label,
                "regular_market_price": _r(regular_price, 2),
                "previous_close": _r(previous_close, 2),
                "last_trade_utc": datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat() if last_ts else None,
                "source": "Yahoo Chart 1m (pre/post enabled)",
            }
            _QUOTE[cache_key] = {"ts": time.time(), "data": dict(out)}
            return out
        except Exception as e:
            errors.append(str(e)[:100])

    return {
        "price": None,
        "session": "UNAVAILABLE",
        "label_ar": "آخر إغلاق",
        "regular_market_price": None,
        "previous_close": None,
        "last_trade_utc": None,
        "source": "weekly-close fallback",
        "error": " | ".join(errors[-3:]),
    }


def _period_days(p):
    p = str(p or "3y").lower().strip()
    try:
        if p.endswith("y"):
            return int(float(p[:-1]) * 366)
        if p.endswith("mo"):
            return int(float(p[:-2]) * 31)
        if p.endswith("d"):
            return int(float(p[:-1]))
    except Exception:
        pass
    return 1100

def _yahoo_symbol(ticker):
    return _norm_symbol(ticker).replace(".", "-")

def _yahoo_daily(ticker, period="3y"):
    ticker = _yahoo_symbol(ticker)
    now = int(time.time())
    start = now - _period_days(period) * 86400
    errs = []

    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            r = requests.get(
                f"https://{host}/v8/finance/chart/{ticker}",
                params={
                    "period1": start,
                    "period2": now + 86400,
                    "interval": "1d",
                    "includeAdjustedClose": "true",
                    "events": "div,splits",
                },
                headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"},
                timeout=15,
            )
            if not r.ok:
                errs.append(f"{host}:{r.status_code}")
                continue

            result = ((r.json().get("chart") or {}).get("result") or [])
            if not result:
                errs.append(f"{host}:no-result")
                continue

            x = result[0]
            ts = x.get("timestamp") or []
            q = (((x.get("indicators") or {}).get("quote") or [{}])[0] or {})
            if not ts:
                errs.append(f"{host}:no-timestamps")
                continue

            df = pd.DataFrame({
                "Open": q.get("open") or [],
                "High": q.get("high") or [],
                "Low": q.get("low") or [],
                "Close": q.get("close") or [],
                "Volume": q.get("volume") or [],
            }, index=pd.to_datetime(ts, unit="s", utc=True).tz_convert(None))

            adj = (((x.get("indicators") or {}).get("adjclose") or [{}])[0] or {}).get("adjclose")
            if adj and len(adj) == len(df):
                a = pd.Series(adj, index=df.index, dtype="float64")
                raw_close = pd.to_numeric(df["Close"], errors="coerce")
                factor = (a / raw_close).replace([np.inf, -np.inf], np.nan).fillna(1.0)
                for col in ("Open", "High", "Low", "Close"):
                    df[col] = pd.to_numeric(df[col], errors="coerce") * factor

            df = df.dropna(subset=["High", "Low", "Close", "Volume"]).sort_index()
            if len(df) >= 300:
                df.attrs["source"] = "Yahoo Chart API (daily close data)"
                return df
            errs.append(f"{host}:only-{len(df)}-rows")
        except Exception as e:
            errs.append(str(e)[:100])

    raise ValueError("Yahoo Chart failed: " + " | ".join(errs[-4:]))

def _stooq_daily(ticker, period="3y"):
    end = pd.Timestamp.utcnow().date()
    start = end - pd.Timedelta(days=_period_days(period) + 60)
    sym = _norm_symbol(ticker).lower().replace(".", "-")
    if not sym.endswith(".us"):
        sym += ".us"

    r = requests.get(
        "https://stooq.com/q/d/l/",
        params={
            "s": sym,
            "d1": pd.Timestamp(start).strftime("%Y%m%d"),
            "d2": pd.Timestamp(end).strftime("%Y%m%d"),
            "i": "d",
        },
        headers={"User-Agent": UA, "Accept": "text/csv,text/plain,*/*"},
        timeout=15,
    )
    if not r.ok or "No data" in (r.text or ""):
        raise ValueError("Stooq no data")

    df = pd.read_csv(io.StringIO(r.text))
    if "Date" not in df.columns:
        raise ValueError("Stooq columns missing")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "High", "Low", "Close", "Volume"]).set_index("Date").sort_index()
    if len(df) < 300:
        raise ValueError("Stooq history too short")
    df.attrs["source"] = "Stooq (daily close fallback)"
    return df[["Open", "High", "Low", "Close", "Volume"]]

def _daily(ticker, period="3y"):
    key = f"{ticker}:{period}"
    c = _PRICE.get(key)
    if c and time.time() - c["ts"] < 300:
        out = c["df"].copy()
        out.attrs.update(c["attrs"])
        return out

    errs = []
    for fn in (_yahoo_daily, _stooq_daily):
        try:
            df = fn(ticker, period)
            _PRICE[key] = {"ts": time.time(), "df": df.copy(), "attrs": dict(df.attrs)}
            return df
        except Exception as e:
            errs.append(str(e)[:150])

    raise ValueError("تعذر تحميل الأسعار التاريخية. " + " | ".join(errs))

def _weekly(ticker):
    d = _daily(ticker, "3y")
    w = pd.DataFrame({
        "Open": d["Open"].resample("W-FRI").first(),
        "High": d["High"].resample("W-FRI").max(),
        "Low": d["Low"].resample("W-FRI").min(),
        "Close": d["Close"].resample("W-FRI").last(),
        "Volume": d["Volume"].resample("W-FRI").sum(),
    }).dropna(subset=["High", "Low", "Close", "Volume"])
    w.attrs.update(d.attrs)
    if len(w) < 70:
        raise ValueError("التاريخ الأسبوعي غير كافٍ.")
    return w

def _market_context():
    if _MARKET["data"] and time.time() - _MARKET["ts"] < 300:
        return dict(_MARKET["data"])

    def one(t):
        try:
            w = _weekly(t)
            c = w["Close"].astype(float)
            ma10 = c.rolling(10).mean()
            ma30 = c.rolling(30).mean()
            return {
                "price": float(c.iloc[-1]),
                "ma10": float(ma10.iloc[-1]),
                "ma30": float(ma30.iloc[-1]),
                "slope5": (float(ma30.iloc[-1]) / float(ma30.iloc[-6]) - 1) * 100,
            }
        except Exception:
            return {}

    spy, qqq = one("SPY"), one("QQQ")
    score = 0
    for x in (spy, qqq):
        if x:
            if x["price"] > x["ma30"]:
                score += 20
            if x["ma10"] > x["ma30"]:
                score += 15
            if x["slope5"] > 0:
                score += 15

    label = "داعم جدًا" if score >= 85 else "داعم" if score >= 65 else "مختلط" if score >= 45 else "دفاعي"
    data = {"score": score, "label": label, "spy": spy, "qqq": qqq}
    _MARKET["ts"], _MARKET["data"] = time.time(), data
    return dict(data)

def _ret(s, n):
    if len(s) <= n:
        return None
    return (float(s.iloc[-1]) / float(s.iloc[-1-n]) - 1) * 100

def _rs(w, spy):
    c = w["Close"].astype(float)
    s = spy["Close"].astype(float)
    sr = {13: _ret(c,13), 26: _ret(c,26), 52: _ret(c,52)}
    mr = {13: _ret(s,13), 26: _ret(s,26), 52: _ret(s,52)}
    edge = {k: (sr[k]-mr[k] if sr[k] is not None and mr[k] is not None else None) for k in sr}
    weighted = (edge[13] or 0)*.25 + (edge[26] or 0)*.35 + (edge[52] or 0)*.40
    score = 100 if weighted >= 25 else 90 if weighted >= 15 else 80 if weighted >= 8 else 70 if weighted >= 3 else 60 if weighted >= 0 else 45 if weighted >= -5 else 20
    return {"score": score, "weighted_edge": weighted, "stock": sr, "edge": edge}

def _base(w):
    def rng(x):
        lo, hi = float(x["Low"].min()), float(x["High"].max())
        return (hi/lo - 1)*100 if lo > 0 else None

    r20, r12, t4 = rng(w.tail(20)), rng(w.tail(12)), rng(w.tail(4))
    lows1 = float(w["Low"].iloc[-12:-6].min())
    lows2 = float(w["Low"].tail(6).min())
    higher = lows2 >= lows1*.98

    x = w.tail(12)
    pos = ((x["Close"]-x["Low"]) / (x["High"]-x["Low"]).replace(0,np.nan)).fillna(.5)
    upper = float((pos >= .5).mean())

    score = 40 if r12 <= 15 else 34 if r12 <= 20 else 26 if r12 <= 25 else 15 if r12 <= 35 else 5
    score += 25 if t4 <= 6 else 20 if t4 <= 9 else 12 if t4 <= 13 else 4
    if higher:
        score += 15
    score += 20 if upper >= .67 else 10 if upper >= .5 else 0

    return {
        "score": min(100,score),
        "range20": r20,
        "range12": r12,
        "tight4": t4,
        "higher_lows": higher,
        "upper": upper,
        "pivot": float(w["High"].iloc[-13:-1].max()),
        "base_low": float(w["Low"].iloc[-13:-1].min()),
    }

def _volume(w, pivot):
    v = w["Volume"].astype(float)
    price = float(w["Close"].iloc[-1])
    avg = float(v.iloc[-11:-1].mean())
    ratio = float(v.iloc[-1]) / avg if avg > 0 else None

    x = w.tail(8)
    up = down = 0.0
    prev = None
    for _, row in x.iterrows():
        c, vv = float(row["Close"]), float(row["Volume"])
        if prev is not None:
            if c >= prev:
                up += vv
            else:
                down += vv
        prev = c

    ud = up/down if down > 0 else (3.0 if up > 0 else None)
    breakout = price >= pivot

    if breakout and ratio is not None:
        score = 100 if ratio >= 1.5 else 85 if ratio >= 1.2 else 70 if ratio >= 1 else 55
    elif ratio is not None and ratio <= .8:
        score = 78
    elif ud is not None and ud >= 1.5:
        score = 68
    else:
        score = 45

    return {"score": score, "ratio": ratio, "ud": ud, "breakout": breakout}

def _dist_count(w):
    avg = w["Volume"].rolling(10).mean()
    x = w.tail(8)
    count = 0
    for i in range(1,len(x)):
        idx, row, prev = x.index[i], x.iloc[i], x.iloc[i-1]
        if float(row["Close"]) < float(prev["Close"]) and pd.notna(avg.loc[idx]) and float(row["Volume"]) > float(avg.loc[idx]):
            count += 1
    return count

def _stage(w, base, rs):
    c = w["Close"].astype(float)
    ma10s, ma30s = c.rolling(10).mean(), c.rolling(30).mean()
    price = float(c.iloc[-1])
    ma10, ma30 = float(ma10s.iloc[-1]), float(ma30s.iloc[-1])
    slope5 = (ma30/float(ma30s.iloc[-6])-1)*100
    slope10 = (ma30/float(ma30s.iloc[-11])-1)*100
    high52, low52 = float(w["High"].tail(52).max()), float(w["Low"].tail(52).min())
    draw = (price/high52-1)*100
    dist = _dist_count(w)

    if price < ma30 and slope5 < -1:
        n, ss, label = 4, 0, "Stage 4 — هبوط"
        reasons = ["السعر تحت متوسط 30 أسبوع.","متوسط 30 أسبوع يميل للأسفل."]
    elif price > ma30 and ma10 > ma30 and slope5 > .7:
        n, ss, label = 2, 100, "Stage 2 — صعود"
        reasons = ["السعر فوق 30W.","30W صاعد.","10W فوق 30W."]
    elif (draw <= -8 and dist >= 2 and (rs["weighted_edge"] or 0) < 5) or (price < ma10 and slope5 <= 0 and dist >= 2):
        n, ss, label = 3, 25, "Stage 3 — توزيع"
        reasons = ["الاتجاه الأسبوعي فقد زخمه.","ظهرت علامات توزيع/ضعف."]
    else:
        n, ss, label = 1, 45, "Stage 1 — تجميع"
        reasons = ["30W مسطح أو غير مؤكد.","السهم يبني قاعدة أكثر من كونه في اتجاه صاعد مؤكد."]

    return {
        "stage": n, "stage_score": ss, "label": label, "reasons": reasons,
        "price": price, "ma10": ma10, "ma30": ma30, "slope5": slope5, "slope10": slope10,
        "distance30": (price/ma30-1)*100, "high52": high52, "low52": low52,
        "draw52": draw, "distribution": dist
    }

def _distance_score(st):
    d = st["distance30"]
    if st["stage"] != 2:
        return 40 if abs(d) <= 10 else 20
    return 100 if 0 <= d <= 10 else 85 if d <= 15 else 65 if d <= 20 else 40 if d <= 30 else 15

def _timing(st, base, vol, rs):
    price, pivot, d = st["price"], base["pivot"], st["distance30"]
    pp = (price/pivot-1)*100

    if st["stage"] == 4:
        status, label = "AVOID", "تجنب — Stage 4"
    elif st["stage"] == 3:
        status, label = "DISTRIBUTION", "توزيع — لا دخول جديد"
    elif st["stage"] == 1:
        status, label = ("WATCH","مراقبة — قريب من الاختراق") if -3 <= pp < 0 else ("TOO EARLY","مبكر — انتظر Stage 2")
    else:
        if 0 <= pp <= 5 and (vol["ratio"] or 0) >= 1.15:
            status, label = "BUY ZONE", "منطقة شراء — اختراق أسبوعي"
        elif 0 <= d <= 5 and rs["score"] >= 70:
            status, label = "BUY ZONE", "منطقة شراء — Pullback قرب 30W"
        elif -3 <= pp < 0:
            status, label = "WATCH", "مراقبة — قريب من Pivot"
        elif pp > 8 or d > 25:
            status, label = "EXTENDED", "ممتد — انتظر Pullback/Base جديدة"
        else:
            status, label = "HOLD", "احتفاظ/مراقبة — Stage 2 قائم"

    stop = max(st["ma30"]*.96, base["base_low"]*.995)
    if stop >= price:
        stop = price*.93

    return {
        "status": status, "label": label, "pivot": pivot, "pivot_pct": pp,
        "stop": stop, "risk_pct": (price/stop-1)*100
    }

def _overall(st, rs, base, vol, market):
    ds = _distance_score(st)
    total = round(
        st["stage_score"]*.35 +
        rs["score"]*.20 +
        base["score"]*.15 +
        vol["score"]*.10 +
        ds*.10 +
        market["score"]*.10
    )
    return total, ds

def _grade(x):
    return "ممتاز" if x >= 85 else "قوي" if x >= 75 else "جيد" if x >= 65 else "متوسط" if x >= 50 else "ضعيف"

def analyze_ticker(ticker, min_cap_b=50):
    ticker = _norm_symbol(ticker)
    meta = _company_meta(ticker)
    w = _weekly(ticker)
    spy = _weekly("SPY")

    rs = _rs(w,spy)
    base = _base(w)
    vol = _volume(w,base["pivot"])
    st = _stage(w,base,rs)
    market = _market_context()
    tm = _timing(st,base,vol,rs)
    raw, ds = _overall(st,rs,base,vol,market)

    # Display quote is intentionally separate from the weekly analysis price.
    quote = _current_quote(ticker)
    if quote.get("price") is None:
        quote["price"] = _r(st["price"], 2)
        quote["label_ar"] = "آخر إغلاق متاح"

    cap = meta.get("market_cap")
    eligible = cap is not None and cap >= min_cap_b*1e9
    score = raw if eligible else max(0,raw-15)

    warnings = []
    if not eligible:
        warnings.append(f"القيمة السوقية أقل من ${min_cap_b:.0f}B أو لم تُرجعها Nasdaq؛ السهم خارج الكون المستهدف.")
    if st["stage"] != 2:
        warnings.append("الدخول الأسبوعي المثالي يحتاج تأكيد Stage 2.")
    if tm["status"] == "EXTENDED":
        warnings.append("السهم ممتد؛ لا تطارد السعر.")
    if market["score"] < 65:
        warnings.append("السوق العام ليس داعمًا بالكامل؛ خفّض المخاطرة.")

    return {
        "ticker": ticker,
        "company": meta.get("company") or ticker,
        "sector": meta.get("sector"),
        "industry": meta.get("industry"),
        "market_cap": _r(cap,0),
        "eligible": eligible,
        "min_cap_b": min_cap_b,
        "score": score,
        "raw_score": raw,
        "grade": _grade(score),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": {
            "company_and_market_cap": "Nasdaq Stock Screener",
            "weekly_prices": w.attrs.get("source","Yahoo Chart / Stooq"),
            "display_quote": quote.get("source")
        },
        "quote": quote,
        "stage": {
            "stage": st["stage"], "stage_score": st["stage_score"], "label": st["label"],
            "reasons": st["reasons"], "price": _r(st["price"],2), "ma10": _r(st["ma10"],2),
            "ma30": _r(st["ma30"],2), "ma30_slope5": _r(st["slope5"],2),
            "ma30_slope10": _r(st["slope10"],2), "distance_30w": _r(st["distance30"],1),
            "high52": _r(st["high52"],2), "low52": _r(st["low52"],2),
            "drawdown_52h": _r(st["draw52"],1), "distribution_weeks_8": st["distribution"]
        },
        "relative_strength": {
            "score": rs["score"], "weighted_edge": _r(rs["weighted_edge"],1),
            "stock_returns": {str(k):_r(v,1) for k,v in rs["stock"].items()},
            "relative_vs_spy": {str(k):_r(v,1) for k,v in rs["edge"].items()},
            "note":"درجة RS نموذج شفاف مقابل SPY وليست Rating مملوكًا لطرف ثالث."
        },
        "base": {
            "score": base["score"], "range20": _r(base["range20"],1),
            "range12": _r(base["range12"],1), "tight4": _r(base["tight4"],1),
            "higher_lows": base["higher_lows"], "upper_half_ratio": _r(base["upper"]*100,0),
            "pivot": _r(base["pivot"],2), "base_low": _r(base["base_low"],2)
        },
        "volume": {
            "score": vol["score"], "current_vs_10w": _r(vol["ratio"],2),
            "up_down_ratio_8w": _r(vol["ud"],2), "breakout": vol["breakout"]
        },
        "distance_score": ds,
        "market": market,
        "timing": {
            "status": tm["status"], "label_ar": tm["label"], "pivot": _r(tm["pivot"],2),
            "pivot_distance_pct": _r(tm["pivot_pct"],1), "stop": _r(tm["stop"],2),
            "risk_pct": _r(tm["risk_pct"],1)
        },
        "warnings": warnings,
        "method_note":"السعر أعلى البطاقة يحاول تغطية الدورة كاملة: BOATS للـOvernight، وYahoo للـPre-market/Regular/After-hours؛ أما Stage و30W وباقي المنهج فتبقى على الإغلاقات المكتملة.",
        "disclaimer":"أداة بحث تعليمية وليست توصية شراء أو بيع."
    }

def _pre_from_history(ticker):
    try:
        w = _weekly(ticker)
        c = w["Close"].astype(float)
        r13, r26, r52 = _ret(c,13), _ret(c,26), _ret(c,52)
        ma10, ma30 = c.rolling(10).mean(), c.rolling(30).mean()
        score = (r13 or 0)*.35 + (r26 or 0)*.35 + (r52 or 0)*.15
        if float(c.iloc[-1]) > float(ma30.iloc[-1]): score += 10
        if float(ma10.iloc[-1]) > float(ma30.iloc[-1]): score += 10
        if float(ma30.iloc[-1]) > float(ma30.iloc[-6]): score += 10
        return score
    except Exception:
        return -999

def _scan_one(meta, spy, market):
    try:
        w = _weekly(meta["ticker"])
        rs = _rs(w,spy)
        base = _base(w)
        vol = _volume(w,base["pivot"])
        st = _stage(w,base,rs)
        tm = _timing(st,base,vol,rs)
        score, _ = _overall(st,rs,base,vol,market)

        return {
            "ticker": meta["ticker"], "company": meta.get("company") or meta["ticker"],
            "sector": meta.get("sector"), "market_cap": _r(meta.get("market_cap"),0),
            "price": _r(st["price"],2), "score": score, "stage": st["stage"],
            "stage_label": st["label"], "status": tm["status"], "status_ar": tm["label"],
            "ma30": _r(st["ma30"],2), "ma30_slope5": _r(st["slope5"],2),
            "distance_30w": _r(st["distance30"],1), "rs_score": rs["score"],
            "rs_edge": _r(rs["weighted_edge"],1), "base_score": base["score"],
            "volume_score": vol["score"], "volume_ratio": _r(vol["ratio"],2),
            "pivot": _r(base["pivot"],2), "pivot_distance_pct": _r(tm["pivot_pct"],1),
            "stop": _r(tm["stop"],2), "risk_pct": _r(tm["risk_pct"],1),
        }
    except Exception as e:
        return {"ticker": meta["ticker"], "error": str(e)[:120]}

def scan_market(min_cap_b=50, min_score=70, max_names=40):
    min_cap_b = max(10,min(1000,float(min_cap_b)))
    min_score = max(0,min(100,float(min_score)))
    max_names = max(10,min(60,int(max_names)))

    started = time.time()
    market = _market_context()
    spy = _weekly("SPY")
    rows = _nasdaq_universe()

    eligible = [
        x for x in rows
        if x.get("market_cap") is not None and x["market_cap"] >= min_cap_b*1e9
    ]

    # For $50B+ the universe is manageable. Rank by weekly price action rather
    # than relying on a paid screener's technical columns.
    ranked = []
    workers = min(10, max(4, len(eligible)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fs = {ex.submit(_pre_from_history, x["ticker"]): x for x in eligible}
        for fut in as_completed(fs):
            meta = fs[fut]
            try:
                ranked.append((fut.result(), meta))
            except Exception:
                ranked.append((-999, meta))

    ranked.sort(key=lambda z: z[0], reverse=True)
    selected = [m for _,m in ranked[:max_names]]

    results, failures = [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        fs = {ex.submit(_scan_one,m,spy,market):m["ticker"] for m in selected}
        for fut in as_completed(fs):
            out = fut.result()
            if out.get("error"):
                failures.append(out)
            else:
                results.append(out)

    results.sort(
        key=lambda x: (
            1 if x["stage"] == 2 else 0,
            1 if x["status"] in {"BUY ZONE","WATCH"} else 0,
            x["score"],
            x["rs_score"]
        ),
        reverse=True
    )

    qualified = [
        x for x in results
        if x["stage"] == 2 and x["score"] >= min_score and x["status"] != "EXTENDED"
    ]
    watch = [x for x in results if x not in qualified and x["stage"] in {1,2}][:20]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scan_seconds": round(time.time()-started,1),
        "min_cap_b": min_cap_b,
        "min_score": min_score,
        "market": market,
        "finviz_universe_count": len(rows),  # kept for current frontend compatibility
        "eligible_large_mega_count": len(eligible),
        "analyzed_count": len(results),
        "qualified_count": len(qualified),
        "qualified": qualified[:25],
        "watch": watch,
        "failures": failures[:12],
        "note":"المسح مجاني بالكامل: Nasdaq للكون والقيمة السوقية، وYahoo/Stooq للإغلاقات التاريخية. لا يحتاج Finviz أو أسعار لحظية."
    }


def billion_companies(min_cap_b=10):
    """
    Return the complete US stock directory from Nasdaq's stock screener whose
    reported market cap is >= min_cap_b. No top-N truncation is applied.
    """
    min_cap_b = max(10.0, float(min_cap_b))
    rows = _nasdaq_universe()
    cutoff = min_cap_b * 1_000_000_000

    companies = []
    for x in rows:
        cap = x.get("market_cap")
        if cap is None or cap < cutoff:
            continue
        companies.append({
            "ticker": x.get("ticker"),
            "company": x.get("company") or x.get("ticker"),
            "sector": x.get("sector"),
            "industry": x.get("industry"),
            "market_cap": _r(cap, 0),
            "last_price": _r(x.get("nasdaq_price"), 2),
            "volume": _r(x.get("volume"), 0),
        })

    companies.sort(
        key=lambda x: (x.get("market_cap") or 0, x.get("ticker") or ""),
        reverse=True
    )

    sectors = {}
    for x in companies:
        sector = x.get("sector") or "غير مصنف"
        sectors[sector] = sectors.get(sector, 0) + 1

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "min_cap_b": min_cap_b,
        "count": len(companies),
        "companies": companies,
        "sectors": dict(sorted(sectors.items(), key=lambda kv: kv[1], reverse=True)),
        "source": "Nasdaq Stock Screener",
        "note": "القائمة غير مختصرة: كل سهم في المصدر بقيمة سوقية 10 مليارات دولار فأكثر يظهر هنا."
    }
