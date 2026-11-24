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

from strategy_engine import ENGINE_VERSION as STRATEGY_ENGINE_VERSION, evaluate_asset

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
    df["sma50"] = df["close"].rolling(50).mean()
    df["sma200"] = df["close"].rolling(200).mean()

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

    # ADX / DMI (Wilder): força e direção da tendência.
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr_wilder = tr.ewm(alpha=1 / 14, adjust=False).mean()
    plus_dm_sm = plus_dm.ewm(alpha=1 / 14, adjust=False).mean()
    minus_dm_sm = minus_dm.ewm(alpha=1 / 14, adjust=False).mean()
    df["plus_di14"] = 100 * plus_dm_sm / atr_wilder.replace(0, float("nan"))
    df["minus_di14"] = 100 * minus_dm_sm / atr_wilder.replace(0, float("nan"))
    dx = 100 * (df["plus_di14"] - df["minus_di14"]).abs() / (
        df["plus_di14"] + df["minus_di14"]
    ).replace(0, float("nan"))
    df["adx14"] = dx.ewm(alpha=1 / 14, adjust=False).mean()

    # Bandas de Bollinger 20,2: posição do preço e compressão/expansão.
    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std(ddof=0)
    df["bb_mid20"] = bb_mid
    df["bb_upper20"] = bb_mid + 2 * bb_std
    df["bb_lower20"] = bb_mid - 2 * bb_std
    df["bb_width_pct"] = (
        (df["bb_upper20"] - df["bb_lower20"]) / bb_mid.replace(0, float("nan"))
    ) * 100
    df["bb_percent_b"] = (
        (df["close"] - df["bb_lower20"]) /
        (df["bb_upper20"] - df["bb_lower20"]).replace(0, float("nan"))
    )

    # Chaikin Money Flow 20: pressão de acumulação/distribuição normalizada.
    hl_range = (df["high"] - df["low"]).replace(0, float("nan"))
    money_flow_multiplier = (
        (2 * df["close"] - df["low"] - df["high"]) / hl_range
    ).fillna(0.0)
    money_flow_volume = money_flow_multiplier * df["volume"]
    df["cmf20"] = (
        money_flow_volume.rolling(20).sum() /
        df["volume"].rolling(20).sum().replace(0, float("nan"))
    )

    # Donchian: níveis anteriores para detectar rompimento sem usar a própria vela.
    df["donchian_high20_prev"] = df["high"].shift(1).rolling(20).max()
    df["donchian_low20_prev"] = df["low"].shift(1).rolling(20).min()

    df["volume_media20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_media20"]
    df["avg_dollar_volume20"] = (df["close"] * df["volume"]).rolling(20).mean()
    df["gap_pct"] = (df["open"] / df["close"].shift(1) - 1) * 100
    df["macd_hist_delta1"] = df["macd_hist"].diff()
    df["rsi_delta1"] = df["rsi14"].diff()
    df["cmf_delta1"] = df["cmf20"].diff()
    df["bb_width_percentile120"] = df["bb_width_pct"].rolling(120).apply(
        lambda x: 100.0 * (x <= x[-1]).mean(), raw=True
    )
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
        "sma50": safe_float(last.get("sma50")),
        "sma200": safe_float(last.get("sma200")),
        "rsi14": safe_float(last.get("rsi14")),
        "macd": safe_float(last.get("macd")),
        "macd_signal": safe_float(last.get("macd_signal")),
        "macd_hist": safe_float(last.get("macd_hist")),
        "atr14": atr,
        "atr_pct": round((atr / close) * 100, 4) if atr and close else None,
        "adx14": safe_float(last.get("adx14")),
        "plus_di14": safe_float(last.get("plus_di14")),
        "minus_di14": safe_float(last.get("minus_di14")),
        "bb_mid20": safe_float(last.get("bb_mid20")),
        "bb_upper20": safe_float(last.get("bb_upper20")),
        "bb_lower20": safe_float(last.get("bb_lower20")),
        "bb_width_pct": safe_float(last.get("bb_width_pct")),
        "bb_percent_b": safe_float(last.get("bb_percent_b")),
        "cmf20": safe_float(last.get("cmf20")),
        "donchian_high20_prev": safe_float(last.get("donchian_high20_prev")),
        "donchian_low20_prev": safe_float(last.get("donchian_low20_prev")),
        "volume_ratio": safe_float(last.get("volume_ratio")),
        "avg_dollar_volume20": safe_float(last.get("avg_dollar_volume20")),
        "gap_pct": safe_float(last.get("gap_pct")),
        "macd_hist_delta1": safe_float(last.get("macd_hist_delta1")),
        "rsi_delta1": safe_float(last.get("rsi_delta1")),
        "cmf_delta1": safe_float(last.get("cmf_delta1")),
        "bb_width_percentile120": safe_float(last.get("bb_width_percentile120")),
        "change_1bar_pct": pct_change(1),
        "change_5bar_pct": pct_change(5),
        "change_20bar_pct": pct_change(20),
        "high20": high20,
        "low20": low20,
    }
    out["distance_high20_pct"] = round((close / high20 - 1) * 100, 4) if close and high20 else None
    out["distance_low20_pct"] = round((close / low20 - 1) * 100, 4) if close and low20 else None
    e20 = out.get("ema20")
    e50 = out.get("ema50")
    s200 = out.get("sma200")
    out["distance_ema20_pct"] = round((close / e20 - 1) * 100, 4) if close and e20 else None
    out["distance_ema50_pct"] = round((close / e50 - 1) * 100, 4) if close and e50 else None
    out["distance_sma200_pct"] = round((close / s200 - 1) * 100, 4) if close and s200 else None

    don_high = out.get("donchian_high20_prev")
    don_low = out.get("donchian_low20_prev")
    if close is not None and don_high is not None and close > don_high:
        out["donchian_breakout20"] = "up"
    elif close is not None and don_low is not None and close < don_low:
        out["donchian_breakout20"] = "down"
    else:
        out["donchian_breakout20"] = "none"
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


