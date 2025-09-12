import os, requests, json

WEBHOOK = os.getenv("FEISHU_WEBHOOK")
API_BASE = os.getenv("DATA_API", "http://127.0.0.1:8000")

def main():
    payload = {"symbols": None, "exchange": "okx", "topn": 5}
    res = requests.post(f"{API_BASE}/screen/daily", json=payload, timeout=60).json()
    items = res.get("topn", [])

    md_lines = ["**今日候选 Top 5**\n"]
    for i, it in enumerate(items, 1):
        score = it.get("score_total")
        tscore = int(it.get("score_trend", 0))
        vscore = int(it.get("score_volume", 0))
        rscore = int(it.get("score_rel_strength", 0))
        spread = it.get("avg_spread_pct")
        action = it.get("action") or "建议观察"
        reason = it.get("reason") or ""
        spread_text = f"{spread}%" if spread is not None else "—"

        md_lines.append(
            f"{i}. {it['symbol']}\n"
            f"   综合分数：{score}（趋势{tscore}，量能{vscore}，强度{rscore}）\n"
            f"   操作建议：**{action}**\n"
            f"   理由：{reason}；点差：{spread_text}"
        )

    card = {
      "msg_type": "interactive",
      "card": {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "每日候选（含操作建议）"}},
        "elements": [
          {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(md_lines)}},
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
