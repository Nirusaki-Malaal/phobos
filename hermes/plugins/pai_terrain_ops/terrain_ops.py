"""pai_terrain_ops — code intelligence via the Terrain index service.

Terrain (https://github.com/sopaco/terrain) scans a project repo into
`.terrain/` — Markdown knowledge docs, an agent context layer, and a repomix
source pack. This is a thin HTTP client for the `terrain` container.

Design notes:
- Terrain is the SINGLE index owner. This tool never parses or indexes code
  itself; it triggers jobs on the service and reads the result. That is what
  replaced the in-process tree-sitter index (removed 2026-10-04), which indexed
  the whole 27k-file workspace in every agent's heap.
- Indexing is FREE. `scan` and `refresh` make no LLM call; `refresh` explicitly
  skips Litho, terrain's LLM doc generator. Only `ask` and `init` invoke an
  LLM, and the service refuses them unless TERRAIN_ALLOW_LLM=1. That LLM is
  NOT the llm-gateway: it is Terrain's own ACP agent on OpenCode's keyless
  Zen models. Terrain holds no gateway link and no provider key.
- `.terrain/` is written INSIDE the indexed repo. Pass `path` relative to
  /opt/data/workspace or absolute inside it; anything else is rejected by the
  service (403) and never reaches the terrain binary.
- `terrain env apply` is unreachable — the service enforces a closed action
  allowlist. It rewrites AGENTS.md and installs preset skills, which would
  overwrite files pai-stack owns.
"""

import json
import os
import time
import urllib.error
import urllib.request

SERVICE = os.environ.get("TERRAIN_URL", "http://terrain:7878")
TOKEN = os.environ.get("TERRAIN_TOKEN", "")
TIMEOUT = int(os.environ.get("TERRAIN_TIMEOUT", "600"))
WORKSPACE = os.environ.get("TERRAIN_WORKSPACE", "/opt/data/workspace")

# Mirrors the service-side allowlist exactly. Keep the two in step.
ALLOWED_ACTIONS = (
    "index",       # scan   — offline
    "refresh",     # refresh— offline (skips Litho)
    "search",      # search — offline
    "read",        # read   — offline
    "overview",    # project overview — offline
    "projects",    # project list — offline
    "unregister",  # project remove — offline
    "source",      # source — offline
    "init",        # init   — LLM (Litho)
    "ask",         # ask    — LLM
)
LLM_ACTIONS = ("init", "ask")

SCHEMA = {
    "name": "pai_terrain_ops",
    "description": (
        "Code intelligence over the Terrain index service. Terrain scans a project "
        "repo into .terrain/ (Markdown knowledge docs + agent context); this tool "
        "triggers those scans and reads the result. Check action='projects' first: "
        "if the repo is indexed, this is far more precise than grep; if it is "
        "not, fall back to grep + targeted reads. "
        "Indexing ('index', 'refresh') makes NO LLM call and costs nothing. "
        "Only 'ask' and 'init' use an LLM — Terrain's keyless Zen models, "
        "not the llm-gateway — and the service refuses them unless "
        "TERRAIN_ALLOW_LLM=1. "
        "Write scope: each scan writes .terrain/ inside the repo you pass. "
        "Paths are resolved inside /opt/data/workspace; other paths are rejected. "
        "Allowed actions: " + ", ".join(ALLOWED_ACTIONS) + "."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(ALLOWED_ACTIONS),
                "description": "Operation to perform",
            },
            "path": {
                "type": "string",
                "description": (
                    "Repo directory to index, relative to /opt/data/workspace or "
                    "absolute within it (e.g. 'github.com/ayedaemon/pai-stack'). "
                    "Required for 'index', 'refresh', 'init'. Optional for 'source'."
                ),
            },
            "slug": {
                "type": "string",
                "description": "Registry name for this project. Defaults to the directory name.",
            },
            "query": {
                "type": "string",
                "description": "Search string ('search') or question ('ask').",
            },
            "project": {
                "type": "string",
                "description": "Project slug ('search', 'overview', 'ask', 'source', 'unregister').",
            },
            "file": {
                "type": "string",
                "description": "Repo-relative file path ('source' only).",
            },
            "limit": {
                "type": "integer",
                "description": "Max search results ('search' only). Default: 20.",
                "default": 20,
                "minimum": 1,
                "maximum": 200,
            },
        },
        "required": ["action"],
    },
}


def check_available(**kwargs) -> bool:
    """True when the terrain service answers.

    Terrain is an opt-in profile, so the tool must degrade to absent
    rather than break the session.
    """
    try:
        req = urllib.request.Request(f"{SERVICE}/healthz", headers=_headers())
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _headers() -> dict:
    h = {"content-type": "application/json"}
    if TOKEN:
        h["x-terrain-token"] = TOKEN
    return h


def _post(action: str, params: dict) -> tuple:
    body = json.dumps({"action": action, "params": params}).encode()
    req = urllib.request.Request(
        f"{SERVICE}/call", data=body, headers=_headers(), method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return 0, resp.read().decode()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("error", detail)
        except Exception:
            pass
        return 1, json.dumps({"error": detail or f"HTTP {e.code}"})
    except urllib.error.URLError as e:
        return 1, json.dumps({
            "error": (
                f"cannot reach the terrain service at {SERVICE}: {e.reason}. "
                "It is an opt-in profile — start it with `make terrain-up`, "
                "or fall back to grep + targeted reads."
            )
        })
    except Exception as e:
        return 1, json.dumps({"error": str(e)})


def handle(args, **kwargs) -> str:
    """Hermes tool handler — dispatch an action, always returning a string."""
    if not isinstance(args, dict):
        return json.dumps({"error": "Invalid arguments"})
    start = time.monotonic()
    action = args.get("action", "") or ""

    if action not in ALLOWED_ACTIONS:
        return json.dumps({
            "error": f"unknown action: {action}. Allowed: {list(ALLOWED_ACTIONS)}"
        })

    if action in ("index", "refresh", "init") and not args.get("path"):
        return json.dumps({
            "error": f"path is required for '{action}'. Pass the repo directory, "
                     f"e.g. path='github.com/ayedaemon/pai-stack' (resolved inside {WORKSPACE})."
        })
    if action in ("search", "ask") and not args.get("query"):
        return json.dumps({"error": f"query is required for '{action}'"})
    if action == "overview" and not args.get("project"):
        return json.dumps({"error": "project is required for 'overview'"})

    params = {k: v for k, v in (
        ("path", args.get("path")),
        ("slug", args.get("slug")),
        ("query", args.get("query")),
        ("project", args.get("project")),
        ("file", args.get("file")),
        ("limit", args.get("limit")),
    ) if v is not None}

    code, out = _post(action, params)

    try:
        parsed = json.loads(out)
    except Exception:
        parsed = {"raw": out}
    if isinstance(parsed, dict):
        parsed["_duration_ms"] = int((time.monotonic() - start) * 1000)
        parsed["_action"] = action
        if code == 0 and action in ("index", "refresh", "init"):
            parsed["_hint"] = (
                f"'.terrain/' now exists inside {params.get('path')}. It is "
                "gitignored; re-run 'refresh' after meaningful code changes."
            )
    return json.dumps(parsed, indent=2) if isinstance(parsed, dict) else str(parsed)