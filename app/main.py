import os, json, time, pathlib
from typing import List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import redis

from .models import KlineQuery, SnapshotQuery, ScreenDailyQuery, Holding
from .config import settings
from .exchanges import get_exchange
from .scoring import total_score, decide_action_cn
from .risk_logic import compute_dynamic_advice
from .market import fetch_ohlcv_df
from .feishu_router import router as feishu_router  # ← 飞书路由

DATA_DIR = pathlib.Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
HOLDINGS_FILE = DATA_DIR / "holdings.json"

app = FastAPI(title="Crypto Agent Data Hub", version="0.4.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 注册飞书回调路由
app.include_router(feishu_router)

r = redis.from_url(settings.REDIS_URL)
TF_ALIAS = {"1m":"1m","5m":"5m","15m":"15m","1h":"1h","4h":"4h","1d":"1d"}

def _proxies():
    px = {}
    if settings.HTTP_PROXY: px["http"] = settings.HTTP_PROXY
    if settings.HTTPS_PROXY: px["https"] = settings.HTTPS_PROXY
    return px or None

def fetch_ohlcv_df(ex, symbol: str, tf: str, limit: int) -> pd.DataFrame:
    tf = TF_ALIAS.get(tf, "1h")
    ohlcv = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)
    return df

@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}

# ---------- KLINE ----------
@app.post("/kline")
def kline(q: KlineQuery):
    cache_key = f"k:{q.exchange}:{q.symbol}:{q.tf}:{q.limit}"
    hit = r.get(cache_key)
    if hit:
        return json.loads(hit)
    try:
        ex = get_exchange(q.exchange, _proxies())
        df = fetch_ohlcv_df(ex, q.symbol, q.tf, q.limit)
        payload = df.reset_index().to_dict(orient="records")
        r.setex(cache_key, 30, json.dumps(payload))
        return payload
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------- SNAPSHOT ----------
@app.post("/snapshot")
def snapshot(q: SnapshotQuery):
    res = []
    try:
        ex = get_exchange(q.exchange, _proxies())
        tickers = ex.fetch_tickers()
        for sym in q.symbols:
            t = tickers.get(sym)
            if not t: continue
            res.append({
                "symbol": sym,
                "last": t.get("last"),
                "bid": t.get("bid"),
                "ask": t.get("ask"),
                "baseVolume": t.get("baseVolume"),
                "quoteVolume": t.get("quoteVolume"),
                "info": t.get("info", {})
            })
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------- SCREEN DAILY ----------
@app.post("/screen/daily")
def screen_daily(q: ScreenDailyQuery):
    try:
        ex = get_exchange(q.exchange, _proxies())
        markets = ex.load_markets()
        candidates: List[str] = q.symbols or [m for m in markets.keys() if m.endswith("/USDT")]
        tick = ex.fetch_tickers()
        # 先按成交额过滤一下规模
        candidates = sorted(candidates, key=lambda s: (tick.get(s, {}).get("quoteVolume") or 0), reverse=True)[:120]

        bench_df = fetch_ohlcv_df(ex, "BTC/USDT", "1h", 500)
        scored = []
        for sym in candidates:
            try:
                df = fetch_ohlcv_df(ex, sym, "1h", 500)
                s = total_score(sym, df, bench_df)
                avg_spread = None
                t = tick.get(sym, {})
                bid, ask = t.get("bid"), t.get("ask")
                if bid and ask and ask > 0:
                    avg_spread = round((ask - bid) / ask * 100, 4)
                action_cn, reason_cn = decide_action_cn(df, s["score_total"], avg_spread)
                item = {
                    "symbol": sym,
                    "exchange": q.exchange,
                    "avg_spread_pct": avg_spread,
                    **s,
                    "action": action_cn,
                    "reason": reason_cn,
                }
                scored.append(item)
            except Exception:
                continue
        scored = sorted(scored, key=lambda x: x["score_total"], reverse=True)[: q.topn]
        return {"topn": scored, "bench": "BTC/USDT", "exchange": q.exchange}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------- HOLDINGS ----------
def _read_holdings() -> list[dict]:
    if not HOLDINGS_FILE.exists():
        return []
    try:
        return json.loads(HOLDINGS_FILE.read_text("utf-8"))
    except Exception:
        return []

def _write_holdings(items: list[dict]):
    HOLDINGS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")

@app.get("/holdings")
def get_holdings():
    return {"items": _read_holdings()}

@app.post("/holdings")
def set_holdings(items: List[Holding]):
    data = [i.model_dump() for i in items]
    _write_holdings(data)
    return {"ok": True, "count": len(data)}

# ---------- RISK SCAN（中文口径） ----------
@app.get("/risk/scan")
def risk_scan():
    items = _read_holdings()
    if not items:
        return {"items": [], "note": "no holdings"}
    ex = get_exchange("okx", _proxies())
    tickers = ex.fetch_tickers()
    out = []
    for h in items:
        sym = h.get("symbol")
        try:
            entry = float(h.get("entry_price", 0))
            qty = float(h.get("qty", 0))
            t = tickers.get(sym) or {}
            last = t.get("last") or ex.fetch_ticker(sym).get("last")
            df = fetch_ohlcv_df(ex, sym, "1h", 220)

            # 判断是否使用“动态风控”（当用户未提供止损/止盈百分比时）
            use_dynamic = ("stop_loss_pct" not in h) and ("take_profit_pct" not in h)

            if use_dynamic:
                dyn = compute_dynamic_advice(df, entry, last)
                sl_price = dyn["stop_loss_price"]
                tp_price = dyn["take_profit_price"]
                action = dyn["action"]
                reason = dyn["reason"]
                ma50 = dyn["ma50"]
                ma200 = dyn["ma200"]
            else:
                slp = float(h.get("stop_loss_pct", 8.0))
                tpp = float(h.get("take_profit_pct", 12.0))
                sl_price = entry * (1 - slp / 100.0)
                tp_price = entry * (1 + tpp / 100.0)
                # 基于百分比的口径 + 均线提示
                ma50 = df["close"].rolling(50).mean().iloc[-1]
                ma200 = df["close"].rolling(200).mean().iloc[-1] if len(df) >= 200 else None
                if last is not None and last <= sl_price:
                    action = "卖出"; reason = "触发止损，优先保护本金"
                elif last is not None and last >= tp_price:
                    action = "分批止盈"; reason = "达到止盈目标，建议分批落袋"
                else:
                    action = "观察"; reasons=[]
                    if ma50 and last < ma50:
                        action = "减仓"; reasons.append(f"跌破MA50≈{ma50:.4f}")
                    if ma200 and last < ma200:
                        action = "减仓"; reasons.append(f"低于MA200≈{ma200:.4f}")
                    reason = "；".join(reasons) if reasons else "趋势未变，继续跟踪"

            pnl_pct = round((last - entry) / entry * 100, 2) if (last and entry) else None

            out.append({
                "symbol": sym,
                "entry_price": entry,
                "qty": qty,
                "last": last,
                "stop_loss_price": round(sl_price, 6) if sl_price else None,
                "take_profit_price": round(tp_price, 6) if tp_price else None,
                "pnl_pct": pnl_pct,
                "ma50": round(ma50, 6) if ma50 else None,
                "ma200": round(ma200, 6) if ma200 else None,
                "action": action,
                "reason": reason,
            })
        except Exception as e:
            out.append({"symbol": sym, "error": str(e)})
    return {"items": out}
