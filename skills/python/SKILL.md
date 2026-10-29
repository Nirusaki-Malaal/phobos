---
name: python
description: Python discovery router — detect env, framework, entrypoints, and tests, and enforce uv-run execution. Load on Python mentions (*.py, pyproject.toml, uv) or stack-discovery hits. Delegates backend architecture, scaffolding, and load-testing depth to senior-backend.
---

# Python (router + conventions)

> You own env **detection and execution discipline**. Backend-architecture depth lives
> upstream. Shared Hermes glue (vault, citations, Telegram) is canonical in the `agents` skill.

## Discover (cite `pyproject.toml:lines`, lockfiles, entry files)
1. **Env**: manager (`uv.lock`→uv, `[tool.poetry]`→Poetry, `Pipfile`→pipenv, else pip), version (`requires-python`, `.python-version`, `Dockerfile: FROM python:X`).
2. **Framework**: `manage.py`→Django, `fastapi`→FastAPI, `flask`→Flask; `sqlalchemy`/`alembic`→ also load `sql` shim; `Dockerfile`→ also load `docker` shim.
3. **Entrypoints**: `src/`/`app/`/`<package>/`/`tests/` layout, `[project.scripts]`, `Makefile` targets, `main.py`/`app.py` 20-line trace for the run command.
4. **Health**: pinned vs unpinned deps, `ruff`/`black`/`mypy` config, `.env.example` secret refs (never raw), test runner (`pytest` config, `tests/`).

## Execution discipline (non-negotiable)
- ALWAYS `uv run <command>` (`uv run pytest`, `uv run python main.py`). Never global `pip install` (suggest `uv sync`); never build eggs/wheels unless asked.

## Conventions for code you write
- 3.10+ typing (`list[str]`, `X | Y`); `pydantic` at API boundaries, `@dataclass` internally.
- `asyncio` + `httpx`/`aiohttp` for I/O; never block the loop (`time.sleep`, heavy sync in `async def`).
- Structured logging, no silent failures; env vars for secrets; ORM/parameterized queries.
- FastAPI: thin routes (`Depends()`), logic in services. SQLAlchemy: 2.0-style declarative, context-managed sessions.
- Bans: mutable default args, bare `except:`/`except Exception: pass`, arrow code (return early).

## Tooling (absolute paths)
- Scaffold/decide/load-test: `python3 /opt/pai/skills/senior-backend/scripts/<tool>.py` (scaffolder, migration tool, load tester, decision engine + `profiles/`).

## Delegate (load via `skill_view`)
| Need | Load |
|---|---|
| API design, DB optimization workflow, security hardening, load-test analysis, stack profiles | `senior-backend` |
