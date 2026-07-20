# Confirm Gate

## WHEN TO USE

- 需要用户**知悉或确认**某事实，**不收集**任何字段或秘密。
- 例：已展示风险说明，用户点「我已了解继续」。

## WHEN NOT TO USE

- 页面上的「登录」「提交」「同意并继续」等 **DOM 按钮** → `browser_exec_script` 点击。
- 滑块/验证码/OTP/密码 → 对应 gate。
- 代替滑块自动化或 `qr_scan` 的完成信号。

## OBSERVE

- 是否真有表单字段需收集；若无 → 才考虑 confirm。
- 用户是否已在消息中明确确认（有时无需 gate）。

## CLASSIFY/CHOOSE

- `request_user_gate(gate_type=confirm, prompt=..., fields=[])` — **不可**传 `fields`。
-  resume 返回 `{"status":"user_completed"}`，无 vault 写入。

## ACTION

1. 先用 `browser_capture_state` 或自然语言说明待确认内容。
2. 单独一步 confirm gate。
3. 继续后续 browser 步骤。

## DO NOT

- 用 confirm 让用户「帮点页面上的按钮」。
- 用 confirm 完成滑块验证。
- 同 step 并行其他工具。

## FAILURE/NEXT STEP

- `gate_cancelled` → 停止或换方案。
- `invalid_arguments`（误传 fields）→ 去掉 fields 重试。
