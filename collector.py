import json
import os
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf


# =========================================================
# ATIVOS INICIAIS
# Depois ampliaremos esse universo.
# =========================================================

CRYPTOS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
]

STOCKS = [
    "AVGO",
    "NVDA",
    "AAPL",
    "MSFT",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "SPY",
    "QQQ",
]


def indicadores(df):
    df = df.copy()

    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()

    delta = df["close"].diff()

    ganho = delta.clip(lower=0)
    perda = -delta.clip(upper=0)

    media_ganho = ganho.ewm(alpha=1 / 14, adjust=False).mean()
    media_perda = perda.ewm(alpha=1 / 14, adjust=False).mean()

    rs = media_ganho / media_perda
    df["rsi14"] = 100 - (100 / (1 + rs))

    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()

    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    anterior = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - anterior).abs(),
            (df["low"] - anterior).abs(),
        ],
        axis=1,
    ).max(axis=1)

    df["atr14"] = tr.rolling(14).mean()

    df["volume_media20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_media20"]

    return df


def preparar_json(df):
    df = indicadores(df)

    # Guarda as últimas 250 velas
    df = df.tail(250)

    resultado = []

    for data, linha in df.iterrows():
        registro = {
            "time": data.isoformat(),
            "open": linha.get("open"),
            "high": linha.get("high"),
            "low": linha.get("low"),
            "close": linha.get("close"),
            "volume": linha.get("volume"),
            "ema20": linha.get("ema20"),
            "ema50": linha.get("ema50"),
            "ema200": linha.get("ema200"),
            "rsi14": linha.get("rsi14"),
            "macd": linha.get("macd"),
            "macd_signal": linha.get("macd_signal"),
            "macd_hist": linha.get("macd_hist"),
            "atr14": linha.get("atr14"),
            "volume_ratio": linha.get("volume_ratio"),
        }

        registro = {
            k: (
                None
                if pd.isna(v)
                else round(float(v), 8)
                if isinstance(v, (int, float))
                else v
            )
            for k, v in registro.items()
        }

        resultado.append(registro)

    return resultado


def binance(symbol, interval):
    url = "https://api.binance.com/api/v3/klines"

    parametros = {
        "symbol": symbol,
        "interval": interval,
        "limit": 500,
    }

    resposta = requests.get(url, params=parametros, timeout=30)
    resposta.raise_for_status()

    dados = resposta.json()

    df = pd.DataFrame(
        dados,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_base",
            "taker_quote",
            "ignore",
        ],
    )

    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("time")

    for coluna in ["open", "high", "low", "close", "volume"]:
        df[coluna] = pd.to_numeric(df[coluna])

    return df[["open", "high", "low", "close", "volume"]]


def yahoo(ticker, interval):
    if interval == "1d":
        df = yf.download(
            ticker,
            period="2y",
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
    else:
        df = yf.download(
            ticker,
            period="60d",
            interval="1h",
            progress=False,
            auto_adjust=False,
        )

    if df.empty:
        raise Exception("Yahoo Finance não retornou dados.")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )

    df = df[["open", "high", "low", "close", "volume"]]

    if interval == "4h":
        df = df.resample("4h").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )

        df = df.dropna(subset=["open", "high", "low", "close"])

    return df


def main():
    saida = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cryptos": {},
        "stocks": {},
        "errors": {},
    }

    # -------------------------
    # CRIPTOMOEDAS
    # -------------------------

    for symbol in CRYPTOS:
        print("Coletando", symbol)

        try:
            saida["cryptos"][symbol] = {
                "source": "Binance",
                "4h": preparar_json(binance(symbol, "4h")),
                "1d": preparar_json(binance(symbol, "1d")),
            }

        except Exception as erro:
            saida["errors"][symbol] = str(erro)

    # -------------------------
    # AÇÕES E ETFs
    # -------------------------

    for ticker in STOCKS:
        print("Coletando", ticker)

        try:
            saida["stocks"][ticker] = {
                "source": "Yahoo Finance",
                "4h": preparar_json(yahoo(ticker, "4h")),
                "1d": preparar_json(yahoo(ticker, "1d")),
            }

        except Exception as erro:
            saida["errors"][ticker] = str(erro)

    os.makedirs("data", exist_ok=True)

    with open("data/latest.json", "w", encoding="utf-8") as arquivo:
        json.dump(
            saida,
            arquivo,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )

    print("Arquivo data/latest.json criado.")


if __name__ == "__main__":
    main()
