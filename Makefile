# Pure-Python tests run with pytest; Nautobot-backed tests run inside the dev stack.
VENV ?= .venv
PY = $(VENV)/bin/python
COMPOSE = docker compose -p nautobot-lantronix-dev -f dev/docker-compose.yml
NAUTOBOT_TESTS = nautobot_ssot_lantronix.tests.test_target_adapter nautobot_ssot_lantronix.tests.test_servicenow_console_paths nautobot_ssot_lantronix.tests.test_app_registry nautobot_ssot_lantronix.tests.test_job_failure nautobot_ssot_lantronix.tests.test_coverage_job

.PHONY: venv test test-nautobot lint check-shared scan
venv:
	uv venv --python 3.12 $(VENV) && uv pip install --python $(PY) -e ".[dev]"

test:
	$(PY) -m pytest -q

test-nautobot:
	# No --keepdb: test_job_failure is a TransactionTestCase, which flushes Nautobot's built-in
	# statuses out of a kept database and breaks the next run.
	$(COMPOSE) exec nautobot nautobot-server test --noinput $(NAUTOBOT_TESTS)

lint:
	$(VENV)/bin/ruff check nautobot_ssot_lantronix && $(VENV)/bin/ruff format --check nautobot_ssot_lantronix

# The code shared with netbox-lantronix must match it (CI: shared-code.yml).
OTHER ?= ../netbox-lantronix

check-shared:
	python3 scripts/check_shared.py $(OTHER)

scan:
	gitleaks detect --no-banner --redact
