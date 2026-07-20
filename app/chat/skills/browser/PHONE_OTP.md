# Phone OTP

## WHEN TO USE

- Inspect shows SMS OTP flow: send button + code input (+ optional phone field).
- Two scenarios below; pick by **DOM**, not user wording alone.

## WHEN NOT TO USE

- Email OTP / TOTP app with no send button → `confirm` or `signal_browser_blocked`.
- Missing `send_selector` or `code_selector` — inspect first.

## Scenarios

| | **A — OTP login tab** | **B — post-login verify** |
|--|------------------------|---------------------------|
| DOM | Phone input + send + code | Phone often fixed/displayed; send + code |
| phone gate | Usually required | **May skip** |
| Send | `browser_trigger_otp_send` **required** | **Required before code gate** |
| code gate | After send | After send |

## OBSERVE

- `phone_selector`, `send_selector`, `code_selector`, `submit_selector`.
- Countdown / “已发送” → send already done; use code gate only (`otp_already_sent`).

## ACTION — always separate model steps

**A:** `request_user_gate(phone_otp, phase=phone)` → read `otp_flow_id` → `browser_trigger_otp_send` → `request_user_gate(phase=code)`.

**B:** inspect → `browser_trigger_otp_send(send_selector)` (may auto-start flow if no `otp_flow_id`) → `request_user_gate(phase=code)`.

After code gate succeeds → **post-auth verify** (blocking) per [AUTH_PRIORITY.md](AUTH_PRIORITY.md) before task URLs.

Agent clicks send. **Never** tell user to click send on the page in gate prompt.

## DO NOT

- `browser_exec_script` instead of `browser_trigger_otp_send` for send button.
- code gate before send (unless countdown proves sent).
- Repeat send same flow (`otp_already_sent`).

## FAILURE / NEXT STEP

- `otp_already_sent` → code gate only.
- `unknown otp_flow_id` → restart from phone gate or B: trigger send with begin flow.
- `login_failed` / `gate_cancelled` → explain; retry if user asks.
