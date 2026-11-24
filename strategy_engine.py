"""Motor determinístico de setups do Radar v3.

Este módulo não prevê retorno e não usa GPT. Ele apenas codifica regras explícitas,
auditáveis e testáveis. Os estados ARMED/TRIGGERED são sinais técnicos, não ordens.
"""

ENGINE_VERSION = "3.0.0-experimental"


def _n(x):
    return isinstance(x, (int, float))


def _gt(a, b):
    return _n(a) and _n(b) and a > b


def _lt(a, b):
    return _n(a) and _n(b) and a < b


def _between(x, lo, hi):
    return _n(x) and lo <= x <= hi


def _risk_plan(direction, entry, atr, stop_mult, target_mult):
    if not (_n(entry) and _n(atr) and atr > 0):
        return None
    risk = stop_mult * atr
    reward = target_mult * atr
    if direction == "long":
        stop = entry - risk
        target = entry + reward
    else:
        stop = entry + risk
        target = entry - reward
    return {
        "entry_reference": round(entry, 8),
        "stop_reference": round(stop, 8),
        "target_reference": round(target, 8),
        "stop_atr_mult": stop_mult,
        "target_atr_mult": target_mult,
        "rr_reference": round(reward / risk, 3),
    }


def evaluate_daily(d1):
    """Retorna setups diários armados/gatilhados a partir de um snapshot 1D."""
    if not d1:
        return []

    close = d1.get("close")
    e20 = d1.get("ema20")
    e50 = d1.get("ema50")
    e200 = d1.get("ema200")
    rsi = d1.get("rsi14")
    rsi_d = d1.get("rsi_delta1")
    hist_d = d1.get("macd_hist_delta1")
    adx = d1.get("adx14")
    pdi = d1.get("plus_di14")
    mdi = d1.get("minus_di14")
    cmf = d1.get("cmf20")
    vr = d1.get("volume_ratio")
    breakout = d1.get("donchian_breakout20")
    dist20 = d1.get("distance_ema20_pct")

    setups = []

    # 1) Rompimento a favor da tendência.
    long_ctx = (
        _gt(close, e20) and _gt(e20, e50) and _gt(e50, e200)
        and _n(adx) and adx >= 20
        and _gt(pdi, mdi)
        and _between(rsi, 45, 78)
        and (not _n(cmf) or cmf >= -0.10)
    )
    if long_ctx:
        trigger = breakout == "up" and _n(vr) and vr >= 1.0
        setups.append({
            "setup": "trend_breakout",
            "direction": "long",
            "daily_state": "TRIGGERED" if trigger else "ARMED",
            "daily_trigger": trigger,
            "reasons": [
                "close>EMA20>EMA50>EMA200",
                "ADX>=20 e +DI>-DI",
                "RSI em faixa de tendência",
                "Donchian20 rompido com volume>=média" if trigger
                else "aguardando rompimento Donchian20 com volume>=média",
            ],
            "stop_atr_mult": 1.5,
            "target_atr_mult": 3.0,
        })

    short_ctx = (
        _lt(close, e20) and _lt(e20, e50) and _lt(e50, e200)
        and _n(adx) and adx >= 20
        and _gt(mdi, pdi)
        and _between(rsi, 22, 55)
        and (not _n(cmf) or cmf <= 0.10)
    )
    if short_ctx:
        trigger = breakout == "down" and _n(vr) and vr >= 1.0
        setups.append({
            "setup": "trend_breakout",
            "direction": "short",
            "daily_state": "TRIGGERED" if trigger else "ARMED",
            "daily_trigger": trigger,
            "reasons": [
                "close<EMA20<EMA50<EMA200",
                "ADX>=20 e -DI>+DI",
                "RSI em faixa de tendência baixista",
                "Donchian20 rompido com volume>=média" if trigger
                else "aguardando rompimento Donchian20 com volume>=média",
            ],
            "stop_atr_mult": 1.5,
            "target_atr_mult": 3.0,
        })

    # 2) Pullback para EMA20 dentro de tendência já estabelecida.
    long_pb_ctx = (
        _gt(close, e50) and _gt(e50, e200)
        and _n(adx) and adx >= 18
        and _gt(pdi, mdi)
        and _between(rsi, 40, 65)
        and _between(dist20, -2.5, 1.5)
        and breakout != "down"
        and (not _n(cmf) or cmf >= -0.15)
    )
    if long_pb_ctx:
        trigger = _gt(close, e20) and _n(hist_d) and hist_d > 0 and _n(rsi_d) and rsi_d > 0
        setups.append({
            "setup": "trend_pullback",
            "direction": "long",
            "daily_state": "TRIGGERED" if trigger else "ARMED",
            "daily_trigger": trigger,
            "reasons": [
                "tendência principal acima de EMA50/EMA200",
                "preço próximo da EMA20",
                "ADX>=18 e +DI>-DI",
                "momento retomando alta" if trigger else "aguardando retomada de MACD/RSI",
            ],
            "stop_atr_mult": 1.25,
            "target_atr_mult": 2.0,
        })

    short_pb_ctx = (
        _lt(close, e50) and _lt(e50, e200)
        and _n(adx) and adx >= 18
        and _gt(mdi, pdi)
        and _between(rsi, 35, 60)
        and _between(dist20, -1.5, 2.5)
        and breakout != "up"
        and (not _n(cmf) or cmf <= 0.15)
    )
    if short_pb_ctx:
        trigger = _lt(close, e20) and _n(hist_d) and hist_d < 0 and _n(rsi_d) and rsi_d < 0
        setups.append({
            "setup": "trend_pullback",
            "direction": "short",
            "daily_state": "TRIGGERED" if trigger else "ARMED",
            "daily_trigger": trigger,
            "reasons": [
                "tendência principal abaixo de EMA50/EMA200",
                "preço próximo da EMA20",
                "ADX>=18 e -DI>+DI",
                "momento retomando baixa" if trigger else "aguardando retomada de MACD/RSI",
            ],
            "stop_atr_mult": 1.25,
            "target_atr_mult": 2.0,
        })

    return setups


