# QR Scan Gate

## WHEN TO USE

- inspect 显示 App 扫码登录 QR（`img` / `canvas`）。

## WHEN NOT TO USE

- 密码/OTP 登录（对应 gate）。
- QR 未刷新即 gate（易展示过期码）。

## OBSERVE

- `qr_image_selector`：QR 渲染节点。
- 刷新控件：覆盖层文字「点击刷新」、可点击 canvas 等（**单独 exec 用**，不进 gate 参数）。

## CLASSIFY/CHOOSE

- 展示 QR 前：独立 `browser_exec_script` 点击刷新 + 等待 1–2s；确认 QR 节点 bbox 可见（inspect `naturalWidth` / rect）。
- gate：`request_user_gate(qr_scan, qr_image_selector=..., prompt=「请用手机扫码，完成后点确认」)` — **only after `login_method` completed**.
- 机制自证：截图须通过尺寸 + decode 校验；失败返回 `qr_*` error_type，**不开面板**。
- 面板每 **3s** 轮询刷新 QR 截图；用户点「我扫好了」提交（v1 无自动 probe）。

## ACTION

0. **Agreement prep** — if login modal shows agreement toggle unchecked, complete [LOGIN_PREP.md](LOGIN_PREP.md) **before** QR gate (blocking).
1. 打开页面 / 切换扫码 tab → inspect。
2. exec 刷新 QR → wait。
3. 单独一步 `request_user_gate(qr_scan)`。
4. **Post-auth verify (blocking)** — `qr_acknowledged` **≠** logged in. 用户点确认后，**单独一步** `browser_exec_script` inspect 登录态（同 [AUTH_PRIORITY.md](AUTH_PRIORITY.md)：profile/logout/登录按钮消失等）。**未通过禁止** goto 任务 URL。

## DO NOT

- 让用户打开桌面浏览器扫码（QR 在聊天面板内展示）。
- 向用户索要 `qr_image_selector`。
- 与 exec 同 step 调用 gate。
- 用户点「我扫好了」后直接抓取 — 必须先 step 4 verify。

## FAILURE/NEXT STEP

- `qr_element_not_visible` / `qr_asset_invalid` / `qr_not_decodable` → re-inspect selector, refresh QR exec, retry gate; do not show broken QR to user.
- QR 过期仍无法 refresh → `browser_capture_state` + 说明，必要时 `signal_browser_blocked`。
- 用户取消 → 可重新 `login_method` 换方式（见 [LOGIN_CLASSIFICATION.md](LOGIN_CLASSIFICATION.md)）。
