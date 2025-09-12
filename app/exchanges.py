import ccxt
from typing import Dict
EX_MAP: Dict[str, type] = {
    "binance": ccxt.binance,
    "okx": ccxt.okx,
    "bitget": ccxt.bitget,
}
def get_exchange(name: str, proxies: dict | None = None):
    name = (name or "okx").lower()
    if name not in EX_MAP:
        raise ValueError(f"unsupported exchange: {name}")
    klass = EX_MAP[name]
    return klass({"enableRateLimit": True, "proxies": proxies or None})
