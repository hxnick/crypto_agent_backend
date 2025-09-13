#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, time, fcntl, errno
from pathlib import Path
from datetime import datetime
import requests
from typing import Dict, Any, List, Optional

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")
DATA_DIR = Path("/root/crypto_agent_backend/data")
STATE_FILE = DATA_DIR / "risk_state.json"
LOCK_FILE = Path("/tmp/push_risk.lock")

REQ_TIMEOUT = float(os.getenv("REQ_TIMEOUT", "15"))     # 每次请求超时（秒）
MAX_RETRY = int(os.getenv("MAX_RETRY", "3"))            # 最大重试次数
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "1.8"))# 退避系数
LOCK_TTL = int(os.getenv("LOCK_TTL", "1500"))           # 锁超时（秒）默认25分钟

# 阈值变化阈值：价格相对变化超过此比例才提示（避免微小抖动反复提示）
THRESH_PCT = float(os.getenv("THRESH_PCT", "0.002"))    # 0.2%

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def with_retry(method, url, **kwargs):
    for i in range(1, MAX_RETRY+1):
        try:
            resp = method(url, timeout=REQ_TIMEOUT, **kwargs)
            if resp.status_code >= 200 and resp.status_code < 300:
                return resp
            # 非2xx也重试
        except requests.RequestException:
            pass
        if i < MAX_RETRY:
            time.sleep((RETRY_BACKOFF ** (i-1)) * 0.8)
    # 最后一轮再试一次，并把异常抛出/返回
    return method(url, timeout=REQ_TIMEOUT, **kwargs)

def acquire_lock():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EAGAIN):
            # 判断是否过期
            try:
                st = os.fstat(fd)
                age = time.time() - st.st_mtime
                if age > LOCK_TTL:
                    # 过期则强制占用并更新mtime
                    fcntl.lockf(fd, fcntl.LOCK_EX)
                else:
                    os.close(fd)
                    return None
            except Exception:
                os.close(fd)
                return None
        else:
            os.close(fd)
            return None
    # 写入当前时间
    os.write(fd, str(time.time()).encode())
    os.ftruncate(fd, os.lseek(fd, 0, os.SEEK_CUR))
    return fd

def release_lock(fd):
    try:
        os.close(fd)
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass

def load_state() -> Dict[str, Any]:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text("utf-8"))
    except Exception:
        pass
    return {}

def save_state(state: Dict[str, Any]):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def pct_change(old: Optional[float], new: Optional[float]) -> float:
    if old is None or new is None or old == 0:
        return 0.0
    return abs(new - old) / abs(old)

def fetch_risk_items() -> List[Dict[str, Any]]:
    r = with_retry(requests.get, f"{API_BASE}/risk/scan")
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"后端返回异常：{r.status_code} {r.text[:200]}")
    items = data.get("items") or []
    return items

def build_markdown(items: List[Dict[str, Any]], prev: Dict[str, Any]) -> str:
    if not items:
        return f"**风控扫描**\n- 暂无持仓或未配置。\n更新时间：{_now()}"

    lines = [f"**风控扫描（自动风控）**\n更新时间：{_now()}\n"]
    new_state = prev.copy()
    new_state.setdefault("symbols", {})

    for it in items:
        sym = it.get("symbol")
        if not sym:
            continue
        last = it.get("last")
        pnl = it.get("pnl_pct")
        ma50 = it.get("ma50"); ma200 = it.get("ma200")
        slp = it.get("stop_loss_price"); tpp = it.get("take_profit_price")
        action = it.get("action"); reason = it.get("reason")

        # 变更检测
        prev_sym = (prev.get("symbols") or {}).get(sym, {})
        changed_notes = []
        if pct_change(prev_sym.get("stop_loss_price"), slp) > THRESH_PCT:
            if prev_sym.get("stop_loss_price") is not None and slp is not None:
                changed_notes.append(f"止损价调整：{prev_sym['stop_loss_price']:.4f} → {slp:.4f}")
        if pct_change(prev_sym.get("take_profit_price"), tpp) > THRESH_PCT:
            if prev_sym.get("take_profit_price") is not None and tpp is not None:
                changed_notes.append(f"止盈价调整：{prev_sym['take_profit_price']:.4f} → {tpp:.4f}")

        # 行文本
        head = f"- **{sym}**  现价:{last}  盈亏:{(pnl if pnl is not None else '—')}%"
        line2 = f"  止损:{(f'{slp:.4f}' if slp else '—')}  止盈:{(f'{tpp:.4f}' if tpp else '—')}  MA50:{(f'{ma50:.4f}' if ma50 else '—')}" + (f"  MA200:{ma200:.4f}" if ma200 else "")
        line3 = f"  建议：**{action}**；{reason}"
        if changed_notes:
            line3 += "；" + "；".join(changed_notes)

        lines.extend([head, line2, line3])

        # 覆盖新状态
        new_state["symbols"][sym] = {
            "stop_loss_price": slp,
            "take_profit_price": tpp,
            "ma50": ma50,
            "ma200": ma200,
            "last": last,
            "pnl_pct": pnl,
            "ts": _now(),
        }

    # 保存新状态
    save_state(new_state)
    return "\n".join(lines)

def push_feishu_markdown(md: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("FEISHU_WEBHOOK not set")
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag":"plain_text", "content":"持仓风控提醒"}},
            "elements": [
                {"tag":"div","text":{"tag":"lark_md","content": md}},
                {"tag":"hr"},
                {"tag":"note","elements":[{"tag":"lark_md","content":"*自动风控·非投资建议*"}]}
            ]
        }
    }
    r = with_retry(requests.post, FEISHU_WEBHOOK, json=payload)
    if r.status_code >= 300:
        raise RuntimeError(f"飞书返回异常：{r.status_code} {r.text[:200]}")

def main():
    # 进程锁，防并发
    lock_fd = acquire_lock()
    if lock_fd is None:
        # 已有实例在运行，直接退出
        return

    try:
        items = fetch_risk_items()
        prev = load_state()
        md = build_markdown(items, prev)
        push_feishu_markdown(md)
    except Exception as e:
        # 失败也要写日志卡片（可选：你也能改成只写本地日志）
        try:
            push_feishu_markdown(f"**风控扫描失败**\n- 时间：{_now()}\n- 错误：{e}")
        except Exception:
            pass
        raise
    finally:
        release_lock(lock_fd)

if __name__ == "__main__":
    main()
