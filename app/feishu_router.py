from fastapi import APIRouter, Request
import json, pathlib, re
from typing import List
from .feishu_utils import parse_event, reply_md, get_tenant_access_token
from .exchanges import get_exchange
from .market import fetch_ohlcv_df
import requests

router = APIRouter(prefix="/feishu", tags=["feishu"])

DATA_DIR = pathlib.Path("/data")
HOLD_DIR = DATA_DIR / "holdings"
HOLD_DIR.mkdir(parents=True, exist_ok=True)

def _user_file(uid: str) -> pathlib.Path:
    return HOLD_DIR / (uid + ".json")

def _load(uid: str) -> List[dict]:
    p = _user_file(uid)
    if not p.exists(): return []
    return json.loads(p.read_text("utf-8"))

def _save(uid: str, items: List[dict]):
    _user_file(uid).write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")

def _parse_hold_lines(txt: str) -> List[dict]:
    items=[]
    for line in txt.strip().splitlines():
        line=line.strip()
        if not line or line.startswith("/"): continue
        parts = re.split(r"[,\s]+", line)
        if len(parts)<3: continue
        sym = parts[0].upper()
        entry = float(parts[1]); qty = float(parts[2])
        sl = float(parts[3]) if len(parts)>=4 else 8.0
        tp = float(parts[4]) if len(parts)>=5 else 12.0
        items.append({"symbol": sym, "exchange":"okx", "entry_price": entry, "qty": qty,
                      "stop_loss_pct": sl, "take_profit_pct": tp})
    return items

def _advice_md(items: List[dict]) -> str:
    if not items: return "**风控建议**\n- 暂无持仓。"
    ex = get_exchange("okx", None)
    ticks = ex.fetch_tickers()
    lines = ["**风控建议（仅供参考）**\n"]
    for h in items:
        sym=h["symbol"]; entry=float(h["entry_price"]); qty=float(h["qty"])
        slp=float(h.get("stop_loss_pct",8.0)); tpp=float(h.get("take_profit_pct",12.0))
        t = ticks.get(sym) or {}
        last = t.get("last") or ex.fetch_ticker(sym).get("last")
        df = fetch_ohlcv_df(ex, sym, "1h", 200)
        ma50 = df["close"].rolling(50).mean().iloc[-1]
        ma200 = df["close"].rolling(200).mean().iloc[-1] if len(df)>=200 else None
        slp_price = entry*(1-slp/100); tpp_price = entry*(1+tpp/100)
        pnl = (last-entry)/entry*100 if (last and entry) else None

        if last <= slp_price:
            action="卖出"; reason="触发止损，优先保护本金"
        elif last >= tpp_price:
            action="分批止盈"; reason="达到止盈目标，建议分批落袋"
        else:
            action="观察"; reasons=[]
            if last<ma50:
                action="减仓"; reasons.append(f"跌破MA50≈{ma50:.4f}")
            if ma200 and last<ma200:
                action="减仓"; reasons.append(f"低于MA200≈{ma200:.4f}")
            reason = "；".join(reasons) if reasons else "趋势未变，继续跟踪"

        lines.append(
          f"- **{sym}**  现价:{last}  盈亏:{pnl:.2f}%  "
          f"止损:{slp_price:.4f}  止盈:{tpp_price:.4f}  MA50:{ma50:.4f}"
          + (f"  MA200:{ma200:.4f}" if ma200 else "") +
          f"\n  建议：**{action}**；{reason}"
        )
    return "\n".join(lines)

def _reply_test_card(message_id: str):
    """发送一张带按钮的交互卡片；按钮回传 value={'cmd':'ping'}"""
    token = get_tenant_access_token()
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "交互测试卡片"}},
            "elements": [
                {"tag":"div","text":{"tag":"lark_md","content":"点击下方按钮验证 **card.action.trigger** 回传："}},
                {"tag":"action","actions":[
                    {
                      "tag":"button","text":{"tag":"plain_text","content":"点我返回 pong"},
                      "type":"primary","value":{"cmd":"ping"}
                    }
                ]},
                {"tag":"note","elements":[{"tag":"lark_md","content":"*非投资建议*"}]}
            ]
        }
    }
    requests.post(url, headers=headers, data=json.dumps(card), timeout=10)

