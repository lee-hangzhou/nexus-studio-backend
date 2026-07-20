.PHONY: help install install-dev dev test lint format clean \
	        docker-backend-build docker-sandbox-build docker-browser-build docker-images-build \
	        docker-config-check docker-config docker-up docker-down docker-logs docker-restart \
	        db-schema db-reset contracts contracts-check type-check

BACKEND_IMAGE ?= dream-drama-prod-backend:latest
CHAT_SANDBOX_IMAGE ?= dream-drama-chat-sandbox:latest
CHAT_BROWSER_IMAGE ?= dream-drama-browser-session:latest
CHAT_WORKSPACE_ROOT ?= /var/lib/dream-drama/chat-workspaces
SANDBOX_PACKAGES_ROOT ?= /var/lib/dream-drama/sandbox-packages
COMPOSE_ENV_FILE ?= deploy/.compose.env
COMPOSE_FILE ?= deploy/docker-compose.yml
DOCKER_COMPOSE = docker compose --env-file $(COMPOSE_ENV_FILE) -f $(COMPOSE_FILE)

# ── 本地开发（conda activate dream-drama-env） ─────────────────────────────

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
	@echo "  make type-check     检查新领域、契约、Agent 兼容层和生成规则"
	@echo ""
	@echo "生产部署:"
	@echo "  make docker-backend-build   构建 backend 镜像"
	@echo "  make docker-sandbox-build   构建 execute_python 沙箱镜像"
	@echo "  make docker-browser-build   构建 browser run-server 镜像"
	@echo "  make docker-images-build    构建 backend + 两个运行时镜像"
	@echo "  make docker-config-check    使用已提交模板校验 Compose"
	@echo "  make docker-config          校验生产 Compose 配置"
	@echo "  make docker-up              使用已有主服务镜像启动生产容器"
	@echo "  make docker-down            停止生产容器"
	@echo "  make docker-restart         使用已有镜像重启生产容器"
	@echo "  make docker-logs            查看后端日志"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements.txt
	pip install pytest pytest-cov pytest-asyncio black ruff mypy pre-commit
	pre-commit install

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	python -m pytest -q \
		app/chat/browser/tests/test_container_runtime.py \
		deploy/browser-session/test_driver_storage.py

lint:
	ruff check --select E9,F63,F7 app/ scripts/export_contracts.py

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
	psql "$$DATABASE_URL" < db/schema.sql

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
		app/domain \
		app/contracts \
		app/compat/agent_tools \
		app/services/generation_capabilities.py \
		app/services/generation_params.py \
		app/services/generation_submit.py

# ── 生产部署 ──────────────────────────────────────────────────────────────

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
	docker compose --env-file deploy/.compose.env.example -f $(COMPOSE_FILE) config --quiet

docker-config:
	$(DOCKER_COMPOSE) config

docker-up: docker-prep
	$(DOCKER_COMPOSE) up -d --no-build

docker-down:
	$(DOCKER_COMPOSE) down

docker-restart: docker-down docker-prep
	$(DOCKER_COMPOSE) up -d --no-build

docker-logs:
	$(DOCKER_COMPOSE) logs -f backend
