WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "通过博查搜索引擎检索全网网页，获取标题、URL、摘要与发布时间。"
            "适用：需要联网事实、新闻、政策、价格、版本、事件进展等 system 与附件无法覆盖的时效信息。"
            "已有明确 URL 时优先用 web_fetch 直接抓取。"
            "构造 query：简短关键词或自然语言，结合 system 中的当前日期，含实体名；勿整段复制用户原话。"
            "freshness 控制时间范围：oneDay/oneWeek/oneMonth/oneYear 用于近期信息；"
            "YYYY-MM-DD 或 YYYY-MM-DD..YYYY-MM-DD 指定日期；"
            "不限或历史材料可省略 freshness（由 API 默认）或显式传 noLimit。"
            "使用返回：阅读每条「发布时间」与摘要，对照 system 当前时间判断新旧；来源冲突时优先较新、较权威；"
            "不足时可换 query 或调整 freshness 再搜。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词，简短明确，可含实体名与时间相关词",
                },
                "freshness": {
                    "type": "string",
                    "description": (
                        "可选。时间范围：noLimit、oneDay、oneWeek、oneMonth、oneYear，"
                        "或 YYYY-MM-DD、YYYY-MM-DD..YYYY-MM-DD。"
                        "省略则不传该参数，由 API 默认。"
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

WEB_FETCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Anonymous HTTP GET for a known URL — no JavaScript, no cookies, no browser session. "
            "Use when static HTML/text is enough and the page is publicly reachable. "
            "Returns truncated body (~12k chars). "
            "On 403/401, empty shell, login wall, or JS-only content: read tool_result and upgrade to "
            "browser_exec_script (session + gate). Do not retry fetch hoping cookies appear."
        ),
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL to fetch"}},
            "required": ["url"],
        },
    },
}

EXECUTE_PYTHON_TOOL = {
    "type": "function",
    "function": {
        "name": "execute_python",
        "description": (
            "Run Python in an isolated Docker sandbox mounted to the conversation workspace. "
            "About 512MB memory limit. "
            "Optional packages: pip installs into a host-mounted global package cache (persists across turns), "
            "then runs __run__.py in the same container. Pip step uses network; code execution does not. "
            "Sandbox image preinstalls global npm modules pptxgenjs and docx; do not npm install at runtime (no network). "
            "To invoke skill scripts (python scripts/..., node ..., soffice --headless, etc.), "
            "use subprocess.run([...]) inside the Python code passed to this tool. "
            "On failure the tool returns structured error_type (e.g. sandbox_timeout, sandbox_error)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source code"},
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional pip packages to install first",
                },
                "allow_network": {
                    "type": "boolean",
                    "description": (
                        "When true, Python execution uses network (for ETL/API calls). "
                        "Default false; pip install always uses network when packages are set."
                    ),
                },
            },
            "required": ["code"],
        },
    },
}

READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a text file from the conversation workspace (relative path)."
        ),
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path in workspace"}},
            "required": ["path"],
        },
    },
}

WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Write UTF-8 text content to a file in the conversation workspace. "
            "Only plain-text suffixes are allowed (.txt, .md, .csv, .json, .py, etc.). "
            "Binary or Office formats must be created via execute_python."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
}

LIST_FILES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": "List files in the conversation workspace directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative directory, default '.'"},
            },
        },
    },
}

PUBLISH_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "publish_file",
        "description": (
            "Upload a workspace file so the user can download it in chat. "
            "You MUST call this when the workspace already contains a file the user should receive; "
            "describing delivery in natural language without calling publish_file is incomplete. "
            "path is relative to the workspace root. Cannot publish files under skills/."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path of output file"},
                "filename": {"type": "string", "description": "Optional upload filename"},
            },
            "required": ["path"],
        },
    },
}

