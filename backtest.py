"""Backtest conservador do motor determinístico do Radar v3.

- Sinal calculado apenas com candle diário já fechado.
- Entrada no OPEN do candle seguinte (evita look-ahead).
- Se stop e alvo forem tocados no mesmo candle, assume STOP primeiro.
- Slippage configurável nas duas pontas.
- Uma posição por ativo de cada vez.
- Separação cronológica 70/30 para relatório out-of-sample.

O resultado é evidência experimental, não garantia de desempenho futuro.
"""
import argparse
import json
import math
import os
from datetime import datetime, timezone

import pandas as pd

import collector
from strategy_engine import ENGINE_VERSION, evaluate_daily


UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "AMZN", "META", "TSLA",
    "JPM", "XOM", "WMT", "SPY", "QQQ", "IWM", "GLD",
    "PETR4.SA", "VALE3.SA", "ITUB4.SA", "BOVA11.SA",
    "BTC-USD", "ETH-USD", "XRP-USD", "SOL-USD",
]

SETUP_PRIORITY = {"trend_breakout": 0, "trend_pullback": 1}


def _safe(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def snapshot_at(ind, i):
    row = ind.iloc[i]
    close = _safe(row.get("close"))
    e20 = _safe(row.get("ema20"))
    e50 = _safe(row.get("ema50"))
    e200 = _safe(row.get("ema200"))
    high_prev = _safe(row.get("donchian_high20_prev"))
    low_prev = _safe(row.get("donchian_low20_prev"))

    if close is not None and high_prev is not None and close > high_prev:
        breakout = "up"
    elif close is not None and low_prev is not None and close < low_prev:
        breakout = "down"
    else:
        breakout = "none"

    return {
        "time": ind.index[i].isoformat(),
        "close": close,
        "ema20": e20,
        "ema50": e50,
        "ema200": e200,
        "rsi14": _safe(row.get("rsi14")),
        "rsi_delta1": _safe(row.get("rsi_delta1")),
        "macd_hist": _safe(row.get("macd_hist")),
        "macd_hist_delta1": _safe(row.get("macd_hist_delta1")),
        "atr14": _safe(row.get("atr14")),
        "adx14": _safe(row.get("adx14")),
        "plus_di14": _safe(row.get("plus_di14")),
        "minus_di14": _safe(row.get("minus_di14")),
        "cmf20": _safe(row.get("cmf20")),
        "volume_ratio": _safe(row.get("volume_ratio")),
        "donchian_breakout20": breakout,
        "distance_ema20_pct": (
            (close / e20 - 1.0) * 100.0
            if close is not None and e20 not in (None, 0)
            else None
        ),
    }


def simulate_trade(ind, signal_i, setup, max_hold=10, slippage_bps=5.0):
    if signal_i + 1 >= len(ind):
        return None

    direction = setup["direction"]
    atr = _safe(ind.iloc[signal_i].get("atr14"))
    next_open = _safe(ind.iloc[signal_i + 1].get("open"))
    if atr is None or atr <= 0 or next_open is None or next_open <= 0:
        return None

    slip = slippage_bps / 10000.0
    entry = next_open * (1.0 + slip if direction == "long" else 1.0 - slip)
    stop_dist = setup["stop_atr_mult"] * atr
    target_dist = setup["target_atr_mult"] * atr
    if direction == "long":
        stop = entry - stop_dist
        target = entry + target_dist
    else:
        stop = entry + stop_dist
        target = entry - target_dist

    last_i = min(len(ind) - 1, signal_i + max_hold)
    exit_i = None
    exit_px = None
    outcome = "timeout"

    for j in range(signal_i + 1, last_i + 1):
        high = _safe(ind.iloc[j].get("high"))
        low = _safe(ind.iloc[j].get("low"))
        if high is None or low is None:
            continue

        if direction == "long":
            hit_stop = low <= stop
            hit_target = high >= target
        else:
            hit_stop = high >= stop
            hit_target = low <= target

        # Conservador: se não sabemos a ordem intrabar, assume stop primeiro.
        if hit_stop:
            exit_i = j
            exit_px = stop
            outcome = "stop"
            break
        if hit_target:
            exit_i = j
            exit_px = target
            outcome = "target"
            break

    if exit_i is None:
        exit_i = last_i
        exit_px = _safe(ind.iloc[exit_i].get("close"))
        if exit_px is None:
            return None

    # Slippage também na saída.
    if direction == "long":
        exit_exec = exit_px * (1.0 - slip)
        pnl = exit_exec - entry
    else:
        exit_exec = exit_px * (1.0 + slip)
        pnl = entry - exit_exec

    r_mult = pnl / stop_dist if stop_dist > 0 else None
    if r_mult is None or not math.isfinite(r_mult):
        return None

    return {
        "entry_time": ind.index[signal_i + 1].isoformat(),
        "exit_time": ind.index[exit_i].isoformat(),
        "entry": round(entry, 8),
        "exit": round(exit_exec, 8),
        "stop": round(stop, 8),
        "target": round(target, 8),
        "r": round(r_mult, 5),
        "outcome": outcome,
        "exit_i": exit_i,
    }


def backtest_symbol(symbol, raw, split_ratio=0.70, max_hold=10, slippage_bps=5.0):
    if raw is None or raw.empty or len(raw) < 260:
        return []

    ind = collector.indicadores(raw.copy().sort_index())
    split_i = int(len(ind) * split_ratio)
    trades = []
    i = 220

    while i < len(ind) - 1:
        snap = snapshot_at(ind, i)
        candidates = [s for s in evaluate_daily(snap) if s.get("daily_trigger")]
        if not candidates:
            i += 1
            continue

        candidates.sort(key=lambda s: SETUP_PRIORITY.get(s["setup"], 99))
        setup = candidates[0]
        trade = simulate_trade(
            ind, i, setup, max_hold=max_hold, slippage_bps=slippage_bps
        )
        if not trade:
            i += 1
            continue

        trade.update({
            "symbol": symbol,
            "signal_time": ind.index[i].isoformat(),
            "setup": setup["setup"],
            "direction": setup["direction"],
            "sample": "OOS" if i >= split_i else "IS",
        })
        trades.append(trade)
        i = max(i + 1, trade["exit_i"] + 1)

    return trades


def metrics(trades):
    rs = [float(t["r"]) for t in trades]
    n = len(rs)
    if not n:
        return {
            "trades": 0, "win_rate": None, "avg_r": None,
            "profit_factor": None, "total_r": 0.0, "max_drawdown_r": 0.0,
        }

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else None

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 4),
        "avg_r": round(sum(rs) / n, 4),
        "profit_factor": round(pf, 4) if pf is not None else None,
        "total_r": round(sum(rs), 4),
        "max_drawdown_r": round(max_dd, 4),
    }


