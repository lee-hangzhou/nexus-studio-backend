# 指针轨迹 — 行为风控

删掉人机 gate 后，轨迹由模型在 `execute_python` 中生成；平台只执行 `browser_challenge_dispatch_pointer_trace`。

## 轨迹应满足

- 必须 `down` → 多个 `move` → `up`；禁止单次 jump 到终点。
- 禁止匀速、禁止严格水平直线：允许小幅度 Y 抖动。
- 总时长落在人类合理区间（通常数百 ms 量级）；相邻点 `t` 间隔不均匀。
- 先加速后减速即可，勿追求过于光滑的数学曲线。
- 每次 attempt 的步数/时长应有变化。

## 反例（勿生成）

- 匀速直线、零 Y 变化、总时长 < 100ms、每次 attempt 点数完全相同。

## 与 travel 几何的关系

1. `browser_challenge_read_geometry` 给出 `travel.left` / `travel.right`。
2. 目标 handle 中心 x 落在 travel 范围内。
3. 轨迹起点 ≈ handle 中心，终点 ≈ 目标 x；Y 在 drag_corridor 内微抖。

## 调试工件

`CHAT_CAPTCHA_DEBUG_ARTIFACTS=true` 时写入 `workspace/raw/captcha/{attempt_id}/05_trace.json`。

## 明确不做

- 请求参数加密逆向、绕过 Playwright 直接伪造验证 API。
- 深度学习行为验证码专用模型（不在当前范围）。
