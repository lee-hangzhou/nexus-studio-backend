# Image Captcha Gate

## WHEN TO USE

- inspect 显示**静态**字符/数字验证码图 + 输入框（非滑块、非行为验证）。

## WHEN NOT TO USE

- 滑块/点选/轨迹验证 → [INTERACTIVE_AUTO.md](INTERACTIVE_AUTO.md)。
- 仅用户名密码无验证码 → [CREDENTIALS.md](CREDENTIALS.md)。
- 在 `credentials.fields` 里加 `captcha` → 后端拒绝，须用本 gate。

## OBSERVE

- `captcha_image_selector`：验证码图片节点。
- `captcha_input_selector`：用户输入框。
- `submit_selector`：提交按钮。
- 验证码是否随刷新变化（本 gate 截图**不**自动刷新，与 QR 不同）。

## CLASSIFY/CHOOSE

- `fields` 必填，含 `captcha`（及必要时 `username`/`password` 若同表单一并收集）。
- 三选择器 + `submit_selector` 均来自 inspect。

## ACTION

1. inspect 确认 captcha 类型为静态图。
2. 单独一步 `request_user_gate(gate_type=image_captcha, fields=[...], captcha_image_selector=..., captcha_input_selector=..., submit_selector=..., prompt=「请输入图中验证码」)`。
3. 面板展示 captcha 截图；用户提交后后端 fill 输入框并 click submit。

## DO NOT

- fill 验证码**图片**元素（只 fill input）。
- 用 OCR/脚本自动识别代替用户（除非用户明确要求且合规）。
- 与 exec 同 step。

## FAILURE/NEXT STEP

- 验证码错误导致 `login_failed` → 重新 inspect，必要时再次 gate（新截图）。
- `missing_selector` → 重新 inspect captcha 节点。