BROWSER_EXEC_SCRIPT_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_exec_script",
        "description": (
            "Run async Python with Playwright `page`, `context`, `Path`, `workspace`, `raw_dir` already in scope "
            "inside the conversation browser session — the session is connected for you. "
            "Language: async Python only; use sync Playwright API on `page`/`context` (e.g. "
            "`await page.goto(url, wait_until='domcontentloaded')`). "
            "Do NOT use JavaScript object literals, camelCase waitUntil, or a third positional dict to goto. "
            "Never import async_playwright or call browser.connect(); use `page` / `context` directly. "
            "Write-verify: any side-effect (goto, click, fill) is a write — next model step must read/verify "
            "(see skills/browser/WRITE_VERIFY.md). "
            "Auth-first: when browser_restore_session returned need_login, use this tool only to open "
            "the site origin and inspect login DOM until auth completes. "
            "Do not goto task targets, paginate, scrape, or run browser_challenge_* while still anonymous. "
            "After login, use for navigation, page inspection, scraping, and writing artifacts under "
            "workspace/raw/. "
            "Prefer web_fetch when URL is known and anonymous HTTP suffices. "
            "Interactions: page.locator(...).click() / page.fill(...) — not inside page.evaluate. "
            "page.evaluate is for standard browser JS/DOM only; querySelector must use standard CSS "
            "(no Playwright :has-text / :has). "
            "Before request_user_gate: run a dedicated inspect step to read the live DOM. "
            "Login/credentials must go through request_user_gate, not this tool. "
            "On failure returns structured error_type (invalid_arguments, browser_error, browser_unavailable)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Async Python source using page/context"},
            },
            "required": ["code"],
        },
    },
}


BROWSER_SIGNAL_BLOCKED_TOOL = {
    "type": "function",
    "function": {
        "name": "signal_browser_blocked",
        "description": (
            "Call when automated verification is exhausted and the user must take over manually, "
            "or when behavior-risk / session fingerprint blocks automation entirely. "
            "Explain the situation in natural language before calling; this tool signals the turn "
            "to stop without further tool calls. "
            "Do not ask for cookies or tokens; use this tool as the exit instead. "
            "No parameters required."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


BROWSER_CAPTURE_STATE_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_capture_state",
        "description": (
            "Capture an ops debug snapshot of the current browser page: metadata "
            "(url, title, text_preview, screenshot_path, screenshot_url) plus a png under "
            "workspace/.browser/debug/ for human troubleshooting. "
            "Not shown in the user UI — use text_preview in your reasoning; png is for manual eyeball. "
            "Call when diagnosis is needed (failures, ambiguous DOM, post-mortem), not as user-facing evidence. "
            "When need_login or still anonymous on an auth-gated site, do not use capture instead of "
            "request_user_gate to progress the workflow. "
            "After this tool returns, do not reproduce url, title, or page text verbatim in your reply unless "
            "the user explicitly asked for those fields."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Optional short filename label; do not encode semantic conclusions here.",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full page when true; current viewport when false. Default true.",
                },
                "text_max_chars": {
                    "type": "integer",
                    "description": "Maximum page text preview characters to return. Default 1200.",
                },
            },
            "required": [],
        },
    },
}


