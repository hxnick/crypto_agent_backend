import pandas as pd
from .indicators import ma, rsi, atr

class FactorWeights:
    TREND = 0.30
    VOLUME = 0.20
    RELSTRENGTH = 0.20
    CATALYST = 0.15
    ONCHAIN = 0.15

def trend_score(df: pd.DataFrame) -> float:
    close = df['close']
    ma50 = ma(close, 50)
    ma200 = ma(close, 200)
    cond = (close.iloc[-1] > (ma50.iloc[-1] or close.iloc[-1])) + (close.iloc[-1] > (ma200.iloc[-1] or close.iloc[-1]))
    peak = close.rolling(60).max().iloc[-1]
    dd = (peak - close.iloc[-1]) / max(peak, 1e-9)
    dd_score = max(0.0, 1.0 - min(dd, 0.3) / 0.3)
    base = {0:40, 1:65, 2:85}[cond]
    return float(min(100, base * 0.7 + dd_score * 30))

def volume_score(df: pd.DataFrame) -> float:
    v = df['volume']
    m7 = v.rolling(7).mean().iloc[-1]
    m90 = v.rolling(90).mean().iloc[-1]
    if not (m7 and m90): return 50.0
    ratio = m7 / m90
    if ratio >= 1.5: return 90.0
    if ratio >= 1.2: return 75.0
    if ratio >= 1.0: return 65.0
    if ratio >= 0.8: return 50.0
    return 35.0

def rel_strength_score(symbol: str, df: pd.DataFrame, bench: pd.DataFrame | None) -> float:
    if bench is None: return 60.0
    r = (df['close'].pct_change(7).iloc[-1] or 0) - (bench['close'].pct_change(7).iloc[-1] or 0)
    if r >= 0.10: return 90.0
    if r >= 0.05: return 75.0
    if r >= 0.00: return 65.0
    if r >= -0.03: return 50.0
    return 35.0

def catalyst_score(symbol: str) -> float: return 60.0
def onchain_score(symbol: str) -> float: return 60.0

def total_score(symbol: str, df: pd.DataFrame, bench: pd.DataFrame | None) -> dict:
    s_trend = trend_score(df)
    s_vol = volume_score(df)
    s_rel = rel_strength_score(symbol, df, bench)
    s_cat = catalyst_score(symbol)
    s_onc = onchain_score(symbol)
    total = (s_trend*FactorWeights.TREND + s_vol*FactorWeights.VOLUME + s_rel*FactorWeights.RELSTRENGTH + s_cat*FactorWeights.CATALYST + s_onc*FactorWeights.ONCHAIN)
    return {
        "score_total": round(total, 2),
        "score_trend": s_trend,
        "score_volume": s_vol,
        "score_rel_strength": s_rel,
        "score_catalyst": s_cat,
        "score_onchain": s_onc,
    }
