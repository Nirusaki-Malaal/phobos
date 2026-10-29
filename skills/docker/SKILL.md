---
name: docker
description: Docker over the mounted socket with native docker + compose binaries. Load on any Dockerfile/compose/container question or when stack-discovery finds them. Teaches socket scope, help-first flag discovery, and low-noise --format output. Delegates optimization and hardening depth to docker-development.
---

# Docker (native CLI over the mounted socket)

> No docker tool exists. Both agents drive `/var/run/docker.sock` directly
> with the native `docker` + `docker compose` binaries baked into their images.
> Shared Hermes glue is canonical in the `agents` skill.

Socket premise (both containers): `/var/run/docker.sock:/var/run/docker.sock:ro`
+ `docker-ce-cli` + `docker-compose-plugin` on `PATH`. Verify once per session:

```bash
docker version --format '{{.Server.Version}}' && docker compose version --short
```

If that fails → report degraded (socket or binary missing), do NOT guess further.

## Learn the CLI from itself — never guess flags

Binaries drift across versions. Before first use of any unfamiliar
object/subcommand, read its help. General → specific:

```bash
docker --help
docker <object> --help            # e.g. docker container --help
docker <object> <cmd> --help      # e.g. docker container ls --help
docker compose --help
docker compose <cmd> --help       # e.g. docker compose up --help
```

Rules: never invent a flag; if help output contradicts this skill, trust help.

## Low-noise output — hide what agents don't need

Default `docker ps` / `compose ps` tables are wide and truncate. Always
constrain columns with `--format`, and prefer `json` when piping to `jq`.

```bash
# pai-stack cluster only, three columns, no truncation noise
docker ps -a --filter label=com.docker.compose.project=phobos \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# machine-readable single container
docker ps --filter name=^hermes$ --format '{{json .}}' | jq '{Name, State, Status}'

# state without full inspect dump
docker inspect hermes --format '{{json .State}}' | jq '{Status, Running, ExitCode}'

# logs: always bound tail, never full dump
docker logs --tail=50 llm-gateway
docker logs --tail=50 --since 10m hermes

# compose inside a project: validate, then scoped status
cd <EXECUTION_DIR> && docker compose -p <project> config --quiet   # syntax + var resolution
cd <EXECUTION_DIR> && docker compose -p <project> ps --format json | jq '[.[] | {Name, Service, State}]'
cd <EXECUTION_DIR> && docker compose -p <project> logs --tail=50 <service>
```

Discovery pattern for any new query: `<cmd> --help | grep -i format`,
then pick `table` for eyes, `json` for pipes.

## Scope — which cluster are you touching?

```
Does EXECUTION_DIR contain a compose file?
(docker-compose*.yaml OR compose.yaml)
│
├── YES → `cd <EXECUTION_DIR> && docker compose -p <project> ...` ONLY
│         NEVER bare `docker run` / `docker create` here.
│
└── NO  → pai-stack services (hermes, llm-gateway, terrain, dsh, open-design)?
          `docker ps/inspect/logs/restart/start/stop/exec <name>` directly.
          `docker run` only after user confirmation + documented reason.
```

**NEVER rules (no exceptions):**
- NEVER run `docker compose` without `-p <project>` — hermes exports
  `COMPOSE_PROJECT_NAME=phobos`, which hijacks bare compose into the
  pai-stack cluster.
- NEVER run compose from outside `EXECUTION_DIR`. Compose v2 only
  (`docker compose`, no hyphen).
- NEVER add a foreign container to the pai-stack cluster, and NEVER add
  `--privileged`, host network, or `/var/run/docker.sock` mounts to project stacks.

Project name: `name:` field in the project's compose file, else basename of
`EXECUTION_DIR`. Confirm with `docker compose ls` before mutating.

## Lifecycle (read-only freely; mutating only after propose → confirm)

```bash
docker ps -a --filter label=com.docker.compose.project=phobos --format 'table {{.Names}}\t{{.Status}}'
docker logs --tail=50 <service>                 # read-only
docker inspect <service> --format '{{json .State}}' | jq .
docker restart|start|stop <service>             # mutating: propose first
docker exec <service> sh -c '<cmd>'             # mutating-ish: prefer running locally; exec only for container-local state
cd <EXECUTION_DIR> && docker compose -p <project> up -d --build   # mutating: propose first, config --quiet first
cd <EXECUTION_DIR> && docker compose -p <project> down            # mutating: propose first, confirm volumes kept
```

Env & secrets: compose reads `<EXECUTION_DIR>/.env` — verify vars before `up`;
`${VAR:-default}` substitution; never commit `.env`; flag raw secrets.

## Discover (validate cheaply: `config --quiet` before `up --build`)

1. **Artifacts**: `Dockerfile*`, `docker-compose*.yaml`, `compose.yaml`, `.dockerignore`, `Makefile` targets.
2. **Dockerfile**: `FROM` + stages, `COPY` hygiene, `RUN` caching, `USER`/`EXPOSE`/`ENTRYPOINT`/`HEALTHCHECK`.
3. **Compose**: service→port map, named vs bind volumes (`EACCES`/`UID` risk), networks, `extra_hosts: host.docker.internal`, env provenance.
4. **Conventions**: multi-stage, minimal base, non-root `USER`, pinned tags (never `:latest`), `.dockerignore` present.

## Tooling (absolute paths — skill dirs mount read-only)

- Analyze: `python3 /opt/pai/skills/docker-development/scripts/dockerfile_analyzer.py <Dockerfile>`
- Validate: `python3 /opt/pai/skills/docker-development/scripts/compose_validator.py <compose.yaml>`
- **Severity caveat**: the validator flags standard `${VAR}` substitutions as CRITICAL — treat its severities as **advisory only**; verify each finding against the file before acting.

## Delegate (load via `skill_view`)

| Need | Load |
|---|---|
| Optimization checklists, multi-stage patterns, security audit tables | `docker-development` |
