import pandas as pd
from .indicators import ma, rsi, atr
from .config import settings

# 打分权重
class FactorWeights:
    TREND = 0.30
    VOLUME = 0.20
    RELSTRENGTH = 0.20
    CATALYST = 0.15
    ONCHAIN = 0.15

def trend_score(df: pd.DataFrame) -> float:
    close = df["close"]
    ma50 = ma(close, 50)
    ma200 = ma(close, 200)
    cond = (close.iloc[-1] > (ma50.iloc[-1] or close.iloc[-1])) + (close.iloc[-1] > (ma200.iloc[-1] or close.iloc[-1]))
    peak = close.rolling(60).max().iloc[-1]
    dd = (peak - close.iloc[-1]) / max(peak, 1e-9)
    dd_score = max(0.0, 1.0 - min(dd, 0.3) / 0.3)
    base = {0: 40, 1: 65, 2: 85}[cond]
    return float(min(100, base * 0.7 + dd_score * 30))

def volume_score(df: pd.DataFrame) -> float:
    v = df["volume"]
    m7 = v.rolling(7).mean().iloc[-1]
    m90 = v.rolling(90).mean().iloc[-1]
    if not (m7 and m90):
        return 50.0
    ratio = m7 / m90
    if ratio >= 1.5: return 90.0
    if ratio >= 1.2: return 75.0
    if ratio >= 1.0: return 65.0
    if ratio >= 0.8: return 50.0
    return 35.0

def rel_strength_score(symbol: str, df: pd.DataFrame, bench: pd.DataFrame | None) -> float:
    if bench is None:
        return 60.0
    r = (df["close"].pct_change(7).iloc[-1] or 0) - (bench["close"].pct_change(7).iloc[-1] or 0)
    if r >= 0.10: return 90.0
    if r >= 0.05: return 75.0
    if r >= 0.00: return 65.0
    if r >= -0.03: return 50.0
    return 35.0

def catalyst_score(symbol: str) -> float:
    return 60.0  # 预留：新闻/上新事件

def onchain_score(symbol: str) -> float:
    return 60.0  # 预留：链上活跃

def total_score(symbol: str, df: pd.DataFrame, bench: pd.DataFrame | None) -> dict:
    s_trend = trend_score(df)
    s_vol = volume_score(df)
    s_rel = rel_strength_score(symbol, df, bench)
    s_cat = catalyst_score(symbol)
    s_onc = onchain_score(symbol)
    total = (s_trend * FactorWeights.TREND +
             s_vol * FactorWeights.VOLUME +
             s_rel * FactorWeights.RELSTRENGTH +
             s_cat * FactorWeights.CATALYST +
             s_onc * FactorWeights.ONCHAIN)
    return {
        "score_total": round(total, 2),
        "score_trend": s_trend,
        "score_volume": s_vol,
        "score_rel_strength": s_rel,
        "score_catalyst": s_cat,
        "score_onchain": s_onc,
    }

# —— 三档风格参数 —— #
def _thresholds(mode: str) -> dict:
    m = (mode or "balanced").lower()
    if m == "conservative":
        return {
            "buy_min_score": 75,
            "breakout_min_score": 85,
            "breakout_window": 30,
            "chase_block_ret7": 0.25,  # 近7根涨幅≥25%则不追
            "spread_max_pct": 0.5,
            "need_ma200": True,        # 价格需优于MA200
        }
    if m == "aggressive":
        return {
            "buy_min_score": 65,
            "breakout_min_score": 75,
            "breakout_window": 20,
            "chase_block_ret7": 0.50,
            "spread_max_pct": 1.0,
            "need_ma200": False,       # 不强制要求MA200
        }
    # balanced
    return {
        "buy_min_score": 70,
        "breakout_min_score": 80,
        "breakout_window": 25,
        "chase_block_ret7": 0.35,
        "spread_max_pct": 0.7,
        "need_ma200": False,          # 优先，但不强制
    }

# —— 中文操作建议（支持三档风格） —— #
def decide_action_cn(df: pd.DataFrame, score_total: float, avg_spread_pct: float | None, mode: str | None = None) -> tuple[str, str]:
    """
    返回 (action_cn, reason_cn)
    action_cn ∈ {"突破买点", "建议买入", "建议观察", "建议回避"}
    """
    if df is None or len(df) < 60:
        return "建议观察", "样本不足，等待更多K线"

    cfg = _thresholds(mode or getattr(settings, "STRATEGY_MODE", "balanced"))

    close = df["close"]
    last = float(close.iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    # 近7根涨幅（1h周期下约近7小时）——防追高
    try:
        ret7 = float((close.iloc[-1] - close.iloc[-7]) / close.iloc[-7]) if len(close) >= 7 else 0.0
    except Exception:
        ret7 = 0.0

    # 是否突破“阶段高点”
    try:
        win = cfg["breakout_window"]
        if len(close) <= win:
            win = max(5, len(close) - 1)
        prev_max = float(close.iloc[-(win+1):-1].max()) if len(close) > win else float(close.max())
        is_breakout = last > prev_max * 1.001  # +0.1%容差
    except Exception:
        is_breakout = False

    # 点差过大 → 回避
    if avg_spread_pct is not None and avg_spread_pct > cfg["spread_max_pct"]:
        return "建议回避", f"点差偏大（≈{avg_spread_pct:.2f}%），交易成本高"

    above_ma50 = last >= ma50 if ma50 else False
    above_ma200 = (ma200 is not None and last >= ma200) if ma200 else False

    # 突破买点（高分+突破+至少站稳MA50）
    if score_total >= cfg["breakout_min_score"] and is_breakout and above_ma50:
        if cfg["need_ma200"] and not above_ma200 and ma200 is not None:
            pass  # 若保守档要求MA200，则继续往下判
        else:
            return "突破买点", "突破阶段高点且量能/强度良好，短期机会"

    # 建议买入（高分+均线结构正向）
    if score_total >= cfg["buy_min_score"] and above_ma50 and (above_ma200 or (not cfg["need_ma200"]) or ma200 is None):
        if ret7 >= cfg["chase_block_ret7"]:
            return "建议观察", "短期涨幅较大，谨慎追高，等待回踩确认"
        return "建议买入", "趋势健康、价格站稳关键均线，可小仓位试探"

    # 中性：建议观察
    if score_total >= 60:
        return "建议观察", "趋势待确认或量能不足，继续跟踪"

    # 其它：建议回避
    return "建议回避", "分数偏低或趋势走弱，暂不参与"
