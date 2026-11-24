"""Pesquisa controlada de regras de breakout para o Radar.

Fluxo:
1. Compara poucas variantes pré-definidas nos conjuntos development + validation.
2. Só uma variante que passe os DOIS conjuntos pode ser selecionada.
3. O holdout final, composto por ativos diferentes, só é consultado se houver seleção.
4. Stop/alvo/horizonte/slippage permanecem fixos durante a seleção.

Isso reduz, mas não elimina, risco de data snooping.
"""
import argparse
import json
import os
from datetime import datetime, timezone

import pandas as pd

import collector
from backtest import (
    DEVELOPMENT_UNIVERSE,
    VALIDATION_UNIVERSE,
    breadth_report,
    metrics,
    simulate_trade,
)


FINAL_HOLDOUT_UNIVERSE = [
    "SNOW", "DDOG", "NET", "UBER", "ABNB", "COIN", "HOOD", "SQ", "SOFI",
    "VRTX", "ISRG", "MDT", "SYK", "CVS", "CI", "HCA", "EOG", "PSX", "HAL",
    "BKR", "DVN", "FANG", "ROST", "CMG", "DPZ", "ORLY", "LULU", "GE", "GD",
    "PH", "PWR", "EMR", "TMUS", "SPOT", "PG", "PM", "CL", "MDLZ", "DUK",
    "AEP", "ENEV3.SA", "EQTL3.SA", "HAPV3.SA", "KLBN11.SA", "LREN3.SA",
    "MGLU3.SA", "RADL3.SA", "RDOR3.SA", "TAEE11.SA", "TIMS3.SA",
    "UGPA3.SA", "VBBR3.SA",
]

VARIANTS = [
    "current_filtered",
    "ema_trend",
    "sma200_trend",
    "adx_trend",
    "adx_no_volume",
    "simple_breakout",
]


def _v(row, key):
    try:
        x = row.get(key)
        if x is None or pd.isna(x):
            return None
        return float(x)
    except Exception:
        return None


def signal_for_variant(ind, i, variant):
    row = ind.iloc[i]
    close = _v(row, "close")
    ema20 = _v(row, "ema20")
    ema50 = _v(row, "ema50")
    ema200 = _v(row, "ema200")
    sma200 = _v(row, "sma200")
    rsi = _v(row, "rsi14")
    adx = _v(row, "adx14")
    pdi = _v(row, "plus_di14")
    mdi = _v(row, "minus_di14")
    cmf = _v(row, "cmf20")
    vr = _v(row, "volume_ratio")
    high_prev = _v(row, "donchian_high20_prev")

    if close is None or high_prev is None or close <= high_prev:
        return None

    vol_ok = vr is not None and vr >= 1.0
    ema_trend = (
        close is not None and ema50 is not None and ema200 is not None
        and close > ema50 > ema200
    )
    adx_ok = (
        adx is not None and adx >= 20
        and pdi is not None and mdi is not None and pdi > mdi
    )

    if variant == "current_filtered":
        ok = (
            ema20 is not None and close > ema20
            and ema50 is not None and ema200 is not None
            and ema20 > ema50 > ema200
            and adx_ok and vol_ok
            and rsi is not None and 45 <= rsi <= 78
            and (cmf is None or cmf >= -0.10)
        )
    elif variant == "ema_trend":
        ok = ema_trend and vol_ok
    elif variant == "sma200_trend":
        ok = sma200 is not None and close > sma200 and vol_ok
    elif variant == "adx_trend":
        ok = ema_trend and adx_ok and vol_ok
    elif variant == "adx_no_volume":
        ok = ema_trend and adx_ok
    elif variant == "simple_breakout":
        ok = vol_ok
    else:
        raise ValueError(f"variante desconhecida: {variant}")

    if not ok:
        return None

    return {
        "setup": variant,
        "direction": "long",
        "stop_atr_mult": 1.5,
        "target_atr_mult": 3.0,
    }


def trades_for_symbol(symbol, raw, variant, split_ratio, max_hold, slippage_bps):
    if raw is None or raw.empty or len(raw) < 260:
        return []
    ind = collector.indicadores(raw.copy().sort_index())
    split_i = int(len(ind) * split_ratio)
    out = []
    i = 220
    while i < len(ind) - 1:
        setup = signal_for_variant(ind, i, variant)
        if setup is None:
            i += 1
            continue
        trade = simulate_trade(
            ind, i, setup, max_hold=max_hold, slippage_bps=slippage_bps
        )
        if trade is None:
            i += 1
            continue
        trade.update({
            "symbol": symbol,
            "setup": variant,
            "direction": "long",
            "sample": "OOS" if i >= split_i else "IS",
            "signal_time": ind.index[i].isoformat(),
        })
        out.append(trade)
        i = max(i + 1, trade["exit_i"] + 1)
    return out


