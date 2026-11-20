#!/usr/bin/env python3
"""Coletor independente de OHLCV para radar: Binance -> Bybit -> Kraken -> Coinbase,
Yahoo -> Stooq. Somente APIs públicas de leitura. Python 3.11+; sem dependências."""
from __future__ import annotations
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import io
import json
import math
import os
from pathlib import Path
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")
BASE = Path(__file__).parent
NOW = dt.datetime.now(UTC)
NOW_TS = int(NOW.timestamp())
CRYPTO_SEED = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "TRX", "AVAX", "LINK",
               "BCH", "LTC", "DOT", "UNI", "NEAR", "ATOM", "ETC", "XLM", "AAVE", "FIL",
               "APT", "ARB", "OP", "SUI", "INJ", "PEPE", "SHIB", "HBAR", "TON", "XMR"]
STOCKS = ["NVDA","AVGO","AMD","MSFT","AAPL","GOOGL","AMZN","META","TSLA","NFLX",
          "PLTR","MSTR","COIN","HOOD","MU","INTC","QCOM","CRM","ORCL","JPM"]
ETFS = ["SPY","QQQ","IWM","DIA","SMH","SOXX","IBIT","GLD","TLT","IEF"]
BANNED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


def http_bytes(url: str, timeout=9):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; market-data-research/1.0)",
        "Accept": "application/json,text/csv,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def http_json(url):
    return json.loads(http_bytes(url).decode("utf-8"))


def when(ts):
    return dt.datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def is_valid(c):
    return c[1] > 0 and c[2] >= max(c[1], c[4]) and c[3] <= min(c[1], c[4]) and c[3] > 0 and c[5] >= 0


def normalize(rows, seconds, is_closed=None):
    """Rows: (open timestamp seconds, open, high, low, close, volume)."""
    dedup = {}
    for row in rows:
        try:
            c = (int(float(row[0])), *[float(v) for v in row[1:6]])
            if all(math.isfinite(v) for v in c) and is_valid(c) and (is_closed(c[0]) if is_closed else c[0] + seconds <= NOW_TS):
                dedup[c[0]] = c
        except (ValueError, TypeError, IndexError):
            continue
    output = sorted(dedup.values())
    return output


def binance(symbol, interval):
    duration = 14400 if interval == "4h" else 86400
    qs = urllib.parse.urlencode({"symbol": symbol, "interval":interval,"limit":500})
    for host in ["https://data-api.binance.vision", "https://api.binance.com"]:
        try:
            rows = http_json(f"{host}/api/v3/klines?{qs}")
            if isinstance(rows, list):
                out = normalize([(r[0] / 1000, *r[1:6]) for r in rows], duration)
                if len(out) >= 30: return out, host
        except Exception:
            pass
    raise ValueError("Binance OHLCV inacessível")


def bybit(symbol, interval):
    qs = urllib.parse.urlencode({"category":"spot", "symbol":symbol,"interval": "240" if interval == "4h" else "D", "limit":500})
    data = http_json(f"https://api.bybit.com/v5/market/kline?{qs}")
    if str(data.get("retCode")) != "0": raise ValueError("Bybit sem candles")
    out = normalize([(int(r[0]) / 1000, *r[1:6]) for r in data["result"]["list"]], 14400 if interval == "4h" else 86400)
    if len(out) < 30: raise ValueError("Bybit histórico insuficiente")
    return out, "https://api.bybit.com"


def kraken(base, interval):
    mapped = "XBT" if base == "BTC" else base
    qs = urllib.parse.urlencode({"pair":mapped + "USD", "interval":240 if interval == "4h" else 1440})
    data = http_json(f"https://api.kraken.com/0/public/OHLC?{qs}")
    if data.get("error"): raise ValueError("Kraken sem candles")
    sets = [v for k,v in data["result"].items() if k != "last"]
    if not sets: raise ValueError("Kraken par não encontrado")
    out = normalize([(r[0],r[1],r[2],r[3],r[4],r[6]) for r in sets[0]], 14400 if interval == "4h" else 86400)
    if len(out) < 30: raise ValueError("Kraken histórico insuficiente")
    return out, "https://api.kraken.com (USD)"


