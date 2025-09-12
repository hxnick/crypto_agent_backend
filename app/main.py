import os, json, time, pathlib
from typing import List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import redis
import ccxt

from .models import KlineQuery, SnapshotQuery, ScreenDailyQuery, Holding
from .config import settings
from .exchanges import get_exchange
from .scoring import total_score, decide_action_cn

DATA_DIR = pathlib.Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
HOLDINGS_FILE = DATA_DIR / "holdings.json"

app = FastAPI(title="Crypto Agent Data Hub", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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

@app.post("/screen/daily")
def screen_daily(q: ScreenDailyQuery):
    try:
        ex = get_exchange(q.exchange, _proxies())
        markets = ex.load_markets()
        candidates: List[str] = q.symbols or [m for m in markets.keys() if m.endswith("/USDT")]
        tick = ex.fetch_tickers()
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
