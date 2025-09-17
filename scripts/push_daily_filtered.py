#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, math, requests, statistics
from datetime import datetime

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")
BINANCE_API = "https://api.binance.com"
BINANCE_TIMEOUT = float(os.getenv("BINANCE_TIMEOUT", "8"))
REQ_TIMEOUT = float(os.getenv("REQ_TIMEOUT", "15"))

# ========== 工具 ==========
def env_float(name, default):
    try: return float(os.getenv(name, str(default)))
    except Exception: return default

def env_int(name, default):
    try: return int(os.getenv(name, str(default)))
    except Exception: return default

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def safe_num(x, nd=2, default="-"):
    try:
        if x is None: return default
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x) if x is not None else default

# ========== Binance 基础 ==========
def bn_get(path, params=None, timeout=BINANCE_TIMEOUT):
    try:
        r = requests.get(f"{BINANCE_API}{path}", params=params or {}, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def bn_klines(symbol: str, interval="1d", limit=200):
    j = bn_get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return j or []

def bn_24h_ticker(symbol: str):
    j = bn_get("/api/v3/ticker/24hr", {"symbol": symbol})
    return j or {}

def bn_24h_all():
    j = bn_get("/api/v3/ticker/24hr")
    return j or []

def ma(series, n):
    if len(series) < n: return None
    return sum(series[-n:]) / n

def symbol_to_binance(sym_pair: str) -> str:
    return sym_pair.replace("/", "")

# ========== 市场与基本面 ==========
def get_trend_info(symbol: str):
    kl = bn_klines(symbol, "1d", 220)
    closes = [float(k[4]) for k in kl] if kl else []
    if not closes:
        return {"trend":"flat","close":None,"ma50":None,"ma200":None,"ma50_slope":0.0}
    _ma50  = ma(closes, 50)
    _ma200 = ma(closes, 200)
    close  = closes[-1]
    if _ma50 and _ma200:
        if close < _ma50 and _ma50 < _ma200: trend = "down"
        elif close > _ma50 and _ma50 > _ma200: trend = "up"
        else: trend = "flat"
        if len(closes) >= 55:
            prev50 = ma(closes[-55:-5], 50)
            slope = ((_ma50 - prev50)/prev50) if (prev50 and prev50!=0) else 0.0
        else:
            slope = 0.0
    else:
        trend, slope = "flat", 0.0
    return {"trend":trend, "close":close, "ma50":_ma50, "ma200":_ma200, "ma50_slope":slope}

def fundamentals_for_symbol(sym_pair: str):
    bnsym = symbol_to_binance(sym_pair)
    info = {'ok': False, 'quote_volume': None, 'volume_ratio': None, 'price_change_pct': None}
    t = bn_24h_ticker(bnsym)
    if not t or 'quoteVolume' not in t: return info
    try:
        quote_vol_24h = float(t.get('quoteVolume', 0.0))
        chg_pct = float(t.get('priceChangePercent', 0.0))
    except Exception:
        quote_vol_24h, chg_pct = 0.0, 0.0
    kl = bn_klines(bnsym, "1d", 35)
    vols = []
    for k in kl:
        try: vols.append(float(k[7]))  # Quote asset volume
        except Exception: pass
    if len(vols) >= 30: vol_avg_30d = sum(vols[-30:])/30
    else: vol_avg_30d = (sum(vols)/len(vols)) if vols else 0.0
    vol_ratio = (quote_vol_24h/vol_avg_30d) if (vol_avg_30d and vol_avg_30d>0) else None
    info.update({'ok': True, 'quote_volume': quote_vol_24h, 'volume_ratio': vol_ratio, 'price_change_pct': chg_pct})
    return info

# ========== 短线波动护栏 & 确认 ==========
def atr_percent_1h(sym_pair: str, lookback=14):
    """1小时ATR%（(均值TrueRange)/close）。"""
    bnsym = symbol_to_binance(sym_pair)
    kl = bn_klines(bnsym, "1h", 60+lookback)
    if not kl or len(kl) < lookback+1: return None
    highs = [float(x[2]) for x in kl]
    lows  = [float(x[3]) for x in kl]
    closes= [float(x[4]) for x in kl]
    trs = []
    for i in range(1, len(kl)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    atr = sum(trs[-lookback:]) / lookback
    close = closes[-1]
    return (atr/close)*100.0 if close else None

def confirm_15m_above_ma20(sym_pair: str, need=2):
    """15m 连续 need 根收在MA20上方。"""
    bnsym = symbol_to_binance(sym_pair)
    kl = bn_klines(bnsym, "15m", 80)
    if not kl or len(kl) < 30: return False
    closes = [float(x[4]) for x in kl]
    ma20s = []
    for i in range(len(closes)):
        if i+1 >= 20: ma20s.append(sum(closes[i-19:i+1])/20)
        else: ma20s.append(None)
    cnt = 0
    for i in range(len(closes)-need, len(closes)):
        if ma20s[i] is None: return False
        if closes[i] > ma20s[i]: cnt += 1
    return cnt >= need

def last_60m_drawdown_pct(sym_pair: str):
    """最近60分钟内的最大回撤百分比（从近1h最高到收盘）。"""
    bnsym = symbol_to_binance(sym_pair)
    kl = bn_klines(bnsym, "1m", 80)
    if not kl or len(kl) < 60: return 0.0
    closes = [float(x[4]) for x in kl[-60:]]
    high = max(closes)
    last = closes[-1]
    if high <= 0: return 0.0
    return (high-last)/high*100.0

# ========== 后端候选读取（兼容 topn 等） ==========
def _normalize_item(it):
    if not isinstance(it, dict): return {}
    out = {}
    out["symbol"] = it.get("symbol") or it.get("sym") or it.get("pair")
    out["score"] = it.get("score") or it.get("score_total") or it.get("total_score")
    out["trend_score"] = it.get("trend_score") or it.get("score_trend") or 70
    out["volume_score"] = it.get("volume_score") or it.get("score_volume") or 70
    out["strength_score"] = it.get("strength_score") or it.get("score_rel_strength") or 70
    out["spread_pct"] = it.get("spread_pct") or it.get("avg_spread_pct")
    out["action_hint"] = it.get("action_hint") or it.get("action") or "建议观察"
    out["notes"] = it.get("notes") or it.get("reason") or "-"
    return out

def fetch_daily_candidates(limit=20):
    try:
        r = requests.post(f"{API_BASE}/screen/daily", json={"limit": limit}, timeout=REQ_TIMEOUT)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return [], {"error": f"{e}"}
    items = []
    if isinstance(raw, list): items = raw
    elif isinstance(raw, dict):
        if isinstance(raw.get("items"), list): items = raw["items"]
        elif isinstance(raw.get("topn"), list): items = raw["topn"]
        elif isinstance(raw.get("data"), list): items = raw["data"]
        elif isinstance(raw.get("data"), dict) and isinstance(raw["data"].get("items"), list):
            items = raw["data"]["items"]
    norm = [_normalize_item(it) for it in items if isinstance(it, dict)]
    norm = [x for x in norm if x.get("symbol")]
    return norm, raw

# ========== 过滤（大盘 + 基本面 + 波动护栏/确认） ==========
def filter_with_all(items):
    # 大盘
    btc = get_trend_info("BTCUSDT")
    eth = get_trend_info("ETHUSDT")
    down_market = (btc["trend"] == "down" and eth["trend"] == "down")

    # 基本面阈值
    LIQ = env_float("LIQUIDITY_USDT_MIN", 10_000_000.0)
    VRM = env_float("VOLUME_RATIO_MIN", 1.2)
    RELAX_ON_UP = os.getenv("RELAX_ON_UPTREND", "1") == "1"
    LIQ_RELAX = env_float("RELAXED_LIQUIDITY_USDT_MIN", 2_000_000.0)
    VRM_RELAX = env_float("RELAXED_VOLUME_RATIO_MIN", 0.95)

    # 波动护栏与确认阈值（可通过环境变量调）
    ATR1H_MAX = env_float("ATR1H_MAX", 3.5)         # 超过则不建议买入
    CONFIRM_NEED = env_int("CONFIRM_15M_NEED", 2)   # 至少N根15m
    DD60_MAX = env_float("DD60_MAX", 1.0)           # 近60m最大回撤超过则只观察

    filtered, reasons = [], {}

    def _fund_filter(it, relaxed=False):
        sym = it.get("symbol")
        f = fundamentals_for_symbol(sym)
        qv = f.get("quote_volume") or 0.0
        vr = f.get("volume_ratio")
        chg= f.get("price_change_pct")
        LIQ_T = LIQ_RELAX if relaxed else LIQ
        VRM_T = VRM_RELAX if relaxed else VRM

        if down_market:
            if f['ok'] and qv < LIQ_T: return False, [f"大盘下行，流动性不足（24h≈{int(qv):,}）"]
            if not f['ok']: return False, ["大盘下行且无主流所数据"]
        if f['ok']:
            if (vr is not None and vr < VRM_T) and (chg is not None and chg <= 0):
                if not down_market and relaxed and ((it.get('trend_score',0) >= 75) or (it.get('strength_score',0) >= 80)):
                    pass
                else:
                    return False, [f"无放量（VR≈{vr:.2f}）且当日≤0%（{chg:.2f}%）"]
        elif down_market:
            return False, ["大盘下行且无数据"]
        return True, []

    def _vol_guard_and_confirm(it):
        sym = it.get("symbol")
        atr1h = atr_percent_1h(sym) or 0.0
        dd60  = last_60m_drawdown_pct(sym) or 0.0
        ok_confirm = confirm_15m_above_ma20(sym, need=CONFIRM_NEED)

        # 默认操作建议
        hint = it.get("action_hint","建议观察")
        notes = it.get("notes","-")

        # 规则：过度波动 → 只观察
        if atr1h > ATR1H_MAX:
            return "建议观察", notes + f"（波动护栏：1h ATR≈{atr1h:.2f}%>阈值{ATR1H_MAX}%）"

        # 规则：近60m回撤过大 → 只观察
        if dd60 > DD60_MAX:
            return "建议观察", notes + f"（近60分钟回撤≈{dd60:.2f}%>阈值{DD60_MAX}%）"

        # 规则：未通过短线确认 → 只观察
        if not ok_confirm:
            return "建议观察", notes + f"（短线确认未通过：15m×{CONFIRM_NEED}根未站上MA20）"

        # 通过护栏 + 确认 → 保留原建议（如原来就是“建议买入”，则维持）
        return hint, notes

    # 严格基本面
    for it in items:
        keep, rs = _fund_filter(it, relaxed=False)
        if keep: filtered.append(it)
        else: reasons.setdefault(it.get("symbol","?"), []).extend(rs)

    # 上行放宽
    if (not down_market) and os.getenv("RELAX_ON_UPTREND","1")=="1" and len(filtered)<3:
        relaxed = []
        for it in items:
            if it in filtered: continue
            keep, rs = _fund_filter(it, relaxed=True)
            if keep: relaxed.append(it)
            else: reasons.setdefault(it.get("symbol","?"), []).extend(["(relaxed) "+r for r in rs])
        seen, merged = set(), []
        for it in filtered + relaxed + items:
            sym = it.get("symbol")
            if sym not in seen:
                merged.append(it); seen.add(sym)
        filtered = merged

    # 波动护栏 + 确认：只调整“操作建议/理由”，不过度删标的，避免空列表
    for it in filtered:
        hint, notes = _vol_guard_and_confirm(it)
        it["action_hint"] = hint
        it["notes"] = notes

    meta = {"market": {"btc": btc, "eth": eth, "down_market": down_market}, "drop_reasons": reasons}
    return filtered, meta

# ========== 卡片与推送 ==========
def build_card_md(raw_items, filtered_items, meta, emergency=False):
    ts = now_str()
    btc = meta["market"]["btc"]; eth = meta["market"]["eth"]

    top = filtered_items[:5]
    fallback_used = False
    if not top and raw_items:
        top = sorted(raw_items, key=lambda x: (x.get("score") or 0), reverse=True)[:5]
        fallback_used = True

    def fmt_item(i, it):
        try:
            return (f"{i}. {it.get('symbol')}\n"
                    f"   综合分数：{safe_num(it.get('score'))}（趋势{it.get('trend_score')}, 量能{it.get('volume_score')}, 强度{it.get('strength_score')}）\n"
                    f"   操作建议：{it.get('action_hint','建议观察')}\n"
                    f"   理由：{it.get('notes','—')}；点差：{safe_num(it.get('spread_pct'), nd=4)}%\n")
        except Exception:
            sym = it.get("symbol","?"); score = it.get("score","?")
            return f"{i}. {sym}\n   综合分数：{score}\n   操作建议：建议观察\n"

    lines = [f"**今日候选 Top 5（已过滤）**\n更新时间：{ts}\n"]
    if emergency: lines.append("_注：后端候选为空，已启用 Binance 应急模式。_\n")
    if fallback_used: lines.append("_注：严格过滤后为空，已回退至原始 Top5。_\n")

    if not top:
        lines.append("_注：经过大盘与基本面过滤后，今日暂无合适标的。_")
    else:
        for idx, it in enumerate(top, 1):
            lines.append(fmt_item(idx, it))

    drops = meta.get("drop_reasons", {})
    if drops:
        lines.append("\n**被剔除的币及理由（部分）**")
        cnt = 0
        for sym, rs in drops.items():
            lines.append(f"- {sym}：{'；'.join(rs[:3])}")
            cnt += 1
            if cnt >= 5: break

    def note_nums(x):
        return f"{x['trend']}（close≈{safe_num(x['close'])}, MA50≈{safe_num(x['ma50'])}, MA200≈{safe_num(x['ma200'])}）"

    diag = f"_诊断：raw={len(raw_items)}, filtered={len(filtered_items)}, fallback={'Y' if fallback_used else 'N'}_"
    note = f"*BTC：{note_nums(btc)}；ETH：{note_nums(eth)}*\n{diag}"
    return "\n".join(lines), note

def push_feishu(card_md: str, meta_note: str = ""):
    if not FEISHU_WEBHOOK: raise RuntimeError("FEISHU_WEBHOOK not set")
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "今日候选（含大盘/基本面过滤）"}},
            "elements": [{"tag":"div", "text":{"tag":"lark_md", "content": card_md}}]
        }
    }
    if meta_note:
        payload["card"]["elements"].extend([{"tag":"hr"},{"tag":"note","elements":[{"tag":"lark_md","content": meta_note}]}])
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=REQ_TIMEOUT)
    try: body = r.json()
    except Exception: body = {}
    if not (r.status_code < 300 and body.get("code", 0) == 0):
        raise RuntimeError(f"Feishu error: {r.status_code} {str(body)[:200]}")