def coinbase(base, interval):
    product = base + "-USD"
    grain = 3600 if interval == "4h" else 86400
    # Coinbase não oferece 4h nativo: obtém candles de 1h e agrega em UTC.
    # Intervalo 1h requer >= 800 horas para média móvel de 200 candles 4h.
    end = NOW_TS - (NOW_TS % grain)
    start = end - (1200 * 3600 if interval == "4h" else 340 * 86400)
    batches = []
    stride = 250 * grain
    for lo in range(start, end, stride):
        hi = min(lo + stride, end)
        qs = urllib.parse.urlencode({"start":when(lo),"end":when(hi),"granularity":grain})
        data = http_json(f"https://api.exchange.coinbase.com/products/{product}/candles?{qs}")
        if not isinstance(data, list): raise ValueError("Coinbase sem candles")
        batches += [(r[0],r[3],r[2],r[1],r[4],r[5]) for r in data]
    rows = normalize(batches, grain)
    if interval == "4h":
        groups = {}
        for r in rows:
            bucket = (r[0] // 14400) * 14400
            groups.setdefault(bucket, []).append(r)
        bars = []
        for b, chunk in sorted(groups.items()):
            chunk.sort()
            if len(chunk) == 4 and [r[0] for r in chunk] == [b + i*3600 for i in range(4)]:
                bars.append((b, chunk[0][1], max(r[2] for r in chunk), min(r[3] for r in chunk), chunk[-1][4], sum(r[5] for r in chunk)))
        rows = bars
    if len(rows) < 30: raise ValueError("Coinbase histórico insuficiente")
    return rows, "https://api.exchange.coinbase.com (USD)"


def crypto_history(base):
    pair = base + "USDT"
    errors = []
    for name, func in [("Binance", lambda i: binance(pair,i)),
                       ("Bybit", lambda i: bybit(pair,i)),
                       ("Kraken", lambda i: kraken(base,i)),
                       ("Coinbase", lambda i: coinbase(base,i))]:
        try:
            d, url1 = func("1d")
            h, url2 = func("4h")
            if len(d) >= 80 and len(h) >= 80:
                return {"1d":d,"4h":h}, name, url1
            errors.append(name+": menos de 80 candles em algum período")
        except Exception as e:
            errors.append(name+": "+str(e)[:100])
    raise ValueError(" | ".join(errors))


def us_stock_day_closed(ts, *, timestamp_in_new_york=True):
    # Um candle diário de ações termina no fechamento regular às 16h NY,
    # não 24h depois do timestamp de abertura. Não inclui after-hours.
    moment = dt.datetime.fromtimestamp(ts, NY if timestamp_in_new_york else UTC)
    end = dt.datetime.combine(moment.date(), dt.time(16,0), tzinfo=NY)
    return end.timestamp() <= NOW_TS


def yahoo(symbol, interval):
    # Ações e ETFs: 1h + 1d; 4h de ações NÃO é padronizado entre sessões.
    iv = "1h" if interval == "1h" else "1d"
    drange = "60d" if iv == "1h" else "2y"
    for host in ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]:
        try:
            qs = urllib.parse.urlencode({"interval":iv,"range":drange,"includePrePost":"false"})
            data = http_json(f"https://{host}/v8/finance/chart/{urllib.parse.quote(symbol)}?{qs}")
            result = data["chart"]["result"][0]
            timestamps = result["timestamp"]
            quotes = result["indicators"]["quote"][0]
            rows = [(ts, quotes["open"][i],quotes["high"][i],quotes["low"][i],quotes["close"][i],quotes["volume"][i])
                    for i,ts in enumerate(timestamps)]
            out = normalize(rows, 3600 if iv == "1h" else 86400, is_closed=us_stock_day_closed if iv == "1d" else None)
            if len(out) >= 30: return out, "https://"+host
        except Exception:
            pass
    raise ValueError("Yahoo Finance histórico inacessível")


def stooq(symbol):
    # Somente histórico diário, com volume. Sem derivar indicadores 4h.
    s = symbol.lower().replace("-", "-") + ".us"
    url = "https://stooq.com/q/d/l/?" + urllib.parse.urlencode({"s":s,"i":"d","d1":(NOW-dt.timedelta(days=900)).strftime("%Y%m%d"),"d2":NOW.strftime("%Y%m%d")})
    data = list(csv.DictReader(io.StringIO(http_bytes(url).decode("utf-8"))))
    rows = []
    for r in data:
        try:
            ts=int(dt.datetime.strptime(r["Date"],"%Y-%m-%d").replace(tzinfo=UTC).timestamp())
            rows.append((ts,r["Open"],r["High"],r["Low"],r["Close"],r.get("Volume",0)))
        except (KeyError, ValueError): pass
    out = normalize(rows,86400,is_closed=lambda t: us_stock_day_closed(t,timestamp_in_new_york=False))
    if len(out)<30: raise ValueError("Stooq histórico insuficiente")
    return out, "https://stooq.com"