def setup_scores(diario):
    if not diario:
        return {
            "long_score": -999.0,
            "short_score": -999.0,
            "reversal_long_score": -999.0,
            "reversal_short_score": -999.0,
            "best_setup": "n/c",
            "best_setup_score": -999.0,
        }

    close = diario.get("close")
    e20 = diario.get("ema20")
    e50 = diario.get("ema50")
    e200 = diario.get("ema200")
    s200 = diario.get("sma200")
    rsi = diario.get("rsi14")
    hist = diario.get("macd_hist")
    hist_d = diario.get("macd_hist_delta1")
    rsi_d = diario.get("rsi_delta1")
    cmf = diario.get("cmf20")
    cmf_d = diario.get("cmf_delta1")
    adx = diario.get("adx14")
    plus_di = diario.get("plus_di14")
    minus_di = diario.get("minus_di14")
    vr = diario.get("volume_ratio")
    breakout = diario.get("donchian_breakout20")
    rel20 = diario.get("relative_strength_20bar_pct")
    bb_b = diario.get("bb_percent_b")
    bb_pctile = diario.get("bb_width_percentile120")
    dh = diario.get("distance_high20_pct")
    dl = diario.get("distance_low20_pct")

    long_score = 0.0
    short_score = 0.0
    rev_long = 0.0
    rev_short = 0.0

    # Tendência: separa explicitamente direção de força.
    if close is not None and e20 is not None:
        long_score += 1.0 if close > e20 else 0.0
        short_score += 1.0 if close < e20 else 0.0
    if e20 is not None and e50 is not None:
        long_score += 1.0 if e20 > e50 else 0.0
        short_score += 1.0 if e20 < e50 else 0.0
    if e50 is not None and e200 is not None:
        long_score += 1.0 if e50 > e200 else 0.0
        short_score += 1.0 if e50 < e200 else 0.0
    if close is not None and s200 is not None:
        long_score += 0.5 if close > s200 else 0.0
        short_score += 0.5 if close < s200 else 0.0

    # Momento: RSI extremo não é automaticamente reversão.
    if rsi is not None:
        if 50 <= rsi <= 70:
            long_score += 0.8
        elif 40 <= rsi < 50:
            long_score += 0.25
        elif rsi > 78:
            long_score -= 0.35

        if 30 <= rsi <= 50:
            short_score += 0.8
        elif 50 < rsi <= 60:
            short_score += 0.25
        elif rsi < 22:
            short_score -= 0.35

        if rsi <= 30:
            rev_long += 1.25
            if rsi <= 24:
                rev_long += 0.5
        if rsi >= 70:
            rev_short += 1.25
            if rsi >= 76:
                rev_short += 0.5

    if hist is not None:
        long_score += 0.8 if hist > 0 else 0.0
        short_score += 0.8 if hist < 0 else 0.0
    rev_long_context = (
        (rsi is not None and rsi <= 40)
        or (bb_b is not None and bb_b <= 0.20)
        or (dl is not None and dl <= 3.0)
    )
    rev_short_context = (
        (rsi is not None and rsi >= 60)
        or (bb_b is not None and bb_b >= 0.80)
        or (dh is not None and dh >= -3.0)
    )

    if hist_d is not None:
        if hist_d > 0:
            long_score += 0.25
            if rev_long_context:
                rev_long += 0.7
        elif hist_d < 0:
            short_score += 0.25
            if rev_short_context:
                rev_short += 0.7
    if rsi_d is not None:
        if rev_long_context and rsi_d > 0:
            rev_long += 0.4
        if rev_short_context and rsi_d < 0:
            rev_short += 0.4

    # Força de tendência e pressão compradora/vendedora.
    if adx is not None and adx >= 25 and plus_di is not None and minus_di is not None:
        if plus_di > minus_di:
            long_score += 1.2
        elif minus_di > plus_di:
            short_score += 1.2
    if cmf is not None:
        long_score += 0.8 if cmf > 0.05 else 0.0
        short_score += 0.8 if cmf < -0.05 else 0.0
    if cmf_d is not None:
        if rev_long_context and cmf_d > 0:
            rev_long += 0.35
        if rev_short_context and cmf_d < 0:
            rev_short += 0.35

    # Participação, rompimento e força relativa.
    if vr is not None and vr >= 1.2:
        long_score += 0.45
        short_score += 0.45
        if rev_long_context:
            rev_long += 0.25
        if rev_short_context:
            rev_short += 0.25
    if breakout == "up":
        long_score += 1.5
    elif breakout == "down":
        short_score += 1.5
    if rel20 is not None:
        if rel20 > 0:
            long_score += min(rel20 / 8.0, 1.25)
        elif rel20 < 0:
            short_score += min(abs(rel20) / 8.0, 1.25)

    # Posição nas Bandas de Bollinger e compressão.
    if bb_b is not None:
        if bb_b < 0.10:
            rev_long += 0.6
        elif bb_b > 0.90:
            rev_short += 0.6
    if bb_pctile is not None and bb_pctile <= 20:
        # Squeeze: aumenta interesse no rompimento, mas não define direção.
        long_score += 0.25
        short_score += 0.25

    # Proximidade de extremos recentes.
    if dh is not None and -2.0 <= dh <= 0.5:
        long_score += 0.35
    if dl is not None and -0.5 <= dl <= 2.0:
        short_score += 0.35

    # Penaliza tentativa de reversão contra tendência muito forte sem melhora de momento.
    if adx is not None and adx >= 40:
        if rev_long > 0 and not (hist_d is not None and hist_d > 0):
            rev_long -= 0.5
        if rev_short > 0 and not (hist_d is not None and hist_d < 0):
            rev_short -= 0.5

    scores = {
        "long": round(long_score, 3),
        "short": round(short_score, 3),
        "reversal_long": round(rev_long, 3),
        "reversal_short": round(rev_short, 3),
    }
    best_setup = max(scores, key=scores.get)
    return {
        "long_score": scores["long"],
        "short_score": scores["short"],
        "reversal_long_score": scores["reversal_long"],
        "reversal_short_score": scores["reversal_short"],
        "best_setup": best_setup,
        "best_setup_score": scores[best_setup],
    }


