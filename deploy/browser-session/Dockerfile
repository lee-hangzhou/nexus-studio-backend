FROM mcr.microsoft.com/playwright:v1.51.0-noble

ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /workspace

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    BROWSER_PYTHON=/opt/pw-venv/bin/python \
    CHAT_BROWSER_RUN_SERVER_PORT=3333 \
  PATH="/opt/pw-server/node_modules/.bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/pw-venv \
    && /opt/pw-venv/bin/pip install --no-cache-dir \
       --index-url "${PIP_INDEX}" \
       --trusted-host "${PIP_TRUSTED_HOST}" \
       playwright==1.51.0 aiohttp==3.13.5 \
    && mkdir -p /opt/pw-server \
    && cd /opt/pw-server \
    && npm init -y \
    && npm install --no-save --registry="${NPM_REGISTRY}" playwright@1.51.0 \
    && npm cache clean --force

COPY entrypoint.sh /usr/local/bin/browser-session-entrypoint.sh
COPY driver.py /opt/browser-session/driver.py
RUN chmod +x /usr/local/bin/browser-session-entrypoint.sh

# Long-lived Playwright run-server; scripts attach via docker exec + BROWSER_PYTHON.
ENTRYPOINT ["/usr/local/bin/browser-session-entrypoint.sh"]
