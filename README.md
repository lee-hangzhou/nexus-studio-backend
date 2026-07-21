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
cp .env.example .env
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

Database DDL is maintained in `db/schema.sql` and `db/migrations/`. Production
migrations are manual maintenance-window operations; deployment does not run
`make db-schema` or `make db-reset` automatically.

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

Build the backend and the two runtime images used by Chat tools:

```bash
make docker-backend-build
make docker-sandbox-build
make docker-browser-build
```

`make docker-images-build` builds all three. The frontend image is built by the
frontend repository. Defaults used by the production Compose file are:

```text
dream-drama-prod-backend:latest
dream-drama-prod-frontend:latest
dream-drama-chat-sandbox:latest
dream-drama-browser-session:latest
```

For releases, set `BACKEND_IMAGE` and `FRONTEND_IMAGE` in
`deploy/.compose.env` to immutable registry tags such as Git commit SHAs. Pass
the same backend tag to `make BACKEND_IMAGE=... docker-backend-build` when
building locally.

## Production Compose

The combined Compose file stays here because the frontend Nginx container
proxies `/api/` to the `backend` service name. Both main services are image-only;
there are no cross-repository build contexts.

Prepare the host once:

```bash
cp deploy/.compose.env.example deploy/.compose.env
cp deploy/.env.prod.example deploy/.env.prod
make docker-prep
```

`deploy/.compose.env` contains only image tags, `FRONTEND_BIND`, and the Docker
network name. `deploy/.env.prod` contains backend runtime settings and secrets.
Both real files are ignored by Git and Docker build contexts and must never be
committed. The checked-in Compose default and template preserve the current
frontend binding at `127.0.0.1:82`.

The checked-in `.compose.env.example` intentionally uses
`BACKEND_ENV_FILE=.env.prod.example` so CI can validate Compose without secrets.
After copying it for production, change that line to:

```dotenv
BACKEND_ENV_FILE=.env.prod
```

Validate and start:

```bash
make docker-config-check
make docker-config
make docker-up
make docker-logs
```

Every Compose command uses `--env-file deploy/.compose.env` for orchestration
variables. The backend service separately loads `deploy/.env.prod` through its
`env_file`, so Compose interpolation does not depend on runtime secrets. The
default application network is `dream-drama-network`; the external
`union-lm-network` must already exist. `make docker-up` never rebuilds the two
main service images. Ensure the selected backend and frontend image tags exist
locally or have been pulled before starting. The backend also requires Docker
socket access and these host paths:

```text
/var/lib/dream-drama/chat-workspaces
/var/lib/dream-drama/sandbox-packages
```

Those paths are mounted at the identical absolute paths inside the backend
because child containers are created through the host Docker daemon.

## Repository Layout

```text
app/                    FastAPI application and agents
contracts/schema/       Generated public JSON Schemas
db/                     Final schema and ordered migrations
deploy/                 Compose and runtime image definitions
scripts/                Migrations, repair tools, contract exporter
Dockerfile              Backend production image
```
