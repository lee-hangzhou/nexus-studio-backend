# Failure Policy

## WHEN TO USE

- 任意 browser / gate 工具返回 `success=false` 或带 `error_type`。
- 规划是否重试、换 gate、或终止 turn。

## WHEN NOT TO USE

- 成功路径上预防性重试（浪费步数、触发 repeat guard）。

## OBSERVE

| `error_type` | 含义 |
|--------------|------|
| `gate_cancelled` | 用户取消面板 |
| `login_method_required` | 未先完成 `login_method` 就开了 credentials/qr/phone/image_captcha |
| `login_failed` | fill/submit 后 probe 未通过 |
| `qr_element_not_visible` | QR selector bbox 不可见/过小（截图前） |
| `qr_asset_invalid` | QR png 尺寸低于阈值 |
| `qr_not_decodable` | QR png 无法解码 |
| `gate_batch_isolation` | gate 与其他 tool 同 step |
| `missing_selector` | 预检缺选择器 |
| `invalid_arguments` | 参数不合法（如 confirm 带 fields） |
| `otp_already_sent` | 重复 trigger send |
| `challenge_element_not_found` | C/T/H 或截图 selector 失效 |
| `challenge_geometry_invalid` | travel 几何不合法 |
| `cv_low_confidence` | CV 置信度过低 |
| `probe_timeout` | 验效 selector 超时（legacy） |
| `challenge_outcome_failed` | probe 观察到 failure 信号 |
| `challenge_outcome_dismissed` | challenge panel 消失 |
| `challenge_outcome_inconclusive` | 超时或无明确信号 |
| `pointer_dispatch_failed` | 轨迹格式/施动失败 |
| `browser_blocked` | `signal_browser_blocked` 正常退出 |
| `browser_error` | Playwright/会话异常 |

## CLASSIFY/CHOOSE

- **应先登录（流程层）**：未登录态在目标 URL 上出现 WAF / 滑块 / “访问验证” → 停止 work；走完整 auth 链（含 [LOGIN_PREP.md](LOGIN_PREP.md) 与 post-auth verify），见 [AUTH_PRIORITY.md](AUTH_PRIORITY.md)。
- **Post-auth verify 未通过**：gate 已成功但 inspect 仍匿名 → **禁止** goto 任务 URL；re-inspect 登录态或重走 auth，勿对用户声称已登录。
- **QR 自证失败**：`qr_element_not_visible` / `qr_asset_invalid` / `qr_not_decodable` → 不展示扫码面板；重 inspect / 刷新 QR / 回 `login_method`。
- **登录方式**：`login_method_required` → 先 `login_method` gate，禁止对话代选。
- **可重试（改参数后）**：`missing_selector`、`invalid_arguments`、`challenge_element_not_found` → 重新 inspect。
- **求解/轨迹层**：`cv_low_confidence`、`challenge_geometry_invalid`、`pointer_dispatch_failed`、`challenge_outcome_failed` → 重截图/改轨迹；读 `observations`。
- **Challenge 层**：`challenge_outcome_dismissed` / `challenge_outcome_inconclusive` → 读 facts；禁止未 verify 再次 write；勿 goto 首页或关登录弹窗除非 `session_invalid` 事实（见 [SESSION_PERSISTENCE.md](SESSION_PERSISTENCE.md)）。
- **同站 auth 缓存**：`browser_auth_status.auth_flags`（`login_method_selected`、`credentials_submitted`）为流程事实；是否重开 gate 由模型读 facts + probe 决定，机制不自动拦截。
- **可能环境层**：`probe_timeout` / `challenge_outcome_inconclusive` 且 CV/geometry 正常 → 考虑行为风控，见 [INTERACTIVE_AUTO.md](INTERACTIVE_AUTO.md)。
- **用户决策**：`gate_cancelled`、`login_failed` → 说明原因，问是否继续。
- **流程修正**：`gate_batch_isolation` → 拆 step；`otp_already_sent` → 跳过 send 进 code phase。
- **终止 turn**：`browser_blocked` → 已向用户说明，勿再调模型 loop。

## ACTION

1. 读 tool_result 结构化 `error_type`，勿 parse 自由文本。
2. 用产品语言向用户说明（不含选择器/秘密）。
3. 最多一次有依据的重试；勿 blind loop。

## DO NOT

- 忽略 `error_type` 继续相同 gate 参数。
- 用 `credentials` 或 `image_captcha` 替代滑块自动化。
- 遇 blocked 仍索要 cookie。

## FAILURE/NEXT STEP

- browser 工具错误回传模型自愈；整轮仅受 `max_tool_calls` / wall clock 限制（browser 不参与 repeat guard 熔断）。
- 无法分类 → `browser_capture_state` + 请求用户澄清。
