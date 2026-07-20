#!/bin/sh
set -e

RUN_PORT="${CHAT_BROWSER_RUN_SERVER_PORT:-3333}"
DRIVER_PORT="${CHAT_BROWSER_DRIVER_PORT:-3334}"

playwright run-server \
  --port "${RUN_PORT}" \
  --host 0.0.0.0 &

export CHAT_BROWSER_RUN_SERVER_WS="ws://127.0.0.1:${RUN_PORT}/"
export CHAT_BROWSER_DRIVER_PORT="${DRIVER_PORT}"

exec /opt/pw-venv/bin/python /opt/browser-session/driver.py
