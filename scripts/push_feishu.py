import os, requests, json
WEBHOOK = os.getenv("FEISHU_WEBHOOK")
API_BASE = os.getenv("DATA_API", "http://localhost:8000")
def main():
    payload = {"symbols": None, "exchange": "okx", "topn": 5}
    res = requests.post(f"{API_BASE}/screen/daily", json=payload, timeout=60).json()
    items = res.get("topn", [])
    md_lines = ["**今日候选 Top 5**\n"]
    for i, it in enumerate(items, 1):
        md_lines.append(f"{i}. {it['symbol']} 分数:{it['score_total']} 趋势:{int(it['score_trend'])} 量能:{int(it['score_volume'])} RS:{int(it['score_rel_strength'])} 点差:{it['avg_spread_pct']}%")
    card = {"msg_type":"interactive","card":{"config":{"wide_screen_mode":True},
        "header":{"title":{"tag":"plain_text","content":"AI Agent - 每日候选"}},
        "elements":[{"tag":"div","text":{"tag":"lark_md","content":"\n".join(md_lines)}},{"tag":"hr"},
                    {"tag":"note","elements":[{"tag":"lark_md","content":"*非投资建议，谨慎参与*"}]}]}}
    resp = requests.post(WEBHOOK, data=json.dumps(card), headers={"Content-Type":"application/json"})
    print(resp.status_code, resp.text)
if __name__ == "__main__":
    if not WEBHOOK: raise SystemExit("FEISHU_WEBHOOK not set")
    main()
