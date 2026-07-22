# Auth priority and risk control

Default on any site: **auth first**, then **work**. Anonymous hits on task URLs trigger WAF/risk controls — login before substantive navigation, not after a failed scrape.

## Default path

1. `browser_restore_session` (optional).
2. If `need_login` or not logged in → **auth chain** (each blocking step is a **separate model step**):
   1. **Inspect** login DOM — tabs, fields, QR node, **agreement toggle** ([INSPECT.md](INSPECT.md), [LOGIN_PREP.md](LOGIN_PREP.md)).
   2. **Agreement prep** — if visible agreement/toggle is **unchecked**, exec click/toggle per [LOGIN_PREP.md](LOGIN_PREP.md); **verify** checked state changed before continuing. If no agreement UI, skip.
   3. **`login_method` gate** — even one method ([LOGIN_METHOD.md](LOGIN_METHOD.md)).
   4. **Target auth gate** — credentials / phone_otp / qr_scan / image_captcha per [LOGIN_CLASSIFICATION.md](LOGIN_CLASSIFICATION.md).
   5. **Post-auth verify (agent)** — **mandatory** before any task URL or capture:
      - Gate tool_result（`submit_dispatched`、`choice_submitted`、`qr_acknowledged`）**does not** prove logged in.
      - One dedicated verify step: `browser_auth_status` with `login_probe_selector` **or** inspect logged-in DOM facts.
      - **Fail verify** → do not goto task targets; re-inspect auth or ask user.
   6. **Work** → deliver.
3. If `logged_in` and target data reachable on minimal probe → skip auth, go to work.

See [LOGIN_METHOD.md](LOGIN_METHOD.md) and [LOGIN_CLASSIFICATION.md](LOGIN_CLASSIFICATION.md).

## Allowed while not logged in

- Open site **origin** (homepage / login entry).
- Inspect login DOM (tabs, fields, QR node facts).
- Clicks needed to expose login UI for inspect or for `login_method` tab switching (mechanism may click tab after user picks method).

These are **not** task work.

## Forbidden while not logged in

- Goto **task targets** (user profiles, article lists, API-backed pages the user asked to capture).
- `context.request` batch fetch, paginated capture to `raw/`.
- `browser_challenge_*` on **task URLs** when still anonymous (WAF / slider / “访问验证” on task navigation).

## Risk-control facts (observable)

| Page fact | Next action |
|-----------|-------------|
| Title/body: “访问验证”, “滑动验证”, Aliyun WAF shell | Stop work; auth chain ([AUTH_PRIORITY](AUTH_PRIORITY.md) → [LOGIN_METHOD](LOGIN_METHOD.md)) |
| Task URL returns challenge instead of content | Same — **do not** run [INTERACTIVE_AUTO.md](INTERACTIVE_AUTO.md) instead of login |
| Challenge on **login flow** while establishing session | [INTERACTIVE_AUTO.md](INTERACTIVE_AUTO.md) inside auth chain only |
| Anonymous homepage shows public feed | **Not** proof that task targets/API are reachable without login |

## DO NOT

- “Homepage feed visible → scrape task targets anonymously.”
- “Try API / profile URL first, login when blocked.”
- Burn challenge steps on WAF while still anonymous instead of opening login for the user.
- Open `credentials` / submit login while agreement toggle is still unchecked ([LOGIN_PREP.md](LOGIN_PREP.md)).
- Treat gate success as logged in — **always** run post-auth verify (step 2.5) before task navigation.

## FAILURE / NEXT STEP

- WAF on task URL → re-inspect login UI → `login_method` → target gate. See [FAILURE_POLICY.md](FAILURE_POLICY.md).
- Mid-work session lost → pause capture; auth; resume work. See [CAPTURE.md](CAPTURE.md).
