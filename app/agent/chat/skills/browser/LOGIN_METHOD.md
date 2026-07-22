# Login method gate

User **must** pick the login method on the **panel**, not in chat. The agent supplies options from inspect; the mechanism blocks target auth gates until the user selects.

## WHEN TO USE

- After inspect finds one or more login methods (tabs, links, or distinct flows).
- **Always** before `credentials`, `phone_otp`, `qr_scan`, or `image_captcha` in the auth chain (even a single method — one button on the panel).

## WHEN NOT TO USE

- `browser_restore_session` + probe confirms logged-in and target data already reachable.
- `confirm` / non-login gates.

## OBSERVE

From inspect, build `choices` — one entry per real option the user can pick:

| Field | Source |
|-------|--------|
| `id` | Stable slug you assign (e.g. `qr`, `pwd`, `sms`) |
| `label` | User-visible text from DOM (tab label, link text) |
| `target_gate` | `credentials` / `phone_otp` / `qr_scan` / `image_captcha` |
| `tab_selector` | Playwright selector to activate that tab/mode (from inspect only) |

## ACTION

1. `browser_exec_script` — open site, inspect login methods **and agreement toggle** ([LOGIN_PREP.md](LOGIN_PREP.md)).
2. If agreement UI unchecked → complete LOGIN_PREP (exec + verify) **before** step 3.
3. **One step** `request_user_gate(gate_type=login_method, choices=[...], prompt=「请选择登录方式」)`.
4. User picks on panel → tool_result `{status: choice_submitted, choice_id, target_gate}`; optional `tab_click_dispatched`.
5. Mechanism clicks `tab_selector` if present; you re-inspect that tab for selectors (including agreement state on that tab).
6. **Next step** — open matching target gate doc (`CREDENTIALS.md`, `QR_SCAN.md`, etc.) with selectors from live DOM; then **post-auth verify** per [AUTH_PRIORITY.md](AUTH_PRIORITY.md) before work.

## DO NOT

- Ask “您想用哪种登录？” in chat and skip `login_method` gate.
- Open `qr_scan` / `credentials` / `phone_otp` before user completed `login_method` (mechanism returns `login_method_required`).
- Guess `tab_selector` or omit `label`.

## FAILURE / NEXT STEP

- User cancels → ask whether to retry or stop; `gate_cancelled`. May open `login_method` again.
- `login_method_required` → you skipped the panel; call `login_method` first.
- Wrong tab after pick → re-inspect; adjust `tab_selector` in next `login_method` if needed.
