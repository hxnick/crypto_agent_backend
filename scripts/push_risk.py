import os, json, requests
WEBHOOK = os.getenv("FEISHU_WEBHOOK")
API_BASE = os.getenv("DATA_API", "http://localhost:8000")

def main():
    res = requests.get(f"{API_BASE}/risk/scan", timeout=60).json()
    items = res.get("items", [])
    if not items:
        text = "**风控扫描**\n- 暂无持仓或未配置。"
    else:
        lines = ["**风控扫描（最近一次）**\n"]
        for it in items:
            if "error" in it:
                lines.append(f"- {it.get('symbol')}: 错误 {it['error']}")
                continue
            lines.append(
              f"- {it['symbol']}  现价:{it['last']}  盈亏:{it['pnl_pct']}%  "
              f"SL:{it['stop_loss_price']}  TP:{it['take_profit_price']}  "
              f"MA50:{it.get('ma50')}  MA200:{it.get('ma200')}\n  建议：**{it['action_hint']}**；{it['notes']}"
            )
        text = "\n".join(lines)

    card = {
      "msg_type": "interactive",
      "card": {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "AI Agent - 风控提醒"}},
        "elements": [
          {"tag": "div", "text": {"tag": "lark_md", "content": text}},
          {"tag": "hr"},
          {"tag": "note", "elements": [{"tag": "lark_md", "content": "*非投资建议，谨慎参与*"}]}
        ]
      }
    }
    resp = requests.post(WEBHOOK, data=json.dumps(card), headers={"Content-Type":"application/json"})
    print(resp.status_code, resp.text)

if __name__ == "__main__":
    if not WEBHOOK:
        raise SystemExit("FEISHU_WEBHOOK not set")
    main()
