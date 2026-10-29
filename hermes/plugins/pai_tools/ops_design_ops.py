"""pai_ops_design_ops — OpenDesign API client (native Hermes tool).

Thin wrapper around the OpenDesign daemon REST API. All operations are
audited to the hermes-data volume log.

Environment variables:
  OD_API_TOKEN  — bearer token (required for all mutating operations)
  OD_HOST       — daemon host (default: http://open-design:7456)
  OD_WORKSPACE  — container workspace path (default: /opt/data/workspace)
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

LOG_DIR = os.environ.get("LOG_DIR", "/opt/hermes/data/logs")
LOG_FILE = os.path.join(LOG_DIR, "ops-design-ops.log")

ALLOWED_ACTIONS = (
    "list_projects",
    "get_project",
    "create_project",
    "import_folder",
    "get_status",
    "health",
)

SCHEMA = {
    "name": "pai_ops_design_ops",
    "description": (
        "Manage OpenDesign projects via the daemon REST API. "
        "Use for: create_project, import_folder, list_projects, get_project, get_status, health. "
        "Projects are rooted at OD_WORKSPACE (default /opt/data/workspace). "
        "All actions are audited."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(ALLOWED_ACTIONS),
                "description": "Operation to perform",
            },
            "project_id": {
                "type": "string",
                "description": "Project ID (required for get_project, get_status)",
            },
            "name": {
                "type": "string",
                "description": "Project name (required for create_project)",
            },
            "path": {
                "type": "string",
                "description": "Folder path relative to workspace (required for import_folder)",
            },
        },
        "required": ["action"],
    },
}


def _log_action(action, detail, status, duration_ms):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(
                f"[{ts}] action={action} detail={detail} "
                f"status={status} duration={duration_ms}ms\n"
            )
    except Exception:
        pass


def _api_request(method, path, body=None):
    token = os.environ.get("OD_API_TOKEN", "")
    host = os.environ.get("OD_HOST", "http://open-design:7456")
    url = f"{host}{path}"

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": raw}
    except Exception as e:
        return 0, {"error": str(e)}


def handle(args, **kwargs) -> str:
    if not isinstance(args, dict):
        return json.dumps({"error": "Invalid arguments"})

    start = time.monotonic()
    action = args.get("action", "")
    project_id = args.get("project_id", "") or ""
    name = args.get("name", "") or ""
    path = args.get("path", "") or ""

    def done(status, output):
        duration_ms = int((time.monotonic() - start) * 1000)
        _log_action(action, f"project_id={project_id} name={name} path={path}", status, duration_ms)
        return output

    if action not in ALLOWED_ACTIONS:
        return done(1, json.dumps({"error": f"unknown action: {action}. Allowed: {list(ALLOWED_ACTIONS)}"}))

    workspace = os.environ.get("OD_WORKSPACE", "/opt/data/workspace")

    if action == "health":
        code, body = _api_request("GET", "/api/health")
        return done(code, json.dumps(body))

    if action == "list_projects":
        code, body = _api_request("GET", "/api/projects")
        return done(code, json.dumps(body))

    if action == "get_project":
        if not project_id:
            return done(1, json.dumps({"error": "project_id is required"}))
        code, body = _api_request("GET", f"/api/projects/{project_id}")
        return done(code, json.dumps(body))

    if action == "create_project":
        if not name:
            return done(1, json.dumps({"error": "name is required"}))
        code, body = _api_request("POST", "/api/projects", {"name": name, "baseDir": workspace})
        return done(code, json.dumps(body))

    if action == "import_folder":
        if not path:
            return done(1, json.dumps({"error": "path is required"}))
        full_path = os.path.join(workspace, path)
        code, body = _api_request("POST", "/api/import/folder", {"path": full_path})
        return done(code, json.dumps(body))

    if action == "get_status":
        if not project_id:
            return done(1, json.dumps({"error": "project_id is required"}))
        code, body = _api_request("GET", f"/api/projects/{project_id}/status")
        return done(code, json.dumps(body))

    return done(1, json.dumps({"error": f"unhandled action: {action}"}))
