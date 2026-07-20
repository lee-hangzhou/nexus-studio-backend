# Security

## WHEN TO USE

- 设计 gate 调用、向用户展示信息、处理 tool_result 时对照。

## WHEN NOT TO USE

- 替代产品/legal 合规审查（本 doc 仅技术边界）。

## OBSERVE

**对用户/SSE 剥离的字段**（`strip_public_gate_payload`）：

- 所有 `*_selector`、`otp_flow_id`、`bridge_token`
- `page_region`、`captured_page_region`、`image_selector`
- vault 中凭据、OTP、captcha、`_bridge_cookies`

**用户可见**：gate `prompt`、字段 label、QR/captcha/challenge 图片 URL、confirm 文案、session_bridge 的 `domain`（非 token）。

## CLASSIFY/CHOOSE

- 秘密路径：用户面板 → vault → 一次性 take → browser fill → 不落 checkpoint/SSE。
- 选择器路径：inspect tool_result → 模型 → gate 参数 → 后端执行；**不经用户**。
- `bridge_token`：仅前端面板内存 + bridge API；过期与 domain 校验见 session_bridge 模块。

## ACTION

- 每 gate 单独 step，降低 secret 与 exec 交错风险。
- 截图证据用 `browser_capture_state`；回复中描述结论，不复读页面敏感文本。

## DO NOT

- 在 assistant 消息中 echo 密码/OTP/cookie/token/选择器。
- 把 `bridge_token` 写入日志（仅允许 prefix 级 audit）。
- 跳过 `expected_domain` 校验导入 cookie。

## FAILURE/NEXT STEP

- 怀疑 secret 泄漏 → 停止重试，检查 ToolMessage / interrupt payload。
- domain mismatch → 拒绝导入，提示用户在正确站点操作。