def intraday_confirms(direction, intra):
    """Confirmação simples do timing; não cria o setup por conta própria."""
    if not intra:
        return False, ["intraday ausente"]

    close = intra.get("close")
    e20 = intra.get("ema20")
    rsi = intra.get("rsi14")
    hist_d = intra.get("macd_hist_delta1")
    pdi = intra.get("plus_di14")
    mdi = intra.get("minus_di14")

    if direction == "long":
        checks = [
            (_gt(close, e20), "close intraday > EMA20"),
            (_n(hist_d) and hist_d > 0, "histograma MACD acelerando alta"),
            (_n(rsi) and rsi >= 48, "RSI intraday >=48"),
            (_gt(pdi, mdi), "+DI intraday > -DI"),
        ]
    else:
        checks = [
            (_lt(close, e20), "close intraday < EMA20"),
            (_n(hist_d) and hist_d < 0, "histograma MACD acelerando baixa"),
            (_n(rsi) and rsi <= 52, "RSI intraday <=52"),
            (_gt(mdi, pdi), "-DI intraday > +DI"),
        ]

    passed = sum(1 for ok, _ in checks if ok)
    # 3 de 4 evita que uma única oscilação de indicador mate um setup já gatilhado no diário.
    return passed >= 3, [label for ok, label in checks if ok]


def evaluate_asset(d1, intraday=None):
    """Combina setup diário e confirmação intraday.

    Estados:
      ARMED: contexto existe, gatilho diário ainda não.
      DAILY_TRIGGER_PENDING_INTRADAY: gatilho diário ocorreu, intraday não confirmou.
      DAILY_TRIGGER_NO_INTRADAY: gatilho diário ocorreu, sem intraday disponível.
      TRIGGERED: gatilho diário + confirmação intraday.
    """
    out = []
    for setup in evaluate_daily(d1):
        item = dict(setup)
        if not setup["daily_trigger"]:
            item["state"] = "ARMED"
            item["intraday_confirmed"] = False
            item["risk_plan"] = None
            out.append(item)
            continue

        if not intraday:
            item["state"] = "DAILY_TRIGGER_NO_INTRADAY"
            item["intraday_confirmed"] = False
            item["intraday_reasons"] = ["intraday ausente"]
            entry = d1.get("close") if d1 else None
            atr = d1.get("atr14") if d1 else None
        else:
            ok, reasons = intraday_confirms(setup["direction"], intraday)
            item["intraday_confirmed"] = ok
            item["intraday_reasons"] = reasons
            item["state"] = "TRIGGERED" if ok else "DAILY_TRIGGER_PENDING_INTRADAY"
            entry = intraday.get("close")
            atr = intraday.get("atr14")

        item["risk_plan"] = _risk_plan(
            setup["direction"], entry, atr,
            setup["stop_atr_mult"], setup["target_atr_mult"]
        )
        out.append(item)
    return out