def stock_history(sym):
    bars = {}
    source = None
    for tf in ["1d", "1h"]:
        try:
            bars[tf], origin = yahoo(sym,tf)
            source = origin
        except Exception:
            if tf == "1d":
                try:
                    bars[tf], origin = stooq(sym)
                    source = origin
                except Exception: pass
    if not bars: raise ValueError("Yahoo e Stooq sem histórico")
    return bars, source


def ema(vals, length):
    if len(vals) < length: return None
    value = sum(vals[:length]) / length
    a = 2 / (length + 1)
    for v in vals[length:]: value += a*(v-value)
    return value


def ema_series(vals, length):
    out=[]; value=None; a=2/(length+1)
    for i,v in enumerate(vals):
        if i==length-1: value=sum(vals[:length])/length
        elif i>=length: value += a*(v-value)
        out.append(value)
    return out


def rsi(vals, length=14):
    if len(vals) < length+1: return None
    diffs=[vals[i]-vals[i-1] for i in range(1,len(vals))]
    gain=sum(max(d,0) for d in diffs[:length])/length
    loss=sum(max(-d,0) for d in diffs[:length])/length
    for d in diffs[length:]:
        gain=(gain*(length-1)+max(d,0))/length
        loss=(loss*(length-1)+max(-d,0))/length
    if loss==0: return 100.0 if gain>0 else 50.0
    return 100-100/(1+gain/loss)


def atr(rows, length=14):
    if len(rows)<length+1: return None
    trs=[]
    for i in range(1,len(rows)):
        c=rows[i]; p=rows[i-1]
        trs.append(max(c[2]-c[3],abs(c[2]-p[4]),abs(c[3]-p[4])))
    value=sum(trs[:length])/length
    for tr in trs[length:]: value=(value*(length-1)+tr)/length
    return value


def macd(vals):
    if len(vals)<36: return (None,None,None)
    e12=ema_series(vals,12)
    e26=ema_series(vals,26)
    line=[a-b for a,b in zip(e12,e26) if a is not None and b is not None]
    if len(line)<9: return (None,None,None)
    signal=ema(line,9)
    return line[-1],signal,line[-1]-signal


def number(x):
    return None if x is None or not math.isfinite(x) else round(float(x),6)


def indicators(rows):
    if not rows: return None
    closes=[r[4] for r in rows]
    volumes=[r[5] for r in rows]
    ml,ms,mh=macd(closes)
    avg20=statistics.mean(volumes[-21:-1]) if len(volumes)>=21 else None
    ret24 = ((closes[-1]/closes[-2])-1)*100 if len(rows)>1 else None
    return {"num_closed_candles":len(rows),"last_closed_open_utc":when(rows[-1][0]),
            "close":number(closes[-1]),"change_last_candle_pct":number(ret24),
            "ema20":number(ema(closes,20)),"ema50":number(ema(closes,50)),
            "ema200":number(ema(closes,200)),"rsi14":number(rsi(closes)),
            "macd_line":number(ml),"macd_signal":number(ms),"macd_histogram":number(mh),
            "atr14":number(atr(rows)),"volume":number(volumes[-1]),
            "relative_volume20":number(volumes[-1]/avg20) if avg20 and avg20>0 else None,
            "low20":number(min(r[3] for r in rows[-20:])),
            "high20":number(max(r[2] for r in rows[-20:]))}


def binance_snapshot():
    try:
        info=http_json("https://data-api.binance.vision/api/v3/exchangeInfo")
        listed={s["symbol"] for s in info["symbols"] if s["status"] == "TRADING" and s.get("quoteAsset")=="USDT" and s.get("isSpotTradingAllowed", True)}
        data=http_json("https://data-api.binance.vision/api/v3/ticker/24hr")
        d=[]
        for r in data:
            symbol=r.get("symbol", "")
            if symbol not in listed or symbol.endswith(BANNED_SUFFIXES):continue
            try:
                volume=float(r["quoteVolume"]); chg=float(r["priceChangePercent"])
                if volume>=5_000_000 and float(r["lastPrice"])>0:
                    d.append({"base":symbol[:-4],"pair":symbol,"change_24h_pct":chg,"quote_volume_24h_usdt":volume,"last_price_usdt":float(r["lastPrice"])})
            except (KeyError, ValueError): continue
        d.sort(key=lambda x:(x["quote_volume_24h_usdt"]),reverse=True)
        return len(listed),d,None
    except Exception as e:
        return None,[],str(e)[:200]


