#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日选币诊断脚本（只打印，不推送）
- 调用后端 /screen/daily，尝试开启 diag 模式
- 打印：raw/filtered 数量、各阶段剔除原因（若后端返回）
- 在没有后端诊断字段时，尽力从 items 的字段推断
环境变量：
  API_BASE           默认 http://127.0.0.1:8000
  LIMIT              默认 20
  USE_STRICT         默认 1   （调用“胜率增强版”）
  FALLBACK_ON_EMPTY  默认 0   （仅诊断，不做兜底）
"""
import os, sys, json, requests
from datetime import datetime

API_BASE  = os.getenv("API_BASE", "http://127.0.0.1:8000").rstrip("/")
LIMIT     = int(os.getenv("LIMIT", "20"))
USE_STRICT= os.getenv("USE_STRICT", "1") == "1"
FALLBACK  = os.getenv("FALLBACK_ON_EMPTY", "0") == "1"
TIMEOUT   = float(os.getenv("REQ_TIMEOUT","30"))

def post_json(path, payload):
    url = f"{API_BASE}{path}"
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    try:
        body = r.json()
    except Exception:
        print(f"[ERROR] 非JSON响应: {r.status_code} {r.text[:200]}")
        sys.exit(2)
    if r.status_code >= 300:
        print(f"[ERROR] HTTP {r.status_code}: {str(body)[:200]}")
        sys.exit(2)
    return body

def main():
    payload = {
        "limit": LIMIT,
        "include_reason": True,
        "diag": True,           # 请求后端返回诊断
        "strict": USE_STRICT,   # 胜率增强版
        "fallback": False       # 诊断阶段默认不强制兜底
    }
    data = post_json("/screen/daily", payload)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== 日选币诊断（{ts}）===\n")

    # 1) 优先读后端提供的诊断
    diag = data.get("diag") or {}
    raw_cnt       = diag.get("raw_count")
    filtered_cnt  = diag.get("filtered_count")
    dropped_mkt   = diag.get("dropped_by_market")
    dropped_fund  = diag.get("dropped_by_fundamental")
    dropped_other = diag.get("dropped_by_other")

    # 2) 若没有 diag，尝试从 items 推断
    items = data.get("topn") or data.get("items") or []
    # 有的实现会把“筛前列表”放在 raw 字段
    raw_list = data.get("raw") if isinstance(data.get("raw"), list) else None
    filtered_list = items

    if raw_cnt is None:
        raw_cnt = len(raw_list) if raw_list is not None else None
    if filtered_cnt is None:
        filtered_cnt = len(filtered_list) if filtered_list is not None else None

    # 打印核心计数
    print("—— 核心统计 ——")
    print(f"筛前(raw)：{raw_cnt if raw_cnt is not None else '未知'}")
    print(f"筛后(filtered)：{filtered_cnt if filtered_cnt is not None else '未知'}")

    # 打印各类剔除统计
    if any(x is not None for x in [dropped_mkt, dropped_fund, dropped_other]):
        print("\n—— 剔除统计 ——")
        if dropped_mkt is not None:  print(f"大盘过滤剔除：{dropped_mkt}")
        if dropped_fund is not None: print(f"基本面过滤剔除：{dropped_fund}")
        if dropped_other is not None:print(f"其它规则剔除：{dropped_other}")

    # 若 filtered 为空，可以选择做“兜底预览”（仅打印，不推送）
    if (filtered_cnt or 0) == 0 and (raw_cnt or 0) > 0 and FALLBACK:
        # 取 raw 里分数最高的前3（字段名做多重兼容）
        pool = raw_list or []
        def score_of(x):
            for k in ("score_total","total_score","score"):
                v = x.get(k)
                if isinstance(v,(int,float)): return v
            return -1e9
        topk = sorted(pool, key=score_of, reverse=True)[:3]
        print("\n—— 触发兜底预览（Top3）——")
        for i, it in enumerate(topk, 1):
            sym = it.get("symbol") or "-"
            ex  = it.get("exchange") or "-"
            s   = score_of(it)
            print(f"{i}. {sym} [{ex}]  分数≈{s:.2f}")

    # 打印若有筛后结果的前N条摘要
    if filtered_cnt and filtered_cnt > 0 and items:
        print("\n—— 筛后 TopN ——")
        for i, it in enumerate(items, 1):
            sym = it.get("symbol") or "-"
            ex  = it.get("exchange") or "-"
            st  = None
            for k in ("score_total","total_score","score"):
                if isinstance(it.get(k),(int,float)): st = it[k]; break
            spread = it.get("avg_spread_pct")
            act    = it.get("action") or it.get("action_hint") or "-"
            reason = it.get("reason") or it.get("reason_text") or "-"
            line = f"{i}. {sym} [{ex}]"
            if st is not None: line += f"  分数≈{st:.2f}"
            if spread is not None: line += f"  点差≈{spread:.3f}%"
            if act != "-": line += f"  建议：{act}"
            if reason != "-": line += f"  理由：{reason}"
            print(line)

    # 对“raw 也是 0”的明确提示
    if (raw_cnt or 0) == 0:
        print("\n[提示] 原始候选为 0：请检查流动性/成交量/胜率阈值是否偏高，或当前市场是否整体低迷。")

if __name__ == "__main__":
    main()
