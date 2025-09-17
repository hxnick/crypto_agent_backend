#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓风控推送（动态/追踪止盈止损 + 增强行情解析）
- 更稳健的 symbol 解析：多交易所（binance/okx/gate/bybit/kucoin）轮询
- 智能匹配交易对：优先 USDT，其次 USD/USDC/TUSD；支持 ATH/USDT, ATH-USDT, ATH_USDT 变体
- 若没有 USDT 报价，自动用 USD/USDC/TUSD 报价并换算成“等值USDT价”显示（用所内USDT汇率）
- 失败原因会在卡片里直说：未上市/无该交易对/被限频/网络失败

与之前版本相比，仅增强了 “行情解析 + 失败原因”。动态止盈/止损逻辑保持不变。
"""

import os, json, requests, pathlib, math
from datetime import datetime

# ========== 环境参数 ==========
FEISHU_WEBHOOK   = os.getenv("FEISHU_WEBHOOK", "").strip()
POS_FILE         = os.getenv("POS_FILE", "/root/crypto_agent_backend/config/positions.json")
STATE_FILE       = os.getenv("STATE_FILE", "/var/lib/crypto_agent/positions_state.json")
REQ_TIMEOUT      = float(os.getenv("REQ_TIMEOUT","15"))

STOP_LOSS_PCT    = float(os.getenv("STOP_LOSS_PCT", "8"))
TAKE_PROFIT_PCT  = float(os.getenv("TAKE_PROFIT_PCT", "12"))

ATR_LOOKBACK       = int(os.getenv("ATR1H_LOOKBACK", "14"))

TRAIL_ENABLE       = os.getenv("TRAIL_ENABLE", "1") == "1"
SL_TRAIL_ATR_K     = float(os.getenv("SL_TRAIL_ATR_K", "3.0"))
SL_TRAIL_MIN_PCT   = float(os.getenv("SL_TRAIL_MIN_PCT", "1.0"))

TP_TRAIL_ENABLE    = os.getenv("TP_TRAIL_ENABLE", "1") == "1"
TP_TRAIL_ATR_K     = float(os.getenv("TP_TRAIL_ATR_K", "1.5"))
TP_TRAIL_MIN_PCT   = float(os.getenv("TP_TRAIL_MIN_PCT", "0.8"))
TP_TRAIL_START_PNL = float(os.getenv("TP_TRAIL_START_PNL", "12"))

CHANGE_EPS_PCT     = float(os.getenv("CHANGE_EPS_PCT", "0.3"))

# 你也可以通过环境变量覆盖默认交易所顺序：EX_LIST="binance,okx,gate,bybit,kucoin"
EX_LIST = [x.strip() for x in os.getenv("EX_LIST","binance,okx,gate,bybit,kucoin").split(",") if x.strip()]

# ========== 交易所 ==========
import ccxt

def _build_exchange(exid):
    klass = getattr(ccxt, exid, None)
    if not klass: return None
    ex = klass({"enableRateLimit": True, "timeout": 15000})
    try:
        ex.load_markets()
    except Exception as e:
        print(f"[WARN] load_markets({exid}) failed: {e}")
    return ex

EXS = []
for exid in EX_LIST:
    ex = _build_exchange(exid)
    if ex: EXS.append(ex)

# ========== 工具 ==========
def now_str(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def safe_num(x, nd=2, default="-"):
    try:
        if x is None or (isinstance(x,float) and (math.isnan(x) or math.isinf(x))): return default
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x) if x is not None else default

def load_positions():
    if not os.path.exists(POS_FILE): return []
    try:
        with open(POS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("positions"), list):
            return data["positions"]
        if isinstance(data, list):
            return data
    except Exception as e:
        print(f"[WARN] positions.json parse error: {e}")
    return []

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] load_state error: {e}")
    return {}

def save_state(state: dict):
    p = pathlib.Path(STATE_FILE); p.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def push_feishu(markdown_text: str, title="持仓风控提醒"):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("FEISHU_WEBHOOK not set")
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag":"plain_text","content": title}},
            "elements":[{"tag":"div","text":{"tag":"lark_md","content":markdown_text}}]
        }
    }
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=REQ_TIMEOUT)
    try: body = r.json()
    except Exception: body = {}
    if not (r.status_code < 300 and body.get("code", 0) == 0):
        raise RuntimeError(f"Feishu error: {r.status_code} {str(body)[:300]}")

# ========== 行情解析增强 ==========
PREF_QUOTES = ["USDT", "USD", "USDC", "TUSD"]

def _all_market_keys(ex):
    # ccxt: ex.markets 键通常是统一符号 "BASE/QUOTE"
    return list(ex.markets.keys())

def _best_symbol_on_exchange(ex, base: str):
    """
    在单个交易所里为 base 挑选最佳报价交易对。
    优先级：USDT > USD > USDC > TUSD
    若找不到，返回 (None, reason)
    """
    base_upper = base.upper()
    candidates = []
    for key, m in ex.markets.items():
        try:
            b = m.get("base")
            q = m.get("quote")
            if not b or not q: continue
            if str(b).upper() != base_upper: continue
            if q.upper() in PREF_QUOTES:
                # 取更有流动性的（使用 market 'info' 里的成交量或 ex.fetch_ticker 再比较都可，这里先按优先级）
                candidates.append((PREF_QUOTES.index(q.upper()), key, q.upper()))
        except Exception:
            continue
    if not candidates:
        return None, f"{base}/(USDT|USD|USDC|TUSD) not found"
    candidates.sort(key=lambda x: x[0])  # quote 优先级
    return candidates[0][1], None

def _fetch_ticker(ex, symbol):
    try:
        t = ex.fetch_ticker(symbol)
        return t
    except Exception as e:
        return {"_err": f"fetch_ticker failed: {e}"}

def _fetch_ohlcv(ex, symbol, timeframe, limit):
    try:
        return ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception as e:
        return {"_err": f"fetch_ohlcv failed: {e}"}

def _get_usdt_fx(ex):
    """
    获取 1 USD / 1 USDC / 1 TUSD 对 USDT 的汇率（用于把非USDT报价转成USDT等值）。
    通常这些稳定币≈1，但个别交易所有时只提供 USDC/USDT 或 USD/USDT。
    返回字典：{ 'USD': fx, 'USDC': fx, 'TUSD': fx }，若缺失则省略。
    """
    fx = {}
    for q in ["USD","USDC","TUSD"]:
        for pair in (f"{q}/USDT", f"USDT/{q}"):
            if pair in ex.markets:
                try:
                    t = ex.fetch_ticker(pair)
                    last = t.get("last")
                    if not last: continue
                    if pair.endswith("/USDT"):
                        fx[q] = float(last)         # q/USDT
                    else:
                        fx[q] = 1.0 / float(last)   # USDT/q
                    break
                except Exception:
                    continue
    return fx

def get_price_ma_atr_smart(symbol: str):
    """
    综合多交易所解析行情。
    返回：
      {
        "price": float or None（USDT等值价）,
        "ma50": float or None,
        "ma200": float or None,
        "atr1h": float or None,
        "exchange": ex.id or None,
        "raw_symbol": 实际使用的交易所内符号,
        "reason": 失败原因（仅在全部失败时返回）
      }
    """
    base, _, want_quote = symbol.partition("/")
    base = base.strip().upper()
    want_quote = (want_quote or "USDT").strip().upper()

    last_reason = None

    for ex in EXS:
        try:
            # 找最佳报价交易对
            best_sym, why = _best_symbol_on_exchange(ex, base)
            if not best_sym:
                last_reason = f"{ex.id}: {why}"
                continue

            # 获取价
            t = _fetch_ticker(ex, best_sym)
            if "_err" in t:
                last_reason = f"{ex.id}: {t['_err']}"
                continue
            last = t.get("last") or t.get("close")
            if not last:
                last_reason = f"{ex.id}: ticker empty"
                continue

            # 若 quote 不是USDT，折算到USDT
            quote = ex.markets[best_sym].get("quote")
            price_usdt = float(last)
            if quote.upper() != "USDT":
                fx = _get_usdt_fx(ex)
                if quote.upper() in fx:
                    price_usdt = price_usdt * fx[quote.upper()]
                else:
                    # 没有汇率，只能直接给出“原计价币”价格
                    price_usdt = None
                    last_reason = f"{ex.id}: no {quote}/USDT fx"
                    # 继续尝试下一交易所
                    continue

            # MA50/MA200（日线）
            ma50 = ma200 = None
            ohlcv_d = _fetch_ohlcv(ex, best_sym, '1d', 220)
            if isinstance(ohlcv_d, dict) and "_err" in ohlcv_d:
                # 不致命，允许缺失
                pass
            elif ohlcv_d and isinstance(ohlcv_d, list) and len(ohlcv_d) >= 200:
                closes_d = [float(c[4]) for c in ohlcv_d]
                ma50  = sum(closes_d[-50:])/50
                ma200 = sum(closes_d[-200:])/200

            # ATR(1h)
            atr_pct = None
            ohlcv_h = _fetch_ohlcv(ex, best_sym, '1h', max(60, ATR_LOOKBACK+2))
            if not (isinstance(ohlcv_h, dict) and "_err" in ohlcv_h) and ohlcv_h and len(ohlcv_h) >= (ATR_LOOKBACK+2):
                highs  = [float(c[2]) for c in ohlcv_h]
                lows   = [float(c[3]) for c in ohlcv_h]
                closes = [float(c[4]) for c in ohlcv_h]
                trs = []
                for i in range(1, len(ohlcv_h)):
                    prev = closes[i-1]
                    tr = max(highs[i]-lows[i], abs(highs[i]-prev), abs(lows[i]-prev))
                    trs.append(tr)
                atr = sum(trs[-ATR_LOOKBACK:]) / ATR_LOOKBACK
                if closes: atr_pct = (atr / closes[-1]) * 100.0

            # 成功
            return {
                "price": price_usdt,
                "ma50": ma50,
                "ma200": ma200,
                "atr1h": atr_pct,
                "exchange": ex.id,
                "raw_symbol": best_sym
            }

        except Exception as e:
            last_reason = f"{ex.id}: {e}"
            continue

    return {"price": None, "ma50": None, "ma200": None, "atr1h": None, "exchange": None, "raw_symbol": None, "reason": last_reason or "symbol not found"}

# ========== 主风控（动态/追踪止盈止损，与之前一致）==========
def pct_change(a, b):
    if a is None or b is None or b == 0: return None
    return abs(a - b) / abs(b) * 100.0

def main():
    positions = load_positions()
    if not positions:
        push_feishu(f"**风控扫描**\n- 暂无持仓或未配置。\n- 时间：{now_str()}")
        return

    prev = load_state()
    new_state = {}
    items = []
    missing = []
    ts = now_str()

    for pos in positions:
        sym  = pos.get("symbol")
        buy  = float(pos.get("buy_price", 0) or 0)
        qty  = float(pos.get("qty", 0) or 0)
        if not sym or buy <= 0 or qty <= 0:
            print(f"[WARN] invalid position: {pos}")
            continue

        info  = get_price_ma_atr_smart(sym)
        close = info["price"]; ma50 = info["ma50"]; ma200 = info["ma200"]; atr1h = info["atr1h"]; exid = info["exchange"]
        raw_symbol = info.get("raw_symbol")
        if close is None:
            reason = info.get("reason") or "行情缺失"
            items.append({
                "symbol": sym, "exchange": exid or "-", "reason": reason,
                "line1": f"- {sym}  现价:-  盈亏:-（{exid or '-'}）",
                "line2": f"  数据缺失：{reason}",
                "line3": "  建议：观察；等待数据恢复"
            })
            continue

        pnl_pct = (close - buy) / buy * 100.0 if buy else None

        # 基线
        sl_base = buy * (1 - STOP_LOSS_PCT/100.0)
        tp_base = buy * (1 + TAKE_PROFIT_PCT/100.0)

        # 上次状态
        prev_it = prev.get(sym, {})
        prev_sl = prev_it.get("sl_price")
        prev_tp = prev_it.get("tp_price")
        highest_close = prev_it.get("highest_close") or close
        highest_close = max(highest_close, close)

        # 追踪止损
        sl_price = sl_base
        sl_trailing = False
        if TRAIL_ENABLE and atr1h is not None:
            sl_trailing = True
            sl_trail_pct = max(atr1h * SL_TRAIL_ATR_K, SL_TRAIL_MIN_PCT) / 100.0
            trail_candidate = close * (1 - sl_trail_pct)
            sl_price = max(sl_price, trail_candidate)
            if prev_sl: sl_price = max(sl_price, prev_sl)

        # 追踪止盈
        tp_price = tp_base
        tp_trailing = False
        if TP_TRAIL_ENABLE and pnl_pct is not None and pnl_pct >= TP_TRAIL_START_PNL and atr1h is not None:
            tp_trailing = True
            tp_trail_pct = max(atr1h * TP_TRAIL_ATR_K, TP_TRAIL_MIN_PCT) / 100.0
            tp_trail_price = highest_close * (1 - tp_trail_pct)
            tp_price = max(tp_price, tp_trail_price)
            if prev_tp: tp_price = max(tp_price, prev_tp)

        # 趋势提示
        if ma50 is None or ma200 is None:
            action_hint, notes = "观察", "均线数据不足，趋势待确认"
        else:
            if (close < ma50) and (ma50 < ma200):
                action_hint, notes = "减仓", f"跌破MA50≈{safe_num(ma50,4)}"
            elif (close > ma50) and (ma50 > ma200):
                action_hint, notes = "观察", "趋势未变，继续跟踪"
            else:
                action_hint, notes = "观察", "趋势待确认"

        # 变化说明
        changed_notes = []
        if prev_sl:
            ch = pct_change(sl_price, prev_sl)
            if ch is not None and ch >= CHANGE_EPS_PCT and sl_price > prev_sl:
                changed_notes.append(f"止损线上调→{safe_num(sl_price,6)}")
        else:
            changed_notes.append(f"设置止损→{safe_num(sl_price,6)}")

        if prev_tp:
            ch = pct_change(tp_price, prev_tp)
            if ch is not None and ch >= CHANGE_EPS_PCT and tp_price > prev_tp:
                changed_notes.append(f"止盈线上调→{safe_num(tp_price,6)}")
        else:
            changed_notes.append(f"设置止盈→{safe_num(tp_price,6)}")

        line1 = f"- {sym}  现价:{safe_num(close,6)}  盈亏:{safe_num(pnl_pct)}%（{exid or '-'}｜{raw_symbol or '-'}）"
        line2 = f"  止损:{safe_num(sl_price,6)}  止盈:{safe_num(tp_price,6)}  MA50:{safe_num(ma50,4)}  MA200:{safe_num(ma200,4)}"
        line3 = f"  建议：**{action_hint}**；{notes}；策略：追踪止损{'开' if sl_trailing else '关'}，追踪止盈{'开' if tp_trailing else '关'}，ATR1h≈{safe_num(atr1h,2)}%"
        if changed_notes: line3 += "；" + "；".join(changed_notes)

        items.append({"line1": line1, "line2": line2, "line3": line3})
        new_state[sym] = {"sl_price": sl_price, "tp_price": tp_price, "highest_close": highest_close}

    # 组装并推送
    lines = [f"**风控扫描（自动风控）**\n更新时间：{ts}\n"]
    for it in items:
        lines.extend([it["line1"], it["line2"], it["line3"]])
    push_feishu("\n".join(lines), title="持仓风控提醒")

    save_state(new_state)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            push_feishu(f"**持仓风控提醒**\n风控扫描失败\n- 时间：{now_str()}\n- 错误：{e}")
        except Exception:
            pass
        raise
