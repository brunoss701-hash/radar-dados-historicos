import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

QUANTFURY_STOCKS_URL = "https://help.quantfury.com/en/articles/5448752-stocks"
QUANTFURY_ETFS_URL = "https://help.quantfury.com/en/articles/5448760-etfs"

QUANTFURY_CRYPTO_USDT = [
    "AAVEUSDT", "ADAUSDT", "APTUSDT", "ARBUSDT", "ATOMUSDT", "AVAXUSDT",
    "BCHUSDT", "BTCUSDT", "DOGEUSDT", "DOTUSDT", "ETCUSDT", "ETHUSDT",
    "FILUSDT", "FTMUSDT", "HBARUSDT", "HYPEUSDT", "INJUSDT", "LINKUSDT",
    "LTCUSDT", "MANAUSDT", "POLUSDT", "NEARUSDT", "NEOUSDT", "ONDOUSDT",
    "OPUSDT", "RUNEUSDT", "SANDUSDT", "SUIUSDT", "SOLUSDT", "TAOUSDT",
    "GRAMUSDT", "THETAUSDT", "UNIUSDT", "VIRTUALUSDT", "XLMUSDT",
    "XRPUSDT", "ZECUSDT",
]

PREFERRED_US_STOCKS = """
AAPL MSFT NVDA AMZN GOOGL GOOG META TSLA AVGO AMD NFLX ORCL CRM ADBE INTC QCOM MU AMAT ARM PLTR CRWD PANW NOW SNOW DDOG MDB NET SHOP UBER ABNB DASH COIN HOOD RBLX PYPL SQ AFRM SOFI UPST
JPM BAC WFC GS MS C SCHW BLK BX AXP V MA COF USB PNC TFC BK STT
LLY UNH JNJ MRK ABBV TMO AMGN GILD REGN VRTX ISRG BSX MDT SYK ZTS BMY CVS CI HUM HCA
XOM CVX COP SLB OXY EOG MPC VLO PSX HAL BKR DVN FANG
WMT COST HD LOW MCD SBUX NKE TGT TJX ROST CMG DPZ ORLY DECK LULU
CAT DE GE RTX LMT BA NOC GD HON ETN PH URI CARR TT PWR EMR MMM
DIS CMCSA T VZ TMUS CHTR SPOT WBD
PG KO PEP PM MO CL KMB MDLZ GIS KHC
NEE DUK SO AEP EXC SRE
LIN APD FCX NUE STLD
UPS FDX UNP CSX NSC DAL UAL LUV
BKNG MAR HLT RCL CCL EXPE
IBM ACN INTU ADP CDNS SNPS ANET DELL HPE HPQ
MRVL ON NXPI MCHP MPWR KLAC LRCX ASML
APP DUOL DKNG ROKU PTON RIVN LCID RKLB ASTS JOBY ACHR IONQ RGTI QUBT WULF RIOT MARA
PDD BIDU BILI NIO XPEV LI
CVNA CAR GME AMC
FSLR ENPH RUN
""".split()

PREFERRED_B3_STOCKS = """
ABEV3 B3SA3 BBAS3 BBDC4 BBSE3 BPAC11 BRAV3 CMIG4 CMIN3 CSNA3 CXSE3 EGIE3
EMBJ3 ENEV3 EQTL3 GGBR4 HAPV3 ITSA4 ITUB4 KLBN11 LREN3 MGLU3 MRVE3 PETR4
PRIO3 RADL3 RAIL3 RDOR3 RENT3 SANB11 SUZB3 TAEE11 TIMS3 TOTS3 UGPA3 VALE3
VBBR3 VIVT3 WEGE3 YDUQ3
""".split()

QUANTFURY_ETFS = """
BOVA11 KSA UAE BOTZ ETHA IBIT IEF JEPQ MCHI QQQ ROBT SHY SMH TLT TUR WOOD
ARGT ARKK BOIL COPX DIA EEM EWA EWG EWH EWJ EWP EWQ EWT EWU EWW EWY EWZ EZA
FBTC FXI GBTC GDX GLD ILF INDA IWM IXN IYW JEPI LIT METV OIH PPLT ROBO SCHD SLV SPY
UNG URA URNM USO UUP VBR VGK VNQ VT XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV XLY XME
""".split()