def screening_priority(item):
    """Prioridade de APROFUNDAMENTO, jamais sinal de trade."""
    t = item.get("indicators",{})
    key = "4h" if item["market"]=="crypto" else "1d"
    i = t.get(key) or t.get("1d") or {}
    if not i:return -100
    p=i.get("close") or 0
    s=0
    for field in ["ema20","ema50"]:
        m=i.get(field)
        if m: s += 1 if p>m else -1
    r=i.get("rsi14")
    if r is not None and (r < 30 or 55<r<72 or r>75):s+=1
    h=i.get("macd_histogram")
    if h is not None:s+=1 if h>0 else -1
    rv=i.get("relative_volume20")
    if rv and rv>1.4:s+=1
    return s


def scan(crypto_limit, stocks_limit):
    listed, ticks, tick_error=binance_snapshot()
    seed=CRYPTO_SEED.copy()
    if ticks:
        # Pre-triagem de TODO snapshot recebido; histórico dos mais líquidos e dos maiores movimentos.
        volume=[t["base"] for t in ticks[:crypto_limit]]
        unusual=sorted(ticks,key=lambda t:abs(t["change_24h_pct"]),reverse=True)
        seed=list(dict.fromkeys(["BTC","ETH"]+volume+ [t["base"] for t in unusual[:10]]))
    else:
        seed=seed[:crypto_limit]
    seed=seed[:crypto_limit]
    ticker_map={r["base"]:r for r in ticks}
    found=[]; failed=[]
    tasks = [("crypto", b) for b in seed] + [("equity", s) for s in (STOCKS+ETFS)[:stocks_limit]]

    def collect(task):
        market, symbol = task
        if market == "crypto":
            bars, provider, endpoint = crypto_history(symbol)
            details = {frame:indicators(arr) for frame,arr in bars.items()}
            return {"market":"crypto","symbol":symbol+"USDT","provider":provider,"source":endpoint,
                    "quote_used":"USDT" if provider in ["Binance","Bybit"] else "USD",
                    "snapshot_24h":ticker_map.get(symbol),"indicators":details,
                    "screening_only":True,"screening_priority":0}
        bars,origin=stock_history(symbol)
        return {"market":"etf" if symbol in ETFS else "stock","symbol":symbol,
                "provider":"Yahoo/Stooq","source":origin,
                "indicators":{tf:indicators(arr) for tf,arr in bars.items()},
                "screening_only":True,"screening_priority":0}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures={executor.submit(collect,task):task for task in tasks}
        for future in as_completed(futures):
            market,symbol=futures[future]
            try:
                found.append(future.result())
            except Exception as e:
                failed.append({"symbol":symbol+("USDT" if market=="crypto" else ""),"reason":str(e)[:300]})
    failed.sort(key=lambda x:x["symbol"])
    for x in found:x["screening_priority"]=screening_priority(x)
    found.sort(key=lambda x:x["screening_priority"],reverse=True)
    return {"schema_version":"1.0","generated_at_utc":NOW.isoformat(),
            "collector":"GitHub Actions / Python stdlib",
            "data_policy":"Cotação externa indicativa. RSI/MACD/ATR/EMA calculados apenas sobre candles fechados; indicação de priorização NÃO é sinal de trade nem preço executável na Quantfury.",
            "counts":{"binance_spot_usdt_pairs_listed":listed,"binance_24h_snapshot_processed":len(ticks),
                       "crypto_history_attempted":len(seed),"crypto_with_ohlcv":sum(x["market"]=="crypto" for x in found),
                       "equities_etfs_history_attempted":stocks_limit,"equities_etfs_with_ohlcv":sum(x["market"]!="crypto" for x in found)},
            "binance_snapshot_error":tick_error,"candidates":found,"failures":failed}


def write_report(report):
    target=BASE/"data"/"latest.json"
    target.parent.mkdir(exist_ok=True,parents=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    # Selo de sucesso no processo somente se houve candles VERDADEIROS.
    count=report["counts"]["crypto_with_ohlcv"]+report["counts"]["equities_etfs_with_ohlcv"]
    print(f"{when(NOW_TS)}: {count} instrumentos com histórico válido; relatório em {target}")
    if count == 0: raise SystemExit("FALHA: nenhum histórico válido; GitHub Action não deve publicar atualização vazia")


def main():
    cl=int(os.getenv("CRYPTO_LIMIT","25"))
    sl=int(os.getenv("EQUITIES_LIMIT","30"))
    write_report(scan(max(1,min(cl,80)),max(1,min(sl,len(STOCKS)+len(ETFS)))))

if __name__ == "__main__":
    main()
