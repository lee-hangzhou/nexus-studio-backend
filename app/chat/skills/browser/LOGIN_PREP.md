# Login prep — agreement and custom controls

## WHEN TO USE

- **Inside the auth chain only** — not for every browser task.
- After inspect shows terms/privacy acceptance UI on the login form **and before** `login_method` / credentials / phone_otp submit.
- **Blocking:** if agreement UI exists and is unchecked, you **must** complete this doc before opening target auth gates or clicking login submit.

## WHEN NOT TO USE

- No agreement UI on the page (no “阅读并同意”, “服务协议”, “隐私政策”, terms/privacy toggle).
- Page already authenticated; going straight to capture.

## OBSERVE

- Text like “阅读并同意”, “服务协议”, “隐私政策”, “terms”, “privacy”.
- Control may **not** be `input[type=checkbox]` — often a custom `label`, `div`, `span`, or `i` icon with toggle class (e.g. `nochecked` / `checked`).
- Agreement blocks may embed links; clicking link text can open a new tab — click the **toggle/icon**, not the link.

Record in inspect output: selector hint, current class/aria/checked state, visible or not.

## ACTION

1. During auth inspect, **explicitly** look for agreement UI — do not infer “no checkbox” from empty `input[type=checkbox]` scan alone.
2. If visible and **unchecked** → **one step** `browser_exec_script`: click toggle/icon (prefer `label[for=…] i`, icon bbox, or `mouse.click` on icon center).
3. **Verify (blocking)** — same step or immediate next inspect script:
   - Re-read toggle class, `aria-checked`, or `input.checked`;
   - State **must** show checked; if unchanged, **change click target** (`locator().click()`, `mouse.click`, parent label) — do not repeat identical failed click.
4. Only after verified checked → continue to `login_method` or target auth gate.

## DO NOT

- Skip agreement because only a standard checkbox scan ran empty.
- Open `request_user_gate(credentials)` or rely on gate submit while toggle still unchecked.
- Rely on the user saying “remember to check the box” — this is a default auth prep step when UI exists.
- Use `confirm` gate instead of clicking the control.
- Assume `label.click()` worked without reading toggle state afterward.

## FAILURE / NEXT STEP

- Cannot find control after expanded inspect → optional `browser_capture_state` and describe what you see; ask user only if truly ambiguous.
- Opened terms tab by mistake → close extra tab, retry toggle on main page.
- Toggle will not stay checked → describe UI state; do not submit credentials.
