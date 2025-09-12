#!/usr/bin/env bash
set -e
source /root/crypto_agent_backend/.env.feishu
python3 /root/crypto_agent_backend/scripts/push_risk.py
