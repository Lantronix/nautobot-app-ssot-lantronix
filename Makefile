# Pure-Python tests run with pytest; Nautobot-backed tests run inside the dev stack.
VENV ?= .venv
PY = $(VENV)/bin/python
COMPOSE = docker compose -p nautobot-lantronix-dev -f dev/docker-compose.yml
NAUTOBOT_TESTS = nautobot_ssot_lantronix.tests.test_target_adapter nautobot_ssot_lantronix.tests.test_servicenow_console_paths

.PHONY: venv test test-nautobot lint scan
venv:
	uv venv --python 3.12 $(VENV) && uv pip install --python $(PY) -e ".[dev]"

test:
	$(PY) -m pytest -q

test-nautobot:
	$(COMPOSE) exec nautobot nautobot-server test --keepdb --noinput $(NAUTOBOT_TESTS)

lint:
	$(VENV)/bin/ruff check nautobot_ssot_lantronix && $(VENV)/bin/ruff format --check nautobot_ssot_lantronix

scan:
	gitleaks detect --no-banner --redact