REQUEST_USER_GATE_TOOL = {
    "type": "function",
    "function": {
        "name": "request_user_gate",
        "description": (
            "Pause the turn for user interaction: login method pick, credentials, phone OTP, QR scan, "
            "image captcha, or confirm. "
            "Auth chain: after inspect, call gate_type=login_method with choices from DOM; user picks on "
            "panel; then open target gate (credentials/qr_scan/phone_otp/image_captcha). "
            "Mechanism returns login_method_required if target auth gate called before login_method completes. "
            "Default when browser_restore_session returns need_login=true — see skills/browser/AUTH_PRIORITY.md. "
            "Must be the ONLY tool_call in the current model step. "
            "Call only after browser_exec_script opened the page AND inspect discovered gate facts "
            "and CSS selectors from the live DOM. "
            "Never ask the user for selectors. Never put credentials in browser_exec. "
            "For image_captcha: captcha_image_selector + captcha_input_selector + submit_selector. "
            "For phone_otp: phase=phone then phase=code in separate steps (scenario A); "
            "scenario B may skip phone gate — always browser_trigger_otp_send before code gate. "
            "For qr_scan: user taps done after scanning. "
            "For confirm: simple user acknowledgment only — not for sliders or page buttons. "
            "Sliders / drag / gap puzzles: use browser_challenge_* + cv_* tools, NOT this gate. "
            "session_bridge is NOT wired — do not call. "
            "Secrets never appear in tool output or SSE."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "gate_type": {
                    "type": "string",
                    "enum": [
                        "login_method",
                        "credentials",
                        "phone_otp",
                        "qr_scan",
                        "image_captcha",
                        "confirm",
                        "session_bridge",
                    ],
                },
                "choices": {
                    "type": "array",
                    "description": "Required for login_method: user-facing login options from inspect.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Stable choice id, e.g. qr, pwd, sms"},
                            "label": {"type": "string", "description": "User-visible label from DOM"},
                            "target_gate": {
                                "type": "string",
                                "enum": ["credentials", "phone_otp", "qr_scan", "image_captcha"],
                            },
                            "tab_selector": {
                                "type": "string",
                                "description": "Playwright selector to activate this login tab/mode",
                            },
                        },
                        "required": ["id", "label", "target_gate"],
                    },
                },
                "prompt": {"type": "string", "description": "User-facing instruction"},
                "phase": {
                    "type": "string",
                    "enum": ["phone", "code"],
                    "description": "Required for phone_otp",
                },
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Vault/submit key, e.g. username or password"},
                            "label": {"type": "string"},
                            "secret": {"type": "boolean"},
                        },
                        "required": ["name"],
                    },
                    "description": "Field definitions for credentials or image_captcha. Use name (not key).",
                },
                "username_selector": {"type": "string"},
                "password_selector": {"type": "string"},
                "submit_selector": {"type": "string"},
                "phone_selector": {"type": "string"},
                "code_selector": {"type": "string"},
                "captcha_image_selector": {"type": "string"},
                "captcha_input_selector": {"type": "string"},
                "qr_image_selector": {"type": "string"},
                "expected_domain": {"type": "string", "description": "For session_bridge and site_auth scope"},
                "scope_selector": {
                    "type": "string",
                    "description": "Optional login modal root; scopes tab/field selectors to avoid strict violations",
                },
                "login_probe_selector": {"type": "string"},
                "login_probe_timeout_ms": {"type": "integer"},
            },
            "required": ["gate_type", "prompt"],
        },
    },
}

BROWSER_CHALLENGE_READ_GEOMETRY_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_challenge_read_geometry",
        "description": (
            "Read challenge container/track/handle bounding boxes and travel range in css_viewport coordinates. "
            "Call after inspect found C/T/H selectors. Returns coordinate_space, scale, resolved_index. "
            "Optional scope_selector limits lookup to login/challenge modal root."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "container_selector": {"type": "string"},
                "track_selector": {"type": "string"},
                "handle_selector": {"type": "string"},
                "scope_selector": {"type": "string"},
                "content_selector": {
                    "type": "string",
                    "description": "Optional image/canvas selector for intrinsic width scale facts",
                },
                "attempt_id": {"type": "string", "description": "Optional debug artifact id"},
            },
            "required": ["container_selector", "track_selector", "handle_selector"],
        },
    },
}

BROWSER_CHALLENGE_SCREENSHOT_ELEMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_challenge_screenshot_element",
        "description": (
            "Screenshot a DOM element to a workspace-relative path. "
            "Returns image dimensions and bbox_page in css_viewport."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "path": {"type": "string", "description": "Workspace-relative image path"},
                "scope_selector": {"type": "string"},
                "attempt_id": {"type": "string"},
            },
            "required": ["selector", "path"],
        },
    },
}

