import os, json, requests
from typing import Any, Dict

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
VERIFICATION_TOKEN = os.getenv("FEISHU_VERIFICATION_TOKEN", "")

def get_tenant_access_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    r = requests.post(url, json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=10).json()
    if r.get("code") != 0:
        raise RuntimeError(f"get token failed: {r}")
    return r["tenant_access_token"]

def reply_md(message_id: str, md: str, title: str = "Crypto Agent"):
    token = get_tenant_access_token()
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [
                {"tag":"div","text":{"tag":"lark_md","content": md}},
                {"tag":"hr"},
                {"tag":"note","elements":[{"tag":"lark_md","content":"*非投资建议*"}]}
            ]
        }
    }
    requests.post(url, headers=headers, data=json.dumps(card), timeout=10)

def parse_event(body: Dict[str, Any]) -> Dict[str, Any]:
    # 1) URL 验证：直接回 challenge（无需校验 token）
    if "challenge" in body:
        return {"type":"challenge", "challenge": body["challenge"]}

    # 2) 兼容两种位置的 token：顶层 token 或 header.token
    token_in = body.get("token") or (body.get("header") or {}).get("token")
    if VERIFICATION_TOKEN and token_in != VERIFICATION_TOKEN:
        raise RuntimeError("invalid verification token")

    # 3) 事件体（v2 为 event 字段）
    return {"type":"event", "event": body.get("event", {})}
