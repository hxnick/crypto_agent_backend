#!/bin/bash
# ========== Daily Candidate Push Script ==========
# 包含大盘过滤 & 基本面过滤

# 正常阈值
export LIQUIDITY_USDT_MIN=3000000
export VOLUME_RATIO_MIN=1.05

# 是否允许在 BTC/ETH 上行时放宽
export RELAX_ON_UPTREND=1

# 放宽阈值
export RELAXED_LIQUIDITY_USDT_MIN=1000000
export RELAXED_VOLUME_RATIO_MIN=0.8

# 执行推送
cd /root/crypto_agent_backend
/usr/bin/python3 scripts/push_daily_filtered.py
