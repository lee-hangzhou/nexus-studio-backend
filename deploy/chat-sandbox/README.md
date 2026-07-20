# Chat Python 沙箱镜像

在 **dream-drama-env** 环境中构建：

```bash
conda activate dream-drama-env
docker build -t dream-drama-chat-sandbox:latest -f deploy/chat-sandbox/Dockerfile deploy/chat-sandbox
```

后端通过 `CHAT_SANDBOX_IMAGE`（默认 `dream-drama-chat-sandbox:latest`）运行 `execute_python` 工具。

## 镜像内容

- Python 3.12 + pandas / openpyxl / python-docx / pypdf / pdfplumber / reportlab / markitdown / matplotlib
- LibreOffice（headless 文档转换，冷启动约 10–30s）
- Node.js / npm；全局预装 `pptxgenjs`、`docx`（skill 脚本直接 `require`，勿在沙箱内 `npm install`）
- poppler-utils、 pandoc

默认 sandbox timeout：`CHAT_SANDBOX_TIMEOUT_SEC=120`。

## LibreOffice 基准（手动）

```bash
docker run --rm -v "$PWD":/workspace dream-drama-chat-sandbox:latest \
  /bin/sh -c "soffice --headless --convert-to pdf --outdir /tmp /workspace/test.docx && ls -la /tmp"
```

记录耗时到 issue 或运维笔记；若 OOM 可将 Docker memory 从 512m 调至 768m。
