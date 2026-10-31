# Development stack

Nautobot 3.2.8 with nautobot-ssot 4.8.0 and this app mounted from the repo (read-only, on `PYTHONPATH`).

1. Create `dev/.env` (never committed) with `NAUTOBOT_SECRET_KEY`, `NAUTOBOT_DB_PASSWORD`,
   `NAUTOBOT_SUPERUSER_PASSWORD`, `NAUTOBOT_SUPERUSER_API_TOKEN` (40 hex characters), `PERCEPXION_URL`, `PERCEPXION_USERNAME`, `PERCEPXION_PASSWORD`
   and `PERCEPXION_ORG_ID`. Use the Percepxion sandbox, never production.
2. From the repo root: `docker compose -p nautobot-lantronix-dev -f dev/docker-compose.yml up -d --build`
3. Nautobot listens on port 8082. Sign in as `admin`.
4. Import the Lantronix device types (devicetype-library YAML) and create the default location.
5. Tests: `make test` (pure Python) and `make test-nautobot` (inside the stack).
