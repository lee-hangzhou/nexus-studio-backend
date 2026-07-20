# Credentials Gate

## WHEN TO USE

- inspect 显示用户名/密码表单，**无**独立图片验证码字段（验证码见 [IMAGE_CAPTCHA.md](IMAGE_CAPTCHA.md)）。

## WHEN NOT TO USE

- 页面含图片验证码 → `image_captcha`（`credentials` 的 `fields` **不可**含 `captcha`）。
- 仅手机 OTP、仅 QR、滑块 → 对应 gate。
- 想在一个 step 里 gate + exec → 禁止（batch isolation）。

## OBSERVE

- `username` / `password` 输入框及 submit 按钮。
- 登录成功探测元素（可选 `login_probe_selector`）。

## CLASSIFY/CHOOSE

- `fields` 必填：至少 `username`、`password`（每项用 `name` 字段声明；`key` 仅作兼容别名）。
- 选择器：`username_selector`、`password_selector`、`submit_selector`（均来自 inspect）。

## ACTION

0. **Agreement prep (blocking if UI present)** — per [LOGIN_PREP.md](LOGIN_PREP.md): inspect for agreement toggle; if unchecked, exec + verify checked **before** this gate. Do not open credentials gate while agreement still unchecked.
1. inspect 确认 username / password / submit 选择器；记录 agreement 已勾选或不存在。
2. **单独一步** `request_user_gate(gate_type=credentials, fields=[...], username_selector=..., password_selector=..., submit_selector=..., login_probe_selector=..., prompt=「请输入账号密码」)` — prefer `login_probe_selector` from inspect (logged-in DOM fact).
3. **Post-auth verify (blocking)** — gate 返回 `submit_dispatched` **不等于**已登录。下一步 `browser_auth_status`（带 `login_probe_selector`）或 inspect DOM；**通过前禁止** goto 任务 URL 或抓取。
4. 验证通过后进入 work（抓取等）。

## DO NOT

- 在 `browser_exec_script` 中 `fill` 密码。
- 向用户索要选择器。
- 与 `browser_exec_script` 同 step 调用 gate。
- 跳过 [LOGIN_PREP.md](LOGIN_PREP.md) 直接开 gate（页面上有协议时）。
- 仅凭 `submit_dispatched` / `choice_submitted` 继续抓取 — 必须先 post-auth verify（见 [AUTH_PRIORITY.md](AUTH_PRIORITY.md)）。

## FAILURE/NEXT STEP

- 同站已 `credentials_submitted`（读 `browser_auth_status`）→ 由模型决定 probe、challenge 或是否再开 gate；机制不自动拦截。
- `login_failed` → 说明可能原因，请用户核对，勿循环重试。
- `gate_cancelled` → 停止登录流程，询问是否继续。
- `gate_batch_isolation` → 拆成独立 step 重试 gate。
