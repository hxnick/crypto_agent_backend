#!/usr/bin/env bash
set -euo pipefail

# ========= 可按需修改的变量 =========
APP_DIR="${APP_DIR:-/opt/feishu-bot}"       # 项目目录
HOST="${HOST:-0.0.0.0}"                     # 监听地址
PORT="${PORT:-8000}"                        # 监听端口
PYTHON_BIN="${PYTHON_BIN:-python3}"         # Python 解释器
# 必填：你的飞书应用凭据（到“凭证与基础信息”里复制）
export FEISHU_APP_ID="${FEISHU_APP_ID:cli_a849536ea195900b}"
export FEISHU_APP_SECRET="${FEISHU_APP_SECRET:kmj6NXoW11A33UzT6bLbdbqoxEFr0WyF}"

echo "==> Installing system deps (if needed)"
if ! command -v $PYTHON_BIN >/dev/null 2>&1; then
  echo "Python3 not found. Please install Python3 first."; exit 1
fi
if ! command -v pip3 >/dev/null 2>&1; then
  echo "pip3 not found. Installing pip may be required."; exit 1
fi

echo "==> Creating app directory: $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$(id -u)":"$(id -g)" "$APP_DIR"
cd "$APP_DIR"

echo "==> Creating virtualenv"
$PYTHON_BIN -m venv .venv
source .venv/bin/activate

echo "==> Installing Python packages"
pip install --upgrade pip >/dev/null
pip install fastapi uvicorn requests >/dev/null

echo "==> Writing feishu_router.py"
cat > feishu_router.py <<'PY'
import os, time, json, logging, threading
from typing import Dict, Any
import requests
from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/feishu", tags=["feishu"])
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("feishu")

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

# 简易 token 缓存
_token_cache = {"token": None, "expire_at": 0}
_lock = threading.Lock()

def get_tenant_access_token() -> str:
    now = int(time.time())
    with _lock:
        if _token_cache["token"] and _token_cache["expire_at"] - 60 > now:
            return _token_cache["token"]
        url = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"get tenant_access_token failed: {data}")
        _token_cache["token"] = data["tenant_access_token"]
        _token_cache["expire_at"] = now + int(data.get("expire", 3600))
        return _token_cache["token"]

HELP_TEXT = (
    "👋 我在～\n"
    "可用指令：\n"
    "/help  显示帮助\n"
    "/holdings  查看/设置持仓（示例：/holdings set BTC 0.5）\n"
    "/advice  获取风控建议\n"
    "（把我拉进群，并直接输入指令即可；若开启“仅@推送”，就 @我 + 指令）"
)

def reply_text(message_id: str, text: str) -> Dict[str, Any]:
    token = get_tenant_access_token()
    url = f"https://open.larksuite.com/open-apis/im/v1/messages/{message_id}/reply"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    body = {"msg_type": "text", "content": {"text": text}}
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
    try:
        return r.json()
    except Exception:
        return {"status": r.status_code, "text": r.text}

@router.post("/callback")
async def feishu_callback(request: Request):
    body = await request.json()
    # 1) 回调地址校验
    if body.get("type") == "url_verification":
        challenge = body.get("challenge")
        log.info(f"url_verification ok: {challenge}")
        return {"challenge": challenge}

    # 2) 事件回调
    if body.get("type") == "event_callback":
        event = body.get("event", {})
        event_type = event.get("type")
        log.info(f"event type: {event_type}")

        if event_type == "im.message.receive_v2":
            msg = event.get("message", {})
            msg_id = msg.get("message_id")
            content_raw = msg.get("content") or "{}"
            try:
                content = json.loads(content_raw)
            except Exception:
                content = {}
            text = (content.get("text") or "").strip()
            log.info(f"received text: {text} (message_id={msg_id})")

            if text.lower().startswith("/help"):
                ret = reply_text(msg_id, HELP_TEXT)
                log.info(f"reply /help => {ret}")

        return Response(status_code=200)

    return Response(status_code=200)
PY

echo "==> Writing main.py"
cat > main.py <<'PY'
from fastapi import FastAPI
from feishu_router import router as feishu_router

app = FastAPI(title="Feishu Bot Minimal")
app.include_router(feishu_router)

@app.get("/")
def root():
    return {"ok": True, "service": "feishu-bot", "tip": "POST /feishu/callback"}
PY

echo "==> Creating launch script run.sh"
cat > run.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail
: "${HOST:=0.0.0.0}"
: "${PORT:=8000}"
source .venv/bin/activate
exec uvicorn main:app --host "$HOST" --port "$PORT"
SH
chmod +x run.sh

echo "==> Starting service (background via nohup)"
# 如需前台运行，改为： ./run.sh
nohup ./run.sh > app.out 2>&1 & echo $! > app.pid
sleep 1
echo "==> Service started. PID: $(cat app.pid)"
echo "==> Logs: tail -f $APP_DIR/app.out  (Ctrl+C 退出查看)"

cat <<TIP

============== 下一步自检 ==============

1) 健康检查（本机或公网）：
   curl -s http://127.0.0.1:${PORT}/

2) 回调地址本地校验（模拟 url_verification）：
   curl -sS -X POST http://127.0.0.1:${PORT}/feishu/callback \\
     -H 'Content-Type: application/json' \\
     -d '{"type":"url_verification","challenge":"abc-123"}'

   预期返回：{"challenge":"abc-123"}

3) 在“飞书开发者后台 -> 事件订阅”：
   - 回调 URL 填： https://你的域名:${PORT}/feishu/callback
     （你已有 nginx/证书的域名：例如 https://crystal.zxszhcs.cn/feishu/callback）
   - 勾选事件：im.message.receive_v2
   - 应用权限：具备发送/回复消息相关 scope
   - 通过“验证”按钮后保存

4) 群里测试：
   - 把机器人拉进群
   - 直接发送 /help
   - 若开启“仅@推送”，发送：@机器人 /help

5) 服务器查看日志：
   tail -f $APP_DIR/app.out

============== 常用维护命令 ==============

停止：    kill \$(cat $APP_DIR/app.pid) || true
重启：    kill \$(cat $APP_DIR/app.pid) || true && nohup $APP_DIR/run.sh > $APP_DIR/app.out 2>&1 & echo \$! > $APP_DIR/app.pid
查看端口： lsof -i :${PORT} -sTCP:LISTEN -nP || ss -ltnp | grep ${PORT}

TIP
