def to_cn_action(last, sl_price, tp_price, ma50=None, ma200=None):
    reasons = []
    # 1) 先看止损/止盈（硬规则）
    if last is not None and sl_price is not None and last <= sl_price:
        return "卖出", ["触发止损，优先保护本金"]
    if last is not None and tp_price is not None and last >= tp_price:
        return "分批止盈", ["达到止盈目标，建议分批落袋"]

    # 2) 趋势判定（软规则）
    if ma50 and last is not None and last < ma50:
        reasons.append(f"跌破MA50≈{ma50:.4f}")
        action = "减仓"
    else:
        action = "观察"

    if ma200 and last is not None and last < ma200:
        reasons.append(f"低于MA200≈{ma200:.4f}")
        # 在弱势结构下进一步保守
        action = "减仓" if action == "观察" else action

    if not reasons:
        reasons.append("趋势未变，继续跟踪")
    return action, reasons