MAX_US_STOCKS = 180
MAX_B3_STOCKS = 35
MAX_STOCK_INTRADAY = 70
MAX_ETF_INTRADAY = 30
RECENT_BARS_TO_STORE = 12

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; QuantfuryRadar/2.0)"}


def safe_float(value):
    try:
        if value is None or pd.isna(value):
            return None
        return round(float(value), 8)
    except Exception:
        return None


def indicadores(df):
    df = df.copy().sort_index()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    delta = df["close"].diff()
    ganho = delta.clip(lower=0)
    perda = -delta.clip(upper=0)
    media_ganho = ganho.ewm(alpha=1 / 14, adjust=False).mean()
    media_perda = perda.ewm(alpha=1 / 14, adjust=False).mean()
    rs = media_ganho / media_perda.replace(0, float("nan"))
    df["rsi14"] = 100 - (100 / (1 + rs))
    df.loc[(media_perda == 0) & (media_ganho > 0), "rsi14"] = 100.0
    df.loc[(media_perda == 0) & (media_ganho == 0), "rsi14"] = 50.0

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    anterior = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - anterior).abs(),
        (df["low"] - anterior).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["volume_media20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_media20"]
    return df


def snapshot(df):
    if df is None or df.empty:
        return None
    ind = indicadores(df)
    last = ind.iloc[-1]
    close = safe_float(last.get("close"))

    def pct_change(bars):
        if len(ind) <= bars:
            return None
        old = safe_float(ind["close"].iloc[-1 - bars])
        if old in (None, 0) or close is None:
            return None
        return round((close / old - 1) * 100, 4)

    high20 = safe_float(ind["high"].tail(20).max())
    low20 = safe_float(ind["low"].tail(20).min())
    atr = safe_float(last.get("atr14"))

    out = {
        "time": ind.index[-1].isoformat(),
        "open": safe_float(last.get("open")),
        "high": safe_float(last.get("high")),
        "low": safe_float(last.get("low")),
        "close": close,
        "volume": safe_float(last.get("volume")),
        "ema20": safe_float(last.get("ema20")),
        "ema50": safe_float(last.get("ema50")),
        "ema200": safe_float(last.get("ema200")),
        "rsi14": safe_float(last.get("rsi14")),
        "macd": safe_float(last.get("macd")),
        "macd_signal": safe_float(last.get("macd_signal")),
        "macd_hist": safe_float(last.get("macd_hist")),
        "atr14": atr,
        "atr_pct": round((atr / close) * 100, 4) if atr and close else None,
        "volume_ratio": safe_float(last.get("volume_ratio")),
        "change_1bar_pct": pct_change(1),
        "change_5bar_pct": pct_change(5),
        "change_20bar_pct": pct_change(20),
        "high20": high20,
        "low20": low20,
    }
    out["distance_high20_pct"] = round((close / high20 - 1) * 100, 4) if close and high20 else None
    out["distance_low20_pct"] = round((close / low20 - 1) * 100, 4) if close and low20 else None
    return out


def recent_bars(df, n=RECENT_BARS_TO_STORE):
    if df is None or df.empty:
        return []
    out = []
    for when, row in df.tail(n).iterrows():
        out.append({
            "time": when.isoformat(),
            "open": safe_float(row.get("open")),
            "high": safe_float(row.get("high")),
            "low": safe_float(row.get("low")),
            "close": safe_float(row.get("close")),
            "volume": safe_float(row.get("volume")),
        })
    return out


