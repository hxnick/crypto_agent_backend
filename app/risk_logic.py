import math
import pandas as pd

def _atr(df: pd.DataFrame, period: int = 14) -> float | None:
    if len(df) < period + 2:
        return None
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])

def compute_dynamic_advice(df: pd.DataFrame, entry: float, last: float) -> dict:
    """
    动态生成止损/止盈与中文建议（无需用户给百分比）。
    规则（长多单）：
      - 动态止损：max( 最近20低点, last - 1.8*ATR, 0.97*MA50, 0.94*MA200[可选] )，取 < last 的最大者
      - 动态止盈：min( 最近20高点*1.01, last + 1.8*ATR )，取 > last 的最小者
      - 趋势判断：MA50/MA200 位置辅助给出“减仓/观察”等口径
    """
    ma50 = float(df["close"].rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
    ma200 = float(df["close"].rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
    atr = _atr(df, 14) or (float(df["close"].pct_change().rolling(14).std().iloc[-1]) * float(last)) if len(df) >= 20 else None

    swing_low20  = float(df["low"].tail(20).min())
    swing_high20 = float(df["high"].tail(20).max())

    # --- 动态止损 ---
    sl_candidates = [swing_low20, (last - 1.8 * atr) if atr else None]
    if ma50:  sl_candidates.append(0.97 * ma50)
    if ma200: sl_candidates.append(0.94 * ma200)
    sl_candidates = [c for c in sl_candidates if c is not None and c < last]
    stop_loss_price = max(sl_candidates) if sl_candidates else (entry * 0.92)

    # --- 动态止盈 ---
    tp_candidates = []
    if atr: tp_candidates.append(last + 1.8 * atr)
    if swing_high20: tp_candidates.append(swing_high20 * 1.01)
    tp_candidates = [c for c in tp_candidates if c is not None and c > last]
    take_profit_price = min(tp_candidates) if tp_candidates else (entry * 1.06)

    # --- 行动口径 ---
    if last <= stop_loss_price:
        action = "卖出"
        reason = "触发动态止损，优先保护本金"
    elif last >= take_profit_price:
        action = "分批止盈"
        reason = "达到动态止盈目标，建议分批落袋"
    else:
        signals = []
        action = "观察"
        if ma50 and last < ma50:
            action = "减仓"
            signals.append(f"跌破MA50≈{ma50:.4f}")
        if ma200 and last < ma200:
            action = "减仓"
            signals.append(f"低于MA200≈{ma200:.4f}")
        reason = "；".join(signals) if signals else "趋势未变，继续跟踪"

    out = {
        "stop_loss_price": round(float(stop_loss_price), 6) if stop_loss_price else None,
        "take_profit_price": round(float(take_profit_price), 6) if take_profit_price else None,
        "ma50": round(ma50, 6) if ma50 else None,
        "ma200": round(ma200, 6) if ma200 else None,
        "action": action,
        "reason": reason,
    }
    return out