def evaluate(universe, downloaded, variant, split_ratio, max_hold, slippage_bps):
    trades = []
    errors = {}
    for symbol in universe:
        raw = downloaded.get(symbol)
        if raw is None or raw.empty:
            errors[symbol] = "sem dados"
            continue
        try:
            trades.extend(trades_for_symbol(
                symbol, raw, variant, split_ratio, max_hold, slippage_bps
            ))
        except Exception as exc:
            errors[symbol] = str(exc)

    full = metrics(trades)
    oos_trades = [t for t in trades if t["sample"] == "OOS"]
    oos = metrics(oos_trades)
    bfull = breadth_report(trades).get(f"{variant}:long", {})
    boos = breadth_report(oos_trades).get(f"{variant}:long", {})
    return {
        "metrics_full": full,
        "metrics_oos": oos,
        "breadth_full": bfull,
        "breadth_oos": boos,
        "errors": errors,
    }


def research_pass(result):
    f = result["metrics_full"]
    o = result["metrics_oos"]
    bf = result["breadth_full"]
    bo = result["breadth_oos"]
    return (
        f.get("trades", 0) >= 200
        and o.get("trades", 0) >= 60
        and f.get("avg_r") is not None and f["avg_r"] > 0
        and o.get("avg_r") is not None and o["avg_r"] > 0
        and f.get("profit_factor") is not None and f["profit_factor"] >= 1.10
        and o.get("profit_factor") is not None and o["profit_factor"] >= 1.10
        and bf.get("symbols_with_min_trades", 0) >= 20
        and bo.get("symbols_with_min_trades", 0) >= 20
        and bf.get("positive_share") is not None and bf["positive_share"] >= 0.50
        and bo.get("positive_share") is not None and bo["positive_share"] >= 0.50
        and bf.get("median_symbol_avg_r") is not None and bf["median_symbol_avg_r"] > 0
        and bo.get("median_symbol_avg_r") is not None and bo["median_symbol_avg_r"] > 0
    )


def robustness_floor(a, b):
    vals = []
    for result in (a, b):
        for scope in ("metrics_full", "metrics_oos"):
            pf = result[scope].get("profit_factor")
            if pf is None:
                return -999.0
            vals.append(pf)
    return min(vals)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/research_v4.json")
    parser.add_argument("--period", default="10y")
    parser.add_argument("--max-hold", type=int, default=10)
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    args = parser.parse_args()

    dev_data = collector.batch_yahoo(
        DEVELOPMENT_UNIVERSE, period=args.period, interval="1d"
    )
    val_data = collector.batch_yahoo(
        VALIDATION_UNIVERSE, period=args.period, interval="1d"
    )

    research = {}
    eligible = []
    for variant in VARIANTS:
        dev = evaluate(
            DEVELOPMENT_UNIVERSE, dev_data, variant, 0.70,
            args.max_hold, args.slippage_bps
        )
        val = evaluate(
            VALIDATION_UNIVERSE, val_data, variant, 0.70,
            args.max_hold, args.slippage_bps
        )
        passed = research_pass(dev) and research_pass(val)
        floor = robustness_floor(dev, val)
        research[variant] = {
            "development": dev,
            "validation": val,
            "passes_both_research_sets": passed,
            "robustness_pf_floor": round(floor, 4),
        }
        if passed:
            eligible.append((floor, variant))

    eligible.sort(reverse=True)
    selected = eligible[0][1] if eligible else None

    # O holdout final permanece intocado quando nenhuma regra passa a pesquisa.
    final = None
    if selected is not None:
        final_data = collector.batch_yahoo(
            FINAL_HOLDOUT_UNIVERSE, period=args.period, interval="1d"
        )
        final = evaluate(
            FINAL_HOLDOUT_UNIVERSE, final_data, selected, 0.70,
            args.max_hold, args.slippage_bps
        )
        final["passes_final_holdout"] = research_pass(final)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": {
            "variants_tested": VARIANTS,
            "stop_atr_mult": 1.5,
            "target_atr_mult": 3.0,
            "max_hold_bars": args.max_hold,
            "slippage_bps_each_side": args.slippage_bps,
            "selection": (
                "exige aprovacao independente em development e validation; "
                "seleciona maior piso de profit factor entre FULL/OOS"
            ),
            "final_holdout_policy": (
                "nao consultar holdout final se nenhuma variante passar a pesquisa"
            ),
        },
        "research": research,
        "selected_variant": selected,
        "final_holdout": final,
        "conclusion": (
            "CANDIDATE_SURVIVED_FINAL"
            if final and final.get("passes_final_holdout")
            else "CANDIDATE_FAILED_FINAL"
            if final
            else "NO_RESEARCH_VARIANT_QUALIFIED"
        ),
    }

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