@router.post("/callback")
async def feishu_callback(req: Request):
    body = await req.json()
    parsed = parse_event(body)
    if parsed["type"]=="challenge":
        return {"challenge": parsed["challenge"]}

    ev = parsed["event"] or {}

    # —— 分支1：卡片按钮回传（card.action.trigger）——
    if ev.get("type") == "card.action.trigger" or "action" in ev:
        val = (ev.get("action") or {}).get("value") or {}
        cmd = val.get("cmd")
        # 取可回复的消息ID：优先用 open_message_id，其次 message_id
        msg_id = ev.get("open_message_id") or (ev.get("message") or {}).get("message_id")
        if cmd == "ping" and msg_id:
            reply_md(msg_id, "pong ✅", "回传确认")
        return {"code":0}

    # —— 分支2：文本消息（im.message.receive_v1）——
    msg = ev.get("message", {})
    message_id = msg.get("message_id")
    sender = ev.get("sender", {})
    user_id = sender.get("sender_id", {}).get("user_id")
    try:
        text = json.loads(msg.get("content","{}")).get("text","").strip()
    except Exception:
        text = ""

    if not text or not user_id:
        return {"code":0}

    t = text.strip()
    low = t.lower()

    if low.startswith("/testcard"):
        _reply_test_card(message_id)
        return {"code":0}

    if low.startswith("/help"):
        reply_md(message_id,
                 "**可用命令**\n"
                 "`/testcard` 发送带按钮的测试卡片\n"
                 "`/holdings set` 多行：`币对 价格 数量 [止损% 止盈%]`\n"
                 "`/holdings list` 查看我的持仓\n"
                 "`/holdings clear confirm` 清空我的持仓\n"
                 "`/advice` 获取我的即时建议\n\n"
                 "示例：\n/holdings set\nBTC/USDT 60000 0.12 8 12\nSOL/USDT 165.3 20")
        return {"code":0}

    if low.startswith("/holdings clear"):
        if "confirm" in low:
            _save(user_id, [])
            reply_md(message_id, "✅ 已清空你的持仓。", "持仓管理")
        else:
            reply_md(message_id, "⚠️ 确认清空请发送：`/holdings clear confirm`", "持仓管理")
        return {"code":0}

    if low.startswith("/holdings list"):
        items = _load(user_id)
        if not items:
            reply_md(message_id, "你当前没有持仓记录。用 `/holdings set` 添加。", "持仓管理")
            return {"code":0}
        lines = ["**你的持仓**\n"]
        for h in items:
            lines.append(f"- {h['symbol']}  价格:{h['entry_price']}  数量:{h['qty']}  止损%:{h.get('stop_loss_pct',8)}  止盈%:{h.get('take_profit_pct',12)}")
        reply_md(message_id, "\n".join(lines), "持仓管理")
        return {"code":0}

    if low.startswith("/holdings set"):
        payload = t.split("\n",1)[1] if "\n" in t else ""
        items = _parse_hold_lines(payload)
        if not items:
            reply_md(message_id, "未解析到任何持仓。\n格式：每行 `币对 价格 数量 [止损% 止盈%]`，可用逗号或空格分隔。", "持仓管理")
            return {"code":0}
        _save(user_id, items)
        reply_md(message_id, "✅ 已更新你的持仓（共 {} 条）。\n\n{}".format(len(items), _advice_md(items)), "持仓已更新")
        return {"code":0}

    if low.startswith("/advice"):
        items = _load(user_id)
        reply_md(message_id, _advice_md(items), "我的风控建议")
        return {"code":0}

    reply_md(message_id, "指令未识别，发送 `/help` 查看用法。", "帮助")
    return {"code":0}
