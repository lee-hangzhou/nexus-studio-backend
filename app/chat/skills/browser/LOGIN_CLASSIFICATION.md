# Login — DOM to tool capability

Match **page facts** to tools that **can** handle them. Not a keyword router.

## WHEN TO USE

- **Default first step** on a site after restore: not logged in → **`login_method` gate** then target auth gate before task navigation.
- Login UI visible on site entry — inspect methods, open `login_method` with choices from DOM.
- Multiple login methods visible — **never** pick in chat; user selects on panel ([LOGIN_METHOD.md](LOGIN_METHOD.md)).
- Mid-work session lost — pause work, auth, resume.

## WHEN NOT TO USE

- `browser_restore_session` + probe confirms logged-in state and target data already reachable.
- Keyword-only guesses without DOM.

## OBSERVE → tools that can handle

| DOM fact | Tool / doc |
|----------|----------------|
| One or more login tabs/modes | **`login_method`** — [LOGIN_METHOD.md](LOGIN_METHOD.md) first |
| Username + password fields | `credentials` — [CREDENTIALS.md](CREDENTIALS.md) (after `login_method`) |
| Phone + send SMS + code | `phone_otp` — [PHONE_OTP.md](PHONE_OTP.md) |
| QR image/canvas | `qr_scan` — [QR_SCAN.md](QR_SCAN.md) |
| Captcha image + input | `image_captcha` — [IMAGE_CAPTCHA.md](IMAGE_CAPTCHA.md) |
| Slider / drag track | `browser_challenge_*` + `cv_*` — [INTERACTIVE_AUTO.md](INTERACTIVE_AUTO.md) (auth chain or post-login only) |
| Agreement before submit | [LOGIN_PREP.md](LOGIN_PREP.md) + `browser_exec_script` click |
| Acknowledge only, no secrets | `confirm` — [CONFIRM.md](CONFIRM.md) |
| No user-viable path | `signal_browser_blocked` |

**Not available:** `session_bridge` — do not use.

## ACTION

1. Inspect login DOM — include agreement toggle state ([INSPECT.md](INSPECT.md)).
2. **Agreement prep (blocking if present)** — [LOGIN_PREP.md](LOGIN_PREP.md): exec + verify checked; skip only when no agreement UI.
3. **`login_method`** with choices from inspect (even if only one method).
4. After user picks → re-inspect tab → open matching gate/challenge doc; selectors from inspect only.
5. **Post-auth verify (blocking)** — [AUTH_PRIORITY.md](AUTH_PRIORITY.md): gate success ≠ logged in; dedicated inspect/probe step.
6. **Work** loop (goto targets, capture, deliver) — **only after** step 5 passes.

## DO NOT

- Map page buttons or sliders to `confirm`.
- Put captcha in `credentials.fields`.
- Silent method choice when multiple real options exist — **panel only**.
- Open `qr_scan` / `credentials` / `phone_otp` before `login_method` completes.

## FAILURE / NEXT STEP

- `login_method_required` → call `login_method` first.
- Unclear → `browser_capture_state` + rebuild choices for `login_method`.
- Preflight `missing_selector` → inspect again.