# ========== 主流程（含应急回退） ==========
def bn_emergency_items(limit=10, usdt_min=1_000_000):
    all24 = bn_24h_all() or []
    rows = []
    for t in all24:
        sym = t.get("symbol","")
        if not sym.endswith("USDT"): continue
        if "UPUSDT" in sym or "DOWNUSDT" in sym or "BULL" in sym or "BEAR" in sym: continue
        try:
            qv = float(t.get("quoteVolume", 0.0))
            chg = float(t.get("priceChangePercent", 0.0))
        except: continue
        if qv < usdt_min: continue
        rows.append((sym, qv, chg))
    rows.sort(key=lambda x: x[1], reverse=True)
    rows = rows[:200]
    def score(row): _, qv, chg = row; return math.log10(max(qv,1)) * (chg + 15)
    rows.sort(key=score, reverse=True)
    items = []
    for sym, qv, chg in rows[:limit]:
        # 简化
        items.append({"symbol": f"{sym[:-4]}/USDT","score": 70, "trend_score":70,"volume_score":70,"strength_score":70,"action_hint":"建议观察","notes":f"Binance应急：24h额≈{int(qv):,}USDT，涨幅{chg:.2f}%"})
    return items

def main():
    raw_items, _ = fetch_daily_candidates(limit=20)
    emergency = False
    if len(raw_items) == 0:
        raw_items = bn_emergency_items(limit=10, usdt_min=int(os.getenv("EMG_USDT_MIN","1000000")))
        emergency = True

    filtered, meta = filter_with_all(raw_items)
    md, note = build_card_md(raw_items, filtered, meta, emergency=emergency)
    push_feishu(md, note)

if __name__ == "__main__":
    main()
