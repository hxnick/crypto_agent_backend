#!/usr/bin/env python3
import os
import subprocess
import json
from pathlib import Path

# 参数存储文件
STATE_FILE = Path("/root/crypto_agent_backend/adaptive_state.json")

# 正常阈值
BASE_LIQUIDITY = 3_000_000
BASE_VOLUME_RATIO = 1.05

# 放宽阈值最低限
MIN_LIQUIDITY = 500_000
MIN_VOLUME_RATIO = 0.6

# 读取状态
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"empty_count": 0, "liquidity": BASE_LIQUIDITY, "volume_ratio": BASE_VOLUME_RATIO}

# 保存状态
def save_state(state):
    STATE_FILE.write_text(json.dumps(state))

def run_scan(liq, vol):
    env = os.environ.copy()
    env["LIQUIDITY_USDT_MIN"] = str(int(liq))
    env["VOLUME_RATIO_MIN"] = str(vol)
    env["RELAX_ON_UPTREND"] = "1"
    env["RELAXED_LIQUIDITY_USDT_MIN"] = str(int(liq // 3))
    env["RELAXED_VOLUME_RATIO_MIN"] = str(round(vol * 0.7, 2))

    result = subprocess.run(
        ["python3", "scripts/push_daily_filtered.py"],
        cwd="/root/crypto_agent_backend",
        env=env,
        capture_output=True,
        text=True
    )
    return result.stdout

def main():
    state = load_state()
    output = run_scan(state["liquidity"], state["volume_ratio"])

    if "暂无合适标的" in output:
        state["empty_count"] += 1
        if state["empty_count"] >= 3:
            # 降低阈值
            state["liquidity"] = max(MIN_LIQUIDITY, int(state["liquidity"] * 0.8))
            state["volume_ratio"] = max(MIN_VOLUME_RATIO, round(state["volume_ratio"] * 0.8, 2))
            state["empty_count"] = 0
            print(f"[Adaptive] 参数已降低: liquidity={state['liquidity']}, volume_ratio={state['volume_ratio']}")
    else:
        # 恢复正常
        state = {"empty_count": 0, "liquidity": BASE_LIQUIDITY, "volume_ratio": BASE_VOLUME_RATIO}
        print("[Adaptive] 已恢复正常阈值")

    save_state(state)
    print(output)

if __name__ == "__main__":
    main()
