from pydantic import BaseModel, Field
from typing import List, Optional

class KlineQuery(BaseModel):
    symbol: str = Field(example="BTC/USDT")
    exchange: Optional[str] = Field(default="okx")
    tf: str = Field(default="1h", description="timeframe")
    limit: int = Field(default=500, ge=50, le=2000)

class SnapshotQuery(BaseModel):
    symbols: List[str]
    exchange: Optional[str] = Field(default="okx")

class ScreenDailyQuery(BaseModel):
    symbols: Optional[List[str]] = None
    exchange: Optional[str] = Field(default="okx")
    topn: int = Field(default=10, ge=1, le=50)

class Holding(BaseModel):
    symbol: str
    exchange: Optional[str] = "okx"
    entry_price: float
    qty: float
    stop_loss_pct: Optional[float] = 8.0
    take_profit_pct: Optional[float] = 12.0
