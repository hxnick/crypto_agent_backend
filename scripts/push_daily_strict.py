#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
今日候选（胜率增强版）二次筛选推送
- 基于现有 API /screen/daily 的候选，叠加更强过滤以提高胜率：
  1) 日线：Close > MA50 > MA200 且 MA50 上行
  2) 4小时：Close > EMA200 且 EMA200 上行
  3) 量能：最近3天 >=2天 的成交量 > 20日均量
- 最终只保留前 N 个（默认3）
- 推送到飞书群机器人
"""

import os, json, time, math, requests
from datetime import datetime

# ======== 环境变量 ========
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "").strip()

STRICT_TOPK = int(os.getenv("STRICT_TOPK", "3"))

# 量能参数
VOL_SMA_N = int(os.getenv("VOL_SMA_N", "20"))
VOL_AT_LEAST_N_OF_M = int(os.getenv("VOL_AT_LEAST_N_OF_M", "2"))
VOL_M = int(os.getenv("VOL_M", "3"))

# 是否启用各过滤（1启用/0关闭）
REQUIRE_TREND_CHAIN = os.getenv("REQUIRE_TREND_CHAIN", "1") == "1"   # 日线强趋势
REQUIRE_4H_CONFIRM  = os.getenv("REQUIRE_4H_CONFIRM", "1") == "1"    # 4小时共振
REQUIRE_VOL_PERSIST = os.getenv("REQUIRE_VOL_PERSIST", "1") == "1"   # 量能持续

# 其它
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ======== ccxt 准备 ========
try:
    import ccxt
except Exception as e:
    raise SystemExit("未安装 ccxt，请先执行：python3 -m pip install --upgrade ccxt") from e

EX_MAP = {}

def get_ex(exchange_id: str):
    exid = (exchange_id or "okx").lower()
    if exid not in EX_MAP:
        if exid == "binance":
            EX_MAP[exid] = ccxt.binance({"enableRateLimit": True, "timeout": 15000})
        elif exid == "okx":
            EX_MAP[exid] = ccxt.okx({"enableRateLimit": True, "timeout": 15000})
        else:
            # 兜底用 OKX
            EX_MAP[exid] = ccxt.okx({"enableRateLimit": True, "timeout": 15000})
        try:
            EX_MAP[exid].load_markets()
        except Exception as e:
            print(f"[WARN] load_markets({exid}) failed: {e}")
    return EX_MAP[exid], exid

def fetch_ohlcv_safe(ex, symbol, timeframe, limit):
    try:
        if symbol not in ex.markets:
            ex.load_markets()
        return ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception:
        try:
            m = ex.market(symbol)
            return ex.fetch_ohlcv(m['symbol'], timeframe=timeframe, limit=limit)
        except Exception as e2:
            print(f"[WARN] fetch_ohlcv({ex.id},{symbol},{timeframe}) failed: {e2}")
            return None

def sma(vals, n):
    if not vals or len(vals) < n:
        return None
    return sum(vals[-n:]) / n

def ema(vals, n):
    if not vals or len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e

def slope_up(series):
    """判断简单上行：最后一点 > 倒数第二点"""
    if not series or len(series) < 2:
        return False
    return series[-1] > series[-2]

def trend_filter_daily(ex, symbol):
    """日线：Close > MA50 > MA200 且 MA50 上行"""
    kl = fetch_ohlcv_safe(ex, symbol, '1d', 230)
    if not kl or len(kl) < 205:
        return False, "-", "-", "数据不足(日线)"
    closes = [float(x[4]) for x in kl]
    last_close = closes[-1]
    ma50 = sma(closes, 50)
    ma200 = sma(closes, 200)
    # MA50 斜率：用“昨天的MA50”对比“今天的MA50”
    ma50_hist = []
    for i in range(len(closes) - 50 + 1):
        ma50_hist.append(sum(closes[i:i+50]) / 50.0)
    ma50_up = slope_up(ma50_hist)

    ok = (ma50 is not None and ma200 is not None and last_close is not None
          and last_close > ma50 > ma200 and ma50_up)
    reason = "OK" if ok else "未满足 Close>MA50>MA200 或 MA50未上行"
    return ok, last_close, ma50, reason

def confirm_4h(ex, symbol):
    """4小时：Close > EMA200 且 EMA200 上行"""
    kl = fetch_ohlcv_safe(ex, symbol, '4h', 220)
    if not kl or len(kl) < 210:
        return False, "数据不足(4h)"
    closes = [float(x[4]) for x in kl]
    ema200_hist = []
    # 生成ema200序列（简化：滑动EMA）
    # 为了效率，先算一遍完全EMA，再模拟倒数两点的上行判断
    e = None
    k = 2/(200+1)
    for c in closes:
        e = c if e is None else (c*k + e*(1-k))
        ema200_hist.append(e)
    if len(ema200_hist) < 2:
        return False, "数据不足(EMA200)"
    ema200_up = slope_up(ema200_hist)
    ok = closes[-1] > ema200_hist[-1] and ema200_up
    return ok, ("OK" if ok else "未满足 Close>EMA200 或 EMA200未上行")

def volume_persist(ex, symbol, n=VOL_SMA_N, at_least=VOL_AT_LEAST_N_OF_M, m=VOL_M):
    """最近 m 天里至少 at_least 天的成交量 > n日均量"""
    kl = fetch_ohlcv_safe(ex, symbol, '1d', max(250, n+m+5))
    if not kl or len(kl) < (n+m+1):
        return False, "数据不足(量能)"
    vols = [float(x[5]) for x in kl]
    # 20日均量以“前一日”为基准，避免未来函数
    ok_cnt = 0
    for i in range(1, m+1):
        sma_n = sma(vols[:-i], n)  # 以今天之前的N日均量对比
        if sma_n is None:
            continue
        if vols[-i] > sma_n:
            ok_cnt += 1
    ok = ok_cnt >= at_least
    return ok, (f"近{m}日有{ok_cnt}日量能>均量{n}日" + ("" if ok else "（不足）"))

def fetch_daily_candidates(limit=5):
    url = f"{API_BASE}/screen/daily"
    try:
        r = requests.post(url, json={"limit": limit}, timeout=HTTP_TIMEOUT)
        j = r.json()
        return j.get("topn", []), None
    except Exception as e:
        return [], f"拉取 /screen/daily 失败：{e}"

def push_feishu(md: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("FEISHU_WEBHOOK 未设置")
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag":"plain_text","content":"今日候选（胜率增强版）"}},
            "elements":[{"tag":"div","text":{"tag":"lark_md","content": md}}]
        }
    }
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=HTTP_TIMEOUT)
    try:
        body = r.json()
    except Exception:
        body = {}
    if not (r.status_code < 300 and body.get("code", 0) == 0):
        raise RuntimeError(f"飞书推送失败：{r.status_code} {str(body)[:200]}")

def main():
    raw, err = fetch_daily_candidates(limit=10)
    ts = now_str()
    if err:
        push_feishu(f"**今日候选（胜率增强版）**\n- 拉取候选失败：{err}\n- 时间：{ts}")
        return

    kept = []
    diag = []
    for i, it in enumerate(raw, 1):
        sym = it.get("symbol")           # 例如 "KMNO/USDT"
        exid = (it.get("exchange") or "okx").lower()  # 例如 okx/binance
        # 允许智能纠错（ccxt 市场里存在）
        ex, exid = get_ex(exid)

        ok_daily = ok_4h = ok_vol = True
        rs_daily = rs_4h = rs_vol = "OK"

        if REQUIRE_TREND_CHAIN:
            ok_daily, last_close, ma50, rs_daily = trend_filter_daily(ex, sym)
        if REQUIRE_4H_CONFIRM:
            ok_4h, rs_4h = confirm_4h(ex, sym)
        if REQUIRE_VOL_PERSIST:
            ok_vol, rs_vol = volume_persist(ex, sym, VOL_SMA_N, VOL_AT_LEAST_N_OF_M, VOL_M)

        all_ok = ( (not REQUIRE_TREND_CHAIN or ok_daily)
                   and (not REQUIRE_4H_CONFIRM or ok_4h)
                   and (not REQUIRE_VOL_PERSIST or ok_vol) )

        diag.append(f"{i}. {sym}({exid}) - 日线:{'OK' if ok_daily else 'X'}[{rs_daily}] / 4h:{'OK' if ok_4h else 'X'}[{rs_4h}] / 量能:{'OK' if ok_vol else 'X'}[{rs_vol}]")

        if all_ok:
            # 组织输出条目
            score_total = it.get("score_total")
            spread = it.get("avg_spread_pct")
            reason = []
            if REQUIRE_TREND_CHAIN: reason.append("日线强趋势")
            if REQUIRE_4H_CONFIRM:  reason.append("4小时共振")
            if REQUIRE_VOL_PERSIST: reason.append("量能持续放大")
            advice = "建议买入（小仓试探）"
            kept.append({
                "symbol": sym,
                "exchange": exid,
                "score": score_total,
                "spread": spread,
                "advice": advice,
                "reason": "、".join(reason) if reason else "—"
            })

    # 排序：优先回用原总分，其次点差
    kept.sort(key=lambda x: (-(x["score"] or 0), (x["spread"] or 9e9)))
    final = kept[:STRICT_TOPK]

    # 生成 Markdown
    lines = [f"**今日候选（胜率增强版）**\n**更新时间：**{ts}\n"]
    lines.append(f"筛前：{len(raw)}  筛后：{len(final)}\n")
    if final:
        lines.append("**Top 候选**")
        for idx, it in enumerate(final, 1):
            lines.append(
                f"{idx}. {it['symbol']}（{it['exchange']}）\n"
                f"   综合分数：{it['score'] if it['score'] is not None else '-'}；点差：{it['spread']:.3f}%\n"
                f"   操作建议：**{it['advice']}**\n"
                f"   理由：{it['reason']}"
            )
    else:
        lines.append("**注：** 经过胜率增强过滤后，**暂无合适标的**。")

    # 追加调试诊断
    lines.append("\n**筛选诊断**")
    for ln in diag:
        lines.append(f"- {ln}")

    push_feishu("\n".join(lines))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 失败也要通知
        try:
            push_feishu(f"**今日候选（胜率增强版）**\n- 任务失败：{e}\n- 时间：{now_str()}")
        except Exception:
            pass
        raise