def prioridade(diario):
    if not diario:
        return -999.0
    score = 0.0
    close = diario.get("close")
    e20 = diario.get("ema20")
    e50 = diario.get("ema50")
    e200 = diario.get("ema200")
    rsi = diario.get("rsi14")
    hist = diario.get("macd_hist")
    vr = diario.get("volume_ratio")
    ch5 = diario.get("change_5bar_pct")
    dh = diario.get("distance_high20_pct")
    dl = diario.get("distance_low20_pct")
    atrp = diario.get("atr_pct")

    if all(v is not None for v in [close, e20, e50]):
        if close > e20 > e50 or close < e20 < e50:
            score += 2.0
        else:
            score += 0.5
    if close is not None and e200 is not None:
        score += 0.5
    if rsi is not None:
        if rsi >= 65 or rsi <= 35:
            score += 1.25
        elif 40 <= rsi <= 60:
            score += 0.25
    if hist is not None and hist != 0:
        score += 0.75
    if vr is not None:
        if vr >= 1.5:
            score += 2.0
        elif vr >= 1.2:
            score += 1.0
    if ch5 is not None:
        score += min(abs(ch5) / 4.0, 2.0)
    if dh is not None and dh >= -1.5:
        score += 1.0
    if dl is not None and dl <= 1.5:
        score += 1.0
    if atrp is not None and 1.0 <= atrp <= 8.0:
        score += 0.5
    return round(score, 3)


def obter_catalogo_quantfury(url, exchanges=None):
    resposta = requests.get(url, headers=HEADERS, timeout=30)
    resposta.raise_for_status()
    soup = BeautifulSoup(resposta.text, "html.parser")
    linhas = []
    for tr in soup.find_all("tr"):
        cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if len(cols) < 3:
            continue
        nome, bolsa, ticker = cols[0], cols[1].strip(), cols[2].strip().upper()
        if exchanges and bolsa not in exchanges:
            continue
        if not re.fullmatch(r"[A-Z0-9.&-]+", ticker):
            continue
        linhas.append({"name": nome, "exchange": bolsa, "ticker": ticker})

    uniq = {}
    for item in linhas:
        uniq.setdefault((item["exchange"], item["ticker"]), item)
    return list(uniq.values())


def selecionar_acoes():
    catalog_ok = True
    try:
        catalog = obter_catalogo_quantfury(
            QUANTFURY_STOCKS_URL, exchanges={"NASDAQ", "NYSE", "B3"}
        )
    except Exception as exc:
        print("Falha ao obter catálogo Quantfury:", exc)
        catalog, catalog_ok = [], False

    oficiais = {(x["exchange"], x["ticker"]): x for x in catalog}
    selecionadas = []

    for ticker in PREFERRED_US_STOCKS:
        matches = [
            x for (exchange, tk), x in oficiais.items()
            if tk == ticker and exchange in {"NASDAQ", "NYSE"}
        ]
        if matches:
            item = matches[0]
            selecionadas.append({
                "ticker": ticker,
                "yahoo": ticker.replace(".", "-"),
                "exchange": item["exchange"],
                "name": item["name"],
                "quantfury_verified": True,
            })
        if sum(1 for x in selecionadas if x["exchange"] in {"NASDAQ", "NYSE"}) >= MAX_US_STOCKS:
            break

    for ticker in PREFERRED_B3_STOCKS:
        item = oficiais.get(("B3", ticker))
        if item:
            selecionadas.append({
                "ticker": ticker,
                "yahoo": f"{ticker}.SA",
                "exchange": "B3",
                "name": item["name"],
                "quantfury_verified": True,
            })
        if sum(1 for x in selecionadas if x["exchange"] == "B3") >= MAX_B3_STOCKS:
            break

    if not selecionadas:
        fallback = [
            "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AVGO", "AMD",
            "PLTR", "JPM", "BAC", "XOM", "WMT", "LLY", "UNH",
        ]
        selecionadas = [{
            "ticker": t, "yahoo": t, "exchange": "US", "name": t,
            "quantfury_verified": False,
        } for t in fallback]

    return selecionadas, catalog_ok, len(catalog)