def prioridade(diario):
    # Mantido por compatibilidade: agora é apenas a melhor nota DIRECIONAL.
    return setup_scores(diario)["best_setup_score"]


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


def remover_vela_incompleta_acoes(df, interval, exchange=None):
    if df is None or df.empty:
        return df
    out = df.copy()
    now_utc = pd.Timestamp.now(tz="UTC")

    if exchange == "B3":
        market_tz = ZoneInfo("America/Sao_Paulo")
        close_hour, close_minute = 17, 0
    else:
        market_tz = ZoneInfo("America/New_York")
        close_hour, close_minute = 16, 0

    now_market = datetime.now(market_tz)
    market_close_reached = (
        (now_market.hour, now_market.minute) >= (close_hour, close_minute)
    )

    if interval == "1h":
        idx = out.index
        if idx.tz is None:
            idx_utc = idx.tz_localize("UTC")
        else:
            idx_utc = idx.tz_convert("UTC")
        last_utc = idx_utc[-1]
        last_market = last_utc.tz_convert(market_tz)

        # Yahoo usa uma barra final parcial de 15:30–16:00 nos EUA.
        # Depois do fechamento ela já é válida mesmo sem completar 60 minutos.
        final_us_bar_closed = (
            exchange in {"NASDAQ", "NYSE", "US", None}
            and market_close_reached
            and last_market.date() == now_market.date()
            and (last_market.hour, last_market.minute) >= (15, 30)
        )
        if not final_us_bar_closed and last_utc + pd.Timedelta(hours=1) > now_utc:
            out = out.iloc[:-1]

    elif interval == "1d":
        if now_market.weekday() < 5 and not market_close_reached:
            try:
                if pd.Timestamp(out.index[-1]).date() == now_market.date():
                    out = out.iloc[:-1]
            except Exception:
                pass
    return out


