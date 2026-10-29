.DEFAULT_GOAL := help
include Makefile.agents
UV ?= $(if $(wildcard .venv/bin/uv),.venv/bin/uv,uv)

.PHONY: help install check test init serve worker telegram up down logs config
help:
	@printf '%s\n' 'Phobos — personal AI helper' 'install  Install locked dependencies' 'check    Lint and test' 'init     Generate local configuration' 'serve    Start local API' 'worker   Run local workflows' 'telegram Run local bot' 'up/down  Start/stop Docker services' 'config   Validate Compose'
install:
	$(UV) sync --frozen
check:
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run pytest -q
	$(UV) run python -m pytest hermes/plugins/compressor/test_compressor.py -q
	node --test dsh/plugins/dsh-pai-telegram/test/
	$(UV) run python scripts/check-dsh-versions.py
test:
	$(UV) run pytest -q
init:
	$(UV) run phobos init
serve:
	$(UV) run phobos serve
worker:
	$(UV) run phobos worker
telegram:
	$(UV) run phobos telegram
config:
	docker compose -p phobos config --quiet
up: config
	docker compose -p phobos up -d --build
down:
	docker compose -p phobos down
logs:
	docker compose -p phobos logs --tail=100 -f