def selecionar_etfs():
    catalog_ok = True
    try:
        catalog = obter_catalogo_quantfury(
            QUANTFURY_ETFS_URL, exchanges={"NASDAQ", "NYSE", "B3"}
        )
        oficiais = {(x["exchange"], x["ticker"]): x for x in catalog}
    except Exception as exc:
        print("Falha ao obter catálogo de ETFs Quantfury:", exc)
        oficiais, catalog, catalog_ok = {}, [], False

    specs = []
    for ticker in QUANTFURY_ETFS:
        matches = [x for (_, tk), x in oficiais.items() if tk == ticker]
        if matches:
            item = matches[0]
            specs.append({
                "ticker": ticker,
                "yahoo": f"{ticker}.SA" if item["exchange"] == "B3" else ticker,
                "exchange": item["exchange"],
                "name": item["name"],
                "quantfury_verified": True,
            })

    if not specs:
        specs = [{
            "ticker": t, "yahoo": t, "exchange": "US", "name": t,
            "quantfury_verified": False,
        } for t in ["SPY", "QQQ", "IWM", "DIA", "SMH", "TLT", "GLD", "SLV", "ARKK"]]

    return specs, catalog_ok, len(catalog)


def normalizar_yahoo(df):
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        level0 = list(out.columns.get_level_values(0))
        level1 = list(out.columns.get_level_values(1))
        if "Open" in level0:
            out.columns = out.columns.get_level_values(0)
        elif "Open" in level1:
            out.columns = out.columns.get_level_values(1)

    out = out.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    cols = ["open", "high", "low", "close", "volume"]
    if not set(cols).issubset(out.columns):
        return pd.DataFrame()
    out = out[cols].dropna(subset=["open", "high", "low", "close"])
    for col in cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["open", "high", "low", "close"])


def batch_yahoo(tickers, period, interval):
    if not tickers:
        return {}
    raw = yf.download(
        tickers=tickers, period=period, interval=interval, group_by="ticker",
        auto_adjust=False, progress=False, threads=True,
    )
    saida = {}
    if raw is None or raw.empty:
        return saida

    if len(tickers) == 1 and not isinstance(raw.columns, pd.MultiIndex):
        saida[tickers[0]] = normalizar_yahoo(raw)
        return saida

    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        level1 = set(raw.columns.get_level_values(1))
        for ticker in tickers:
            try:
                if ticker in level0:
                    df = raw[ticker].copy()
                elif ticker in level1:
                    df = raw.xs(ticker, axis=1, level=1).copy()
                else:
                    continue
                norm = normalizar_yahoo(df)
                if not norm.empty:
                    saida[ticker] = norm
            except Exception:
                continue
    return saida


def remover_vela_incompleta_acoes(df, interval):
    if df is None or df.empty:
        return df
    out = df.copy()
    now_utc = pd.Timestamp.now(tz="UTC")
    if interval == "1h":
        idx = out.index
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        last = idx[-1].tz_convert("UTC")
        if last + pd.Timedelta(hours=1) > now_utc:
            out = out.iloc[:-1]
    elif interval == "1d":
        now_ny = datetime.now(ZoneInfo("America/New_York"))
        if now_ny.weekday() < 5 and now_ny.hour < 16:
            try:
                if pd.Timestamp(out.index[-1]).date() == now_ny.date():
                    out = out.iloc[:-1]
            except Exception:
                pass
    return out


def agregar_4h_sessao(df):
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.sort_index().copy()
    rows = []
    index_dates = pd.Index([pd.Timestamp(x).date() for x in df.index])
    for day in sorted(set(index_dates)):
        day_df = df[index_dates == day]
        for start in range(0, len(day_df), 4):
            chunk = day_df.iloc[start:start + 4]
            if len(chunk) < 4:
                continue
            rows.append({
                "time": chunk.index[0],
                "open": chunk["open"].iloc[0],
                "high": chunk["high"].max(),
                "low": chunk["low"].min(),
                "close": chunk["close"].iloc[-1],
                "volume": chunk["volume"].sum(),
            })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("time")


