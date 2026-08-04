.PHONY: help install install-dev dev test lint format clean \
	        docker-backend-build docker-sandbox-build docker-browser-build docker-images-build \
	        docker-config-check docker-config docker-up docker-down docker-logs docker-restart \
	        db-schema db-reset contracts contracts-check type-check

ROOT_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
BACKEND_IMAGE ?= nexus-studio-prod-backend
CHAT_SANDBOX_IMAGE ?= nexus-studio-chat-sandbox:latest
CHAT_BROWSER_IMAGE ?= nexus-studio-browser-session:latest
CHAT_WORKSPACE_ROOT ?= /var/lib/nexus-studio/chat-workspaces
SANDBOX_PACKAGES_ROOT ?= /var/lib/nexus-studio/sandbox-packages
COMPOSE_FILE := $(ROOT_DIR)deploy/docker-compose.yml
DOCKER_COMPOSE = docker compose -f $(COMPOSE_FILE)


help:
	@echo "本地开发:"
	@echo "  make install        安装 Python 依赖"
	@echo "  make install-dev    安装 Python + 开发工具依赖"
	@echo "  make dev            启动后端开发服务器（热重载）"
	@echo "  make test           运行 backend 容器运行时测试"
	@echo "  make lint           运行 ruff 检查"
	@echo "  make format         自动格式化代码"
	@echo "  make clean          清理缓存文件"
	@echo "  make db-schema      执行 db/schema.sql 建表"
	@echo "  make db-reset       清空开发库 public schema 后按最终 schema 重建"
	@echo "  make contracts      重新生成后端 JSON Schema"
	@echo "  make contracts-check 检查生成 contract 是否漂移"
	@echo "  make type-check     检查各域 domain、契约和生成规则"
	@echo ""
	@echo "生产部署:"
	@echo "  make docker-backend-build   构建 backend 镜像"
	@echo "  make docker-sandbox-build   构建 execute_python 沙箱镜像"
	@echo "  make docker-browser-build   构建 browser run-server 镜像"
	@echo "  make docker-images-build    构建 backend + 两个运行时镜像"
	@echo "  make docker-config-check    校验 Compose 配置"
	@echo "  make docker-config          打印 Compose 配置"
	@echo "  make docker-up              构建并启动生产容器（需已有 frontend 镜像）"
	@echo "  make docker-down            停止生产容器"
	@echo "  make docker-restart         重启生产容器"
	@echo "  make docker-logs            查看后端日志"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements.txt
	pip install pytest pytest-cov pytest-asyncio black ruff mypy pre-commit import-linter
	pre-commit install

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	python -m pytest -q \
		app/agent/chat/browser/tests/test_container_runtime.py \
		deploy/browser-session/test_driver_storage.py

lint:
	ruff check --select E9,F63,F7 app/ scripts/export_contracts.py
	lint-imports

format:
	ruff check --fix --select E9,F63,F7 app/ scripts/export_contracts.py
	ruff format app/ scripts/export_contracts.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

db-schema:
	psql "$$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/schema.sql

db-reset:
	@test "$$ENV" != "production"
	psql "$$DATABASE_URL" -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	psql "$$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/schema.sql

contracts:
	python scripts/export_contracts.py

contracts-check:
	python scripts/export_contracts.py --check

type-check:
	python -m mypy --follow-imports=skip \
		app/server/generation/domain \
		app/server/canvas/domain \
		app/server/chat/domain \
		app/contracts \
		app/server/generation/services/service.py

# ── 生产部署（与 union_lm 相同：make docker-up）────────────────────────────

docker-prep:
	mkdir -p $(CHAT_WORKSPACE_ROOT) $(SANDBOX_PACKAGES_ROOT)

docker-backend-build:
	docker build -t $(BACKEND_IMAGE) .

docker-sandbox-build:
	docker build -t $(CHAT_SANDBOX_IMAGE) -f deploy/chat-sandbox/Dockerfile deploy/chat-sandbox

docker-browser-build:
	docker build -t $(CHAT_BROWSER_IMAGE) -f deploy/browser-session/Dockerfile deploy/browser-session

docker-images-build: docker-backend-build docker-sandbox-build docker-browser-build

docker-config-check:
	@test -f deploy/.env.prod
	$(DOCKER_COMPOSE) config --quiet

docker-config:
	$(DOCKER_COMPOSE) config

docker-up: docker-prep docker-sandbox-build docker-browser-build
	$(DOCKER_COMPOSE) up -d --build

docker-down:
	$(DOCKER_COMPOSE) down

docker-restart: docker-down docker-up

docker-logs:
	$(DOCKER_COMPOSE) logs -f backend celery-worker celery-beat
