# Web tool boundaries

Choose tools by **capabilities**, not by site name or user phrasing. Read tool descriptions for limits.

## Four dimensions

1. **URL known?** Unknown → `web_search`. Known → not search (usually `web_fetch` or browser).
2. **Browser session required?** Needs cookies, JS rendering, gates, `raw/` batch capture, or authenticated `context.request` → browser.
3. **Deliverable?** Answer in chat from one page → may suffice with `web_fetch`. Files under `workspace/raw/` + `publish_file` → browser + ETL.
4. **Previous tool enough?** `web_fetch` 403/401, empty shell, or login HTML in body → may escalate to browser.

## web_search

**WHEN TO USE:** Need to discover URLs, titles, snippets, or recency; no target URL yet.

**WHEN NOT TO USE:** URL already given; need full page behind auth; need workspace capture.

**FAILURE / NEXT:** Pick a URL from results → `web_fetch` or browser per dimensions 2–3.

## web_fetch

**WHEN TO USE:** URL known; anonymous HTTP GET is enough; static or mostly static HTML/text.

**WHEN NOT TO USE:** Login, gates, sliders, JS-only content, paginated `raw/` capture, session cookies.

**OBSERVE:** Response status in tool_result; body length; obvious login wall or empty app shell.

**FAILURE / NEXT:** 403/401, login page body, or useless shell → browser. Do not blind-retry same URL many times.

## browser (this skill)

**WHEN TO USE:** Dimensions 2 or 3 require a live Playwright session; or fetch failed for auth/JS reasons.

**WHEN NOT TO USE:** Single public URL text already returned by `web_fetch`; offline files only → `execute_python`.

**SESSION LOOP (default):** Restore or establish login on the site → then navigate and act until done (see [SKILL.md](SKILL.md)). Do not probe task URLs in an anonymous session when the workflow needs that site’s session.

**FAILURE / NEXT:** See [FAILURE_POLICY.md](FAILURE_POLICY.md). Anonymous WAF/slider → auth gate, not endless challenge automation. Auth blocked mid-capture → pause capture, auth, resume.

## DO NOT

- Route by keywords (“登录”, site names, product names).
- Open browser “just in case” when fetch already returned usable content.
- Use browser for tasks `web_search` alone can answer without a page fetch.