def binance(symbol, interval):
    urls = [
        "https://data-api.binance.vision/api/v3/klines",
        "https://api.binance.com/api/v3/klines",
    ]
    last_error = None
    for url in urls:
        try:
            resposta = requests.get(
                url,
                params={"symbol": symbol, "interval": interval, "limit": 500},
                headers=HEADERS, timeout=20,
            )
            resposta.raise_for_status()
            dados = resposta.json()
            if not dados:
                raise RuntimeError("sem candles")
            df = pd.DataFrame(dados, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades", "taker_base",
                "taker_quote", "ignore",
            ])
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            df = df[df["close_time"] <= now_ms].copy()
            df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df = df.set_index("time")
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            if df.empty:
                raise RuntimeError("sem candles fechados")
            return df, url
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Binance falhou: {last_error}")


def yahoo_crypto(symbol, interval):
    base = symbol.removesuffix("USDT")
    ticker = f"{base}-USD"
    if interval == "1d":
        raw = yf.download(
            ticker, period="2y", interval="1d", progress=False, auto_adjust=False
        )
        return remover_vela_incompleta_acoes(normalizar_yahoo(raw), "1d")
    raw = yf.download(
        ticker, period="60d", interval="1h", progress=False, auto_adjust=False
    )
    h1 = remover_vela_incompleta_acoes(normalizar_yahoo(raw), "1h")
    return agregar_4h_sessao(h1)


def coletar_crypto(symbol):
    try:
        d4, source4 = binance(symbol, "4h")
        d1, _ = binance(symbol, "1d")
        return symbol, {
            "source": "Binance data-api" if "data-api.binance.vision" in source4 else "Binance API",
            "quantfury_pair": symbol.replace("USDT", "/USDT"),
            "quantfury_verified": True,
            "4h": snapshot(d4),
            "1d": snapshot(d1),
            "recent_4h": recent_bars(d4),
            "recent_1d": recent_bars(d1),
        }, None
    except Exception as binance_error:
        try:
            d4 = yahoo_crypto(symbol, "4h")
            d1 = yahoo_crypto(symbol, "1d")
            if d4.empty or d1.empty:
                raise RuntimeError("Yahoo sem histórico suficiente")
            return symbol, {
                "source": "Yahoo Finance (fallback)",
                "quantfury_pair": symbol.replace("USDT", "/USDT"),
                "quantfury_verified": True,
                "4h": snapshot(d4),
                "1d": snapshot(d1),
                "recent_4h": recent_bars(d4),
                "recent_1d": recent_bars(d1),
            }, None
        except Exception as yahoo_error:
            return symbol, None, f"Binance: {binance_error}; Yahoo: {yahoo_error}"


def processar_specs(specs, intraday_limit):
    yahoo_to_spec = {x["yahoo"]: x for x in specs}
    tickers = list(yahoo_to_spec)
    diarios = batch_yahoo(tickers, period="2y", interval="1d")
    daily_rank, dados, erros = [], {}, {}

    for yticker, spec in yahoo_to_spec.items():
        df = remover_vela_incompleta_acoes(diarios.get(yticker), "1d")
        snap = snapshot(df)
        if not snap:
            erros[spec["ticker"]] = "Yahoo Finance sem série diária válida."
            continue
        score = prioridade(snap)
        daily_rank.append((score, yticker))
        dados[spec["ticker"]] = {
            "source": "Yahoo Finance",
            "exchange": spec["exchange"],
            "name": spec["name"],
            "yahoo_ticker": yticker,
            "quantfury_verified": spec["quantfury_verified"],
            "priority_score": score,
            "1d": snap,
            "recent_1d": recent_bars(df),
            "4h": None,
            "recent_4h": [],
            "intraday_note": "n/c",
        }

    top_yahoo = [yt for _, yt in sorted(daily_rank, reverse=True)[:intraday_limit]]
    horas = batch_yahoo(top_yahoo, period="60d", interval="1h")

    for yticker in top_yahoo:
        spec = yahoo_to_spec[yticker]
        key = spec["ticker"]
        if key not in dados:
            continue
        h1 = remover_vela_incompleta_acoes(horas.get(yticker), "1h")
        h4 = agregar_4h_sessao(h1)
        snap4 = snapshot(h4)
        if snap4:
            dados[key]["4h"] = snap4
            dados[key]["recent_4h"] = recent_bars(h4)
            dados[key]["intraday_note"] = "4h aproximado, agregado de barras 1h por sessão"
        else:
            dados[key]["intraday_note"] = "sem série intradiária válida"
    return dados, erros


