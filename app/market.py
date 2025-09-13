import pandas as pd

TF_ALIAS = {"1m":"1m","5m":"5m","15m":"15m","1h":"1h","4h":"4h","1d":"1d"}

def fetch_ohlcv_df(ex, symbol: str, tf: str, limit: int) -> pd.DataFrame:
    tf = TF_ALIAS.get(tf, "1h")
    ohlcv = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)
    return df