BROWSER_CHALLENGE_DISPATCH_POINTER_TRACE_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_challenge_dispatch_pointer_trace",
        "description": (
            "Dispatch pointer down/move/up events in css_viewport pixel coordinates on the live page. "
            "Events must include type, x, y, and optional t (ms offset)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "attempt_id": {"type": "string"},
            },
            "required": ["events"],
        },
    },
}

BROWSER_CHALLENGE_WAIT_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_challenge_wait_probe",
        "description": (
            "Observe challenge outcome after pointer trace via multi-signal probe. "
            "Returns outcome (passed|failed|dismissed|inconclusive), observations, url_before/after. "
            "Provide success_selector (or legacy selector); optional failure_selector, panel_selector, "
            "retry_text_probe from inspect. Read outcome facts before next write."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Alias for success_selector"},
                "success_selector": {"type": "string"},
                "failure_selector": {"type": "string"},
                "retry_text_probe": {"type": "string", "description": "Visible retry text from inspect"},
                "panel_selector": {"type": "string", "description": "Challenge panel root for dismissed detection"},
                "scope_selector": {"type": "string"},
                "timeout_ms": {"type": "integer"},
                "attempt_id": {"type": "string"},
            },
            "required": [],
        },
    },
}

CV_IMAGE_INFO_TOOL = {
    "type": "function",
    "function": {
        "name": "cv_image_info",
        "description": "Return width, height, channels for a workspace image (pixel index coordinates).",
        "parameters": {
            "type": "object",
            "properties": {"image_path": {"type": "string"}},
            "required": ["image_path"],
        },
    },
}

CV_MATCH_TEMPLATE_TOOL = {
    "type": "function",
    "function": {
        "name": "cv_match_template",
        "description": (
            "OpenCV template match in image pixel coordinates. "
            "Returns dx, dy, confidence. Fails with cv_low_confidence when below threshold."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "template_path": {"type": "string"},
                "roi": {"type": "object"},
            },
            "required": ["image_path", "template_path"],
        },
    },
}

CV_FIND_GAP_X_TOOL = {
    "type": "function",
    "function": {
        "name": "cv_find_gap_x",
        "description": (
            "Find gap x candidates via column projection or background-piece diff. "
            "Provide background_path+piece_path or image_path. Fails loud on low confidence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "background_path": {"type": "string"},
                "piece_path": {"type": "string"},
                "image_path": {"type": "string"},
            },
            "required": [],
        },
    },
}

BROWSER_RESTORE_SESSION_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_restore_session",
        "description": (
            "Load saved Playwright storage_state for this conversation workspace and open/refresh "
            "the browser session. Use at the start of same-site follow-up work. "
            "Returns restored, logged_in, need_login, domain in tool_result output JSON. "
            "logged_in is true only when login_probe_selector is provided and visible after restore. "
            "When need_login is true (or logged_in is false after restore), auth chain starts with "
            "request_user_gate(login_method) then target gate — see skills/browser/AUTH_PRIORITY.md. "
            "Do not goto task targets, paginated capture, or browser_challenge_* while still anonymous. "
            "Missing storage on first visit is normal: open the site minimally to inspect login UI, "
            "then auth — not 'work first, login when blocked'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expected_domain": {
                    "type": "string",
                    "description": "Host to restore, e.g. example.com; omit to infer from active page URL.",
                },
                "login_probe_selector": {
                    "type": "string",
                    "description": "Optional CSS selector that is visible when logged in.",
                },
            },
            "required": [],
        },
    },
}

BROWSER_AUTH_STATUS_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_auth_status",
        "description": (
            "Read-only merged auth facts: site_auth workflow flags (login method chosen, credentials dispatched), "
            "storage availability, and optional login_probe_selector visibility. "
            "logged_in is null when login_probe_selector omitted — pass selector from inspect to probe. "
            "Use before resuming auth; does not mutate page or storage. See skills/browser/SESSION_PERSISTENCE.md."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expected_domain": {"type": "string"},
                "login_probe_selector": {"type": "string"},
            },
            "required": [],
        },
    },
}

