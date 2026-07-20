# Session Bridge Gate

> **当前未接入生产路径。Agent 勿调用 `session_bridge` gate。** 同会话登录续用见 [SESSION_PERSISTENCE.md](SESSION_PERSISTENCE.md)。下文仅供将来实现参考。

## WHEN TO USE

- 目标站必须在**用户本地已登录浏览器**中操作，远程 session 无法完成登录。
- inspect 确认需要导入 cookie 才能访问。

## WHEN NOT TO USE

- 可用 credentials / OTP / QR 等常规定义 gate 完成登录。
- 用户无法运行 bridge 扩展/脚本的环境。

## OBSERVE

- 目标站点 registrable domain（如 `example.com`）。
- 登录后应出现的 probe 元素（可选，用于 bridge 后继续 exec）。

## CLASSIFY/CHOOSE

- `expected_domain` 必填，与 bridge 导入 URL 的 host 校验（精确或子域）。
- `request_user_gate(gate_type=session_bridge, expected_domain=..., prompt=「请在本地浏览器完成登录并导入会话」)`。
- **`bridge_token` 仅存在于面板内存**（前端 `createBridgeToken`），**不出现在 SSE / tool output**。

## ACTION

1. 单独一步 session_bridge gate。
2. 用户在面板按指引于本地浏览器执行 bridge；cookie 经 vault 导入远程 session。
3. gate resume 后 `context.add_cookies`；继续 inspect 登录态。

## DO NOT

- 向用户索要 cookie 字符串粘贴到聊天。
- 跳过 domain 校验。
- 与 exec 同 step。

## FAILURE/NEXT STEP

- `domain mismatch` → 说明期望域名，请用户在正确站点导入。
- token 过期 → 重新 gate。
- 导入后仍无登录态 → inspect + `browser_capture_state`，必要时换 gate 类型。

Dev 参考：`dev_fixtures/browser_gate_fixtures/bridge_stub.sh`。