def remover_vela_incompleta_crypto(df, interval):
    if df is None or df.empty:
        return df
    out = df.copy()
    idx = out.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    last = idx[-1]
    now_utc = pd.Timestamp.now(tz="UTC")
    delta = pd.Timedelta(days=1) if interval == "1d" else pd.Timedelta(hours=1)
    if last + delta > now_utc:
        out = out.iloc[:-1]
    return out


def agregar_4h_continuo(df):
    # Para cripto 24/7: só aceita blocos completos de quatro barras de 1h.
    if df is None or df.empty:
        return pd.DataFrame()
    base = df.sort_index()
    grouped = base.resample("4h", origin="start_day")
    out = grouped.agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    counts = grouped["close"].count()
    out = out[counts == 4]
    return out.dropna(subset=["open", "high", "low", "close"])


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
        return remover_vela_incompleta_crypto(normalizar_yahoo(raw), "1d")
    raw = yf.download(
        ticker, period="60d", interval="1h", progress=False, auto_adjust=False
    )
    h1 = remover_vela_incompleta_crypto(normalizar_yahoo(raw), "1h")
    return agregar_4h_continuo(h1)


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
    dados, erros = {}, {}

    for yticker, spec in yahoo_to_spec.items():
        df = remover_vela_incompleta_acoes(
            diarios.get(yticker), "1d", spec.get("exchange")
        )
        snap = snapshot(df)
        if not snap:
            erros[spec["ticker"]] = "Yahoo Finance sem série diária válida."
            continue

        scores = setup_scores(snap)
        dados[spec["ticker"]] = {
            "source": "Yahoo Finance",
            "exchange": spec["exchange"],
            "name": spec["name"],
            "yahoo_ticker": yticker,
            "quantfury_verified": spec["quantfury_verified"],
            "priority_score": scores["best_setup_score"],
            **scores,
            "1d": snap,
            "recent_1d": recent_bars(df),
            "1h": None,
            "recent_1h": [],
            "intraday_note": "n/c",
        }

    # Seleciona intraday de forma balanceada entre continuação long/short e reversões.
    score_fields = [
        "long_score", "short_score",
        "reversal_long_score", "reversal_short_score"
    ]
    quota = max(1, intraday_limit // len(score_fields))
    selected = []
    for field in score_fields:
        ranking = sorted(
            [
                (data.get(field, -999), data.get("yahoo_ticker"))
                for data in dados.values()
                if data.get("yahoo_ticker")
            ],
            reverse=True,
        )
        for _, yticker in ranking[:quota]:
            if yticker not in selected:
                selected.append(yticker)

    if len(selected) < intraday_limit:
        ranking_all = sorted(
            [
                (data.get("priority_score", -999), data.get("yahoo_ticker"))
                for data in dados.values()
                if data.get("yahoo_ticker")
            ],
            reverse=True,
        )
        for _, yticker in ranking_all:
            if yticker not in selected:
                selected.append(yticker)
            if len(selected) >= intraday_limit:
                break

    top_yahoo = selected[:intraday_limit]
    horas = batch_yahoo(top_yahoo, period="60d", interval="1h")

    for yticker in top_yahoo:
        spec = yahoo_to_spec[yticker]
        key = spec["ticker"]
        if key not in dados:
            continue
        h1 = remover_vela_incompleta_acoes(
            horas.get(yticker), "1h", spec.get("exchange")
        )
        snap1 = snapshot(h1)
        if snap1:
            dados[key]["1h"] = snap1
            dados[key]["recent_1h"] = recent_bars(h1)
            dados[key]["intraday_note"] = "1h fechado do Yahoo Finance; não é 4h artificial"
        else:
            dados[key]["intraday_note"] = "sem série intradiária válida"

    return dados, erros


def adicionar_forca_relativa(saida):
    # Compara 20 barras contra um benchmark da mesma classe/mercado.
    benchmarks = {
        "cryptos": ("BTCUSDT", saida.get("cryptos", {})),
        "stocks_us": ("SPY", saida.get("etfs", {})),
        "stocks_b3": ("BOVA11", saida.get("etfs", {})),
        "etfs": ("SPY", saida.get("etfs", {})),
    }

    def aplicar(grupo, benchmark_data):
        if not benchmark_data or not benchmark_data.get("1d"):
            return
        bench20 = benchmark_data["1d"].get("change_20bar_pct")
        bench5 = benchmark_data["1d"].get("change_5bar_pct")
        if bench20 is None:
            return
        for dados in grupo.values():
            diario = dados.get("1d")
            if not diario:
                continue
            ch20 = diario.get("change_20bar_pct")
            ch5 = diario.get("change_5bar_pct")
            diario["relative_strength_20bar_pct"] = (
                round(ch20 - bench20, 4) if ch20 is not None else None
            )
            diario["relative_strength_5bar_pct"] = (
                round(ch5 - bench5, 4)
                if ch5 is not None and bench5 is not None else None
            )

    aplicar(saida.get("cryptos", {}), saida.get("cryptos", {}).get("BTCUSDT"))

    us_stocks = {
        k: v for k, v in saida.get("stocks", {}).items()
        if v.get("exchange") in {"NASDAQ", "NYSE", "US"}
    }
    b3_stocks = {
        k: v for k, v in saida.get("stocks", {}).items()
        if v.get("exchange") == "B3"
    }
    aplicar(us_stocks, saida.get("etfs", {}).get("SPY"))
    aplicar(b3_stocks, saida.get("etfs", {}).get("BOVA11"))
    aplicar(saida.get("etfs", {}), saida.get("etfs", {}).get("SPY"))

    # Recalcula as quatro notas direcionais depois de incluir força relativa.
    for classe in ["cryptos", "stocks", "etfs"]:
        for dados in saida.get(classe, {}).values():
            scores = setup_scores(dados.get("1d"))
            dados.update(scores)
            dados["priority_score"] = scores["best_setup_score"]


def aplicar_scores_multiframe(saida):
    # Combina contexto diário com o timeframe de entrada sem exigir concordância perfeita.
    mapping = {
        "cryptos": ("4h", 0.70),
        "stocks": ("1h", 0.55),
        "etfs": ("1h", 0.55),
    }
    score_keys = [
        ("long_score", "long"),
        ("short_score", "short"),
        ("reversal_long_score", "reversal_long"),
        ("reversal_short_score", "reversal_short"),
    ]

    for classe, (intraday_key, weight) in mapping.items():
        for dados in saida.get(classe, {}).values():
            daily = setup_scores(dados.get("1d"))
            intra = setup_scores(dados.get(intraday_key)) if dados.get(intraday_key) else None

            dados["daily_scores"] = {
                "long": daily["long_score"],
                "short": daily["short_score"],
                "reversal_long": daily["reversal_long_score"],
                "reversal_short": daily["reversal_short_score"],
                "best_setup": daily["best_setup"],
                "best_setup_score": daily["best_setup_score"],
            }

            if intra:
                dados["intraday_scores"] = {
                    "timeframe": intraday_key,
                    "long": intra["long_score"],
                    "short": intra["short_score"],
                    "reversal_long": intra["reversal_long_score"],
                    "reversal_short": intra["reversal_short_score"],
                    "best_setup": intra["best_setup"],
                    "best_setup_score": intra["best_setup_score"],
                }
                combined = {}
                for field, label in score_keys:
                    combined[label] = round(
                        daily[field] + weight * intra[field], 3
                    )
                best = max(combined, key=combined.get)
                dados["long_score"] = combined["long"]
                dados["short_score"] = combined["short"]
                dados["reversal_long_score"] = combined["reversal_long"]
                dados["reversal_short_score"] = combined["reversal_short"]
                dados["best_setup"] = best
                dados["best_setup_score"] = combined[best]
                dados["priority_score"] = combined[best]
                dados["timeframe_alignment"] = (
                    "aligned"
                    if daily["best_setup"] == intra["best_setup"]
                    else "mixed"
                )
                dados["daily_best_setup"] = daily["best_setup"]
                dados["intraday_best_setup"] = intra["best_setup"]
            else:
                dados["intraday_scores"] = None
                dados["timeframe_alignment"] = "daily_only"
                dados["daily_best_setup"] = daily["best_setup"]
                dados["intraday_best_setup"] = None
                dados.update(daily)
                dados["priority_score"] = daily["best_setup_score"]


def aplicar_setups_deterministicos(saida):
    """Acopla o motor v3 sem substituir ainda o score legado."""
    mapping = {"cryptos": "4h", "stocks": "1h", "etfs": "1h"}
    for classe, intraday_key in mapping.items():
        for dados in saida.get(classe, {}).values():
            dados["deterministic_setups"] = evaluate_asset(
                dados.get("1d"), dados.get(intraday_key)
            )


def construir_radar_compacto(saida):
    # Arquivo pequeno para a automação: resume o universo efetivamente processado.
    keep = [
        "time", "close", "ema20", "ema50", "ema200", "rsi14", "rsi_delta1",
        "adx14", "plus_di14", "minus_di14", "cmf20", "volume_ratio",
        "atr14", "atr_pct", "macd_hist", "macd_hist_delta1",
        "donchian_breakout20", "distance_ema20_pct",
        "relative_strength_20bar_pct", "bb_percent_b",
        "change_5bar_pct", "change_20bar_pct",
    ]

    def mini_snap(snap):
        if not snap:
            return None
        return {k: snap.get(k) for k in keep if k in snap}

    assets = []
    for classe in ["cryptos", "stocks", "etfs"]:
        for ticker, dados in saida.get(classe, {}).items():
            intraday_key = "4h" if classe == "cryptos" else "1h"
            assets.append({
                "ticker": ticker,
                "class": classe,
                "exchange": dados.get("exchange"),
                "verified": dados.get("quantfury_verified"),
                "source": dados.get("source"),
                "best_setup": dados.get("best_setup"),
                "best_setup_score": dados.get("best_setup_score"),
                "long_score": dados.get("long_score"),
                "short_score": dados.get("short_score"),
                "reversal_long_score": dados.get("reversal_long_score"),
                "reversal_short_score": dados.get("reversal_short_score"),
                "alignment": dados.get("timeframe_alignment"),
                "daily_best": dados.get("daily_best_setup"),
                "intraday_best": dados.get("intraday_best_setup"),
                "intraday_tf": intraday_key if dados.get(intraday_key) else None,
                "deterministic_setups": dados.get("deterministic_setups", []),
                "d1": mini_snap(dados.get("1d")),
                "intraday": mini_snap(dados.get(intraday_key)),
            })

    score_fields = {
        "long": "long_score",
        "short": "short_score",
        "reversal_long": "reversal_long_score",
        "reversal_short": "reversal_short_score",
    }
    candidates = {}
    for label, field in score_fields.items():
        ranked = sorted(
            [a for a in assets if a.get("verified") and isinstance(a.get(field), (int, float))],
            key=lambda a: a.get(field, -999),
            reverse=True,
        )
        candidates[label] = ranked[:30]

    top_overall = sorted(
        [a for a in assets if a.get("verified")],
        key=lambda a: a.get("best_setup_score", -999),
        reverse=True,
    )[:40]

    fixed = {
        ticker: next((a for a in assets if a["ticker"] == ticker), None)
        for ticker in ["XRPUSDT", "ADAUSDT", "ETHUSDT"]
    }

    state_rank = {
        "TRIGGERED": 0,
        "DAILY_TRIGGER_PENDING_INTRADAY": 1,
        "DAILY_TRIGGER_NO_INTRADAY": 2,
        "ARMED": 3,
    }
    deterministic_candidates = []
    for asset in assets:
        for setup in asset.get("deterministic_setups", []):
            deterministic_candidates.append({
                "ticker": asset["ticker"],
                "class": asset["class"],
                "exchange": asset.get("exchange"),
                "verified": asset.get("verified"),
                **setup,
            })
    deterministic_candidates.sort(
        key=lambda x: (
            state_rank.get(x.get("state"), 99),
            0 if x.get("setup") == "trend_breakout" else 1,
            x.get("ticker", ""),
        )
    )

    return {
        "generated_at": saida.get("generated_at"),
        "meta": saida.get("meta", {}),
        "universe_count": len(assets),
        "top_overall": top_overall,
        "candidates": candidates,
        "deterministic_candidates": deterministic_candidates[:60],
        "fixed": fixed,
        "errors": saida.get("errors", {}),
    }



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
            "stock_intraday_method": "1h fechado do Yahoo Finance; 4h artificial removido",
            "score_model_version": "2.2-directional-multiframe-reversal-context",
            "strategy_engine_version": STRATEGY_ENGINE_VERSION,
            "strategy_engine_status": "experimental_backtest_required",
            "indicator_set_version": "3.2",
            "additional_indicators": [
                "SMA50", "SMA200", "ADX14", "+DI14", "-DI14",
                "Bollinger20x2", "CMF20", "Donchian20",
                "relative_strength_5bar", "relative_strength_20bar"
            ],
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
                scores = setup_scores(data.get("1d"))
                data.update(scores)
                data["priority_score"] = scores["best_setup_score"]
                saida["cryptos"][symbol] = data
            else:
                saida["errors"][symbol] = error

    stocks, stock_errors = processar_specs(stock_specs, MAX_STOCK_INTRADAY)
    etfs, etf_errors = processar_specs(etf_specs, MAX_ETF_INTRADAY)
    saida["stocks"] = stocks
    saida["etfs"] = etfs
    adicionar_forca_relativa(saida)
    aplicar_scores_multiframe(saida)
    aplicar_setups_deterministicos(saida)
    saida["errors"].update(stock_errors)
    saida["errors"].update({f"ETF:{k}": v for k, v in etf_errors.items()})

    saida["meta"].update({
        "cryptos_collected": len(saida["cryptos"]),
        "stocks_collected": len(saida["stocks"]),
        "stocks_with_1h": sum(1 for x in saida["stocks"].values() if x.get("1h")),
        "etfs_collected": len(saida["etfs"]),
        "etfs_with_1h": sum(1 for x in saida["etfs"].values() if x.get("1h")),
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
                if k not in {"recent_4h", "recent_1h", "recent_1d"}
            }

    radar = construir_radar_compacto(saida)

    os.makedirs("data", exist_ok=True)
    with open("data/radar.json", "w", encoding="utf-8") as arquivo:
        json.dump(radar, arquivo, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with open("data/summary.json", "w", encoding="utf-8") as arquivo:
        json.dump(resumo, arquivo, ensure_ascii=False, indent=2, allow_nan=False)
    with open("data/latest.json", "w", encoding="utf-8") as arquivo:
        json.dump(saida, arquivo, ensure_ascii=False, indent=2, allow_nan=False)

    print(json.dumps(saida["meta"], ensure_ascii=False, indent=2))
    print("Arquivos data/radar.json, data/summary.json e data/latest.json criados.")


if __name__ == "__main__":
    main()