def grouped_report(trades, sample=None):
    base = [t for t in trades if sample is None or t["sample"] == sample]
    out = {"ALL": metrics(base)}
    keys = sorted({(t["setup"], t["direction"]) for t in base})
    for setup, direction in keys:
        name = f"{setup}:{direction}"
        out[name] = metrics([
            t for t in base
            if t["setup"] == setup and t["direction"] == direction
        ])
    return out


def gate(oos_metrics):
    """Gate mínimo para considerar um setup candidato a estudos adicionais.

    Não promove automaticamente para dinheiro real.
    """
    decisions = {}
    for key, m in oos_metrics.items():
        if key == "ALL":
            continue
        pf = m.get("profit_factor")
        passed = (
            m.get("trades", 0) >= 20
            and m.get("avg_r") is not None and m["avg_r"] > 0
            and pf is not None and pf >= 1.15
            and m.get("max_drawdown_r", -999) >= -12.0
        )
        decisions[key] = {
            "status": "PASS_CANDIDATE" if passed else "FAIL_OR_INSUFFICIENT",
            "criteria": "OOS trades>=20; avg_r>0; PF>=1.15; maxDD>=-12R",
        }
    return decisions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/backtest_v3.json")
    parser.add_argument("--period", default="5y")
    parser.add_argument("--max-hold", type=int, default=10)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    args = parser.parse_args()

    downloaded = collector.batch_yahoo(UNIVERSE, period=args.period, interval="1d")
    all_trades = []
    errors = {}
    for symbol in UNIVERSE:
        raw = downloaded.get(symbol)
        if raw is None or raw.empty:
            errors[symbol] = "sem dados"
            continue
        try:
            all_trades.extend(backtest_symbol(
                symbol, raw, max_hold=args.max_hold,
                slippage_bps=args.slippage_bps
            ))
        except Exception as exc:
            errors[symbol] = str(exc)

    oos = grouped_report(all_trades, sample="OOS")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": ENGINE_VERSION,
        "method": {
            "signal": "candle diario fechado",
            "entry": "open do candle seguinte",
            "same_bar_stop_target": "stop-first (conservador)",
            "split": "70% IS / 30% OOS cronologico por ativo",
            "max_hold_bars": args.max_hold,
            "slippage_bps_each_side": args.slippage_bps,
            "overlap": "uma posicao por ativo de cada vez",
        },
        "universe_requested": UNIVERSE,
        "universe_downloaded": sorted(downloaded.keys()),
        "errors": errors,
        "metrics_all": grouped_report(all_trades),
        "metrics_oos": oos,
        "gate_oos": gate(oos),
        "trades_count": len(all_trades),
        "warning": (
            "Backtest experimental. Um PASS_CANDIDATE exige validacao adicional "
            "em universo maior, outros regimes e custos reais antes de dinheiro real."
        ),
    }

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)

    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