BROWSER_END_SESSION_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_end_session",
        "description": (
            "Save storage_state (default save=true) and close the browser for this conversation. "
            "Call after publish_file or allowed workflow completion — before final natural language. "
            "Do NOT call while request_user_gate is pending (gate interrupt). "
            "On turn cancel/abort, session closes without save."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "save": {
                    "type": "boolean",
                    "description": "When true (default), persist cookies/storage before close.",
                },
                "expected_domain": {
                    "type": "string",
                    "description": "Optional host key for storage file; omit to use current page URL.",
                },
            },
            "required": [],
        },
    },
}

BROWSER_TRIGGER_OTP_SEND_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_trigger_otp_send",
        "description": (
            "Click the send-SMS button — separate model step, never inside gate prompt. "
            "Requires send_selector from inspect. "
            "Scenario A: otp_flow_id from phone-phase gate tool_result. "
            "Scenario B (post-login verify): omit otp_flow_id — tool starts a new flow (begin_flow). "
            "Must run before phone_otp phase=code gate unless countdown proves already sent "
            "(otp_already_sent → code gate only). Idempotent per flow+selector."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "send_selector": {"type": "string", "description": "CSS selector for send-SMS button"},
                "otp_flow_id": {
                    "type": "string",
                    "description": "From phone gate tool_result; optional for scenario B begin_flow.",
                },
            },
            "required": ["send_selector"],
        },
    },
}

ALL_TOOL_SCHEMAS = [
    WEB_SEARCH_TOOL,
    WEB_FETCH_TOOL,
    EXECUTE_PYTHON_TOOL,
    READ_FILE_TOOL,
    WRITE_FILE_TOOL,
    LIST_FILES_TOOL,
    PUBLISH_FILE_TOOL,
    BROWSER_EXEC_SCRIPT_TOOL,
    BROWSER_CAPTURE_STATE_TOOL,
    BROWSER_SIGNAL_BLOCKED_TOOL,
    REQUEST_USER_GATE_TOOL,
    BROWSER_CHALLENGE_READ_GEOMETRY_TOOL,
    BROWSER_CHALLENGE_SCREENSHOT_ELEMENT_TOOL,
    BROWSER_CHALLENGE_DISPATCH_POINTER_TRACE_TOOL,
    BROWSER_CHALLENGE_WAIT_PROBE_TOOL,
    CV_IMAGE_INFO_TOOL,
    CV_MATCH_TEMPLATE_TOOL,
    CV_FIND_GAP_X_TOOL,
    BROWSER_RESTORE_SESSION_TOOL,
    BROWSER_AUTH_STATUS_TOOL,
    BROWSER_END_SESSION_TOOL,
    BROWSER_TRIGGER_OTP_SEND_TOOL,
]

MEMORY_TOOL_DESCRIPTIONS = {
    "manage_user_memory": (
        "Create, update, or delete one user-scope memory (statement + context).\n"
        "See tool instructions for when to write; do not save background statements "
        "unless the user explicitly asks to remember them."
    ),
    "recall_user_memory": (
        "Semantic search of user-scope memories. Prefer injected Memory blocks first. "
        "At most once per turn; empty list means no matches."
    ),
    "manage_project_memory": (
        "Create, update, or delete one durable project fact for this canvas project "
        "(subject/predicate/object/context)."
    ),
    "recall_project_memory": (
        "Semantic search of this canvas project's durable memories. "
        "Prefer injected Memory blocks first. At most once per turn."
    ),
}

TOOL_DESCRIPTIONS = {
    item["function"]["name"]: item["function"]["description"]
    for item in ALL_TOOL_SCHEMAS
}
TOOL_DESCRIPTIONS.update(MEMORY_TOOL_DESCRIPTIONS)
