# Nexus Studio Backend

FastAPI backend for Nexus Studio. The frontend lives in the independent
`nexus-studio-frontend` repository; this repository owns the API, database DDL,
typed JSON Schemas, and production orchestration.

## Stack

- Python 3.12, FastAPI, Uvicorn
- PostgreSQL with pgvector
- Redis
- Tortoise ORM and LangGraph
- Docker-based Python sandbox and browser sessions

## Local Development

The existing Conda environment is named `dream-drama-env`:

```bash
conda activate dream-drama-env
pip install -r requirements.txt
# create ignored .env with secrets / local overrides only
# (defaults live in app/server/infra/config.py)
make dev
```

The API listens on `http://localhost:8000`. Documentation is available at
`/docs`; readiness is exposed at `/api/v1/health/ready`.

Useful checks:

```bash
make test
make lint
make type-check
make contracts-check
```

Database DDL is maintained in `db/schema.sql` (greenfield source of truth).
Local reset: `make db-reset`. Production DDL is a manual maintenance-window
operation; deployment does not run `make db-schema` or `make db-reset`.

## Typed Contracts

Pydantic models under `app/contracts/` are the protocol source of truth.

```bash
make contracts
make contracts-check
```

Generated JSON Schemas are committed under `contracts/schema/`. The frontend
vendors a synchronized copy and generates its TypeScript declarations without
importing Python code from this repository. See
`docs/typed-contracts-and-architecture.md` for the cross-repository workflow.

## Container Images

```bash
make docker-images-build   # backend + chat-sandbox + browser-session
```

Frontend image is built in the frontend repository:

```bash
make docker-build
```

Image names used by Compose / runtime defaults:

```text
nexus-studio-prod-backend
nexus-studio-prod-frontend
nexus-studio-chat-sandbox:latest
nexus-studio-browser-session:latest
```

## Production Compose

Same pattern as `union_lm`: one Compose file, one `deploy/.env.prod`, deploy with Make.
The Compose file lives here because the frontend Nginx proxies `/api/` to the
`backend` service name.

Prepare once on the host:

```bash
cp deploy/.env.prod.example deploy/.env.prod   # fill secrets
# ensure external network union-lm-network exists (union_lm already running)
# build frontend image in the frontend repo (tag nexus-studio-prod-frontend)
```

Deploy:

```bash
make docker-up
make docker-logs
```

`make docker-up` creates host workspace dirs, builds sandbox/browser images,
then `docker compose up -d --build` for backend + frontend. Backend secrets come
from `deploy/.env.prod` only. Application network is `nexus-studio-network`;
external `union-lm-network` must already exist. Host bind mounts:

```text
/var/lib/nexus-studio/chat-workspaces
/var/lib/nexus-studio/sandbox-packages
```

## Repository Layout

```text
app/                    FastAPI application and agents
contracts/schema/       Generated public JSON Schemas
db/                     Greenfield DDL (`schema.sql`)
deploy/                 Compose and runtime image definitions
scripts/                Contract exporter (`export_contracts.py`)
Dockerfile              Backend production image
```