def main():
    generated_at = datetime.now(timezone.utc).isoformat()
    stock_specs, stocks_catalog_ok, stock_catalog_count = selecionar_acoes()
    etf_specs, etf_catalog_ok, etf_catalog_count = selecionar_etfs()

    saida = {
        "generated_at": generated_at,
        "meta": {
            "quantfury_stocks_catalog_url": QUANTFURY_STOCKS_URL,
            "quantfury_etfs_catalog_url": QUANTFURY_ETFS_URL,
            "quantfury_stock_catalog_rows_found": stock_catalog_count,
            "quantfury_etf_catalog_rows_found": etf_catalog_count,
            "quantfury_stock_catalog_live_verified": stocks_catalog_ok,
            "quantfury_etf_catalog_live_verified": etf_catalog_ok,
            "crypto_pairs_targeted": len(QUANTFURY_CRYPTO_USDT),
            "stocks_targeted": len(stock_specs),
            "etfs_targeted": len(etf_specs),
            "stock_intraday_target": min(MAX_STOCK_INTRADAY, len(stock_specs)),
            "etf_intraday_target": min(MAX_ETF_INTRADAY, len(etf_specs)),
            "stock_intraday_method": "4h aproximado agregado de barras Yahoo 1h por sessão",
        },
        "cryptos": {}, "stocks": {}, "etfs": {}, "errors": {},
    }

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(coletar_crypto, s): s for s in QUANTFURY_CRYPTO_USDT
        }
        for future in as_completed(futures):
            symbol, data, error = future.result()
            if data:
                data["priority_score"] = prioridade(data.get("1d"))
                saida["cryptos"][symbol] = data
            else:
                saida["errors"][symbol] = error

    stocks, stock_errors = processar_specs(stock_specs, MAX_STOCK_INTRADAY)
    etfs, etf_errors = processar_specs(etf_specs, MAX_ETF_INTRADAY)
    saida["stocks"] = stocks
    saida["etfs"] = etfs
    saida["errors"].update(stock_errors)
    saida["errors"].update({f"ETF:{k}": v for k, v in etf_errors.items()})

    saida["meta"].update({
        "cryptos_collected": len(saida["cryptos"]),
        "stocks_collected": len(saida["stocks"]),
        "stocks_with_4h": sum(1 for x in saida["stocks"].values() if x.get("4h")),
        "etfs_collected": len(saida["etfs"]),
        "etfs_with_4h": sum(1 for x in saida["etfs"].values() if x.get("4h")),
        "errors_count": len(saida["errors"]),
    })

    resumo = {
        "generated_at": generated_at,
        "meta": saida["meta"],
        "cryptos": {}, "stocks": {}, "etfs": {},
        "errors": saida["errors"],
    }
    for classe in ["cryptos", "stocks", "etfs"]:
        for ticker, dados in saida[classe].items():
            resumo[classe][ticker] = {
                k: v for k, v in dados.items()
                if k not in {"recent_4h", "recent_1d"}
            }

    os.makedirs("data", exist_ok=True)
    with open("data/summary.json", "w", encoding="utf-8") as arquivo:
        json.dump(resumo, arquivo, ensure_ascii=False, indent=2, allow_nan=False)
    with open("data/latest.json", "w", encoding="utf-8") as arquivo:
        json.dump(saida, arquivo, ensure_ascii=False, indent=2, allow_nan=False)

    print(json.dumps(saida["meta"], ensure_ascii=False, indent=2))
    print("Arquivos data/summary.json e data/latest.json criados.")


if __name__ == "__main__":
    main()
