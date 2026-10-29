"""Run ported native tools with a fresh per-project environment for every call."""

import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

from phobos.config import Settings
from phobos.workspace import confined, project_path

MODULES = {
    "pai_notebook_ops": "hermes/plugins/pai_tools/notebook_ops.py",
    "pai_adr_ops": "hermes/plugins/pai_tools/adr_ops.py",
    "pai_ops_design_ops": "hermes/plugins/pai_tools/ops_design_ops.py",
    "pai_terrain_ops": "hermes/plugins/pai_terrain_ops/terrain_ops.py",
}


def skills(root: Path) -> list[dict]:
    return [{"name": path.parent.name} for path in sorted((root / "skills").glob("*/SKILL.md"))]


def skill_view(root: Path, name: str, file: str = "SKILL.md") -> dict:
    if not re.fullmatch(r"[a-z0-9_-]+", name):
        raise ValueError("invalid skill name")
    directory = root / "skills" / name
    path = confined(directory, file)
    with path.open(encoding="utf-8") as stream:
        content = stream.read(32001)
    return {
        "name": name,
        "file": file,
        "content": content[:32000],
        "truncated": len(content) > 32000,
    }


def invoke(settings: Settings, project: str, name: str, arguments: dict) -> dict:
    if name not in MODULES:
        raise ValueError("unknown native tool")
    root = project_path(settings.workspace_dir, project)
    root.mkdir(parents=True, exist_ok=True)
    args = dict(arguments)
    args["execution_dir"] = str(root)
    for directory in (".project", ".project/research", ".project/planning"):
        if not (root / directory).resolve().is_relative_to(root):
            raise ValueError("project state resolves outside this project")
    for key in ("notebook_id", "source_id"):
        if args.get(key) and not re.fullmatch(r"[\w: -]+", args[key]):
            raise ValueError(f"invalid {key}")
    if name == "pai_notebook_ops" and args.get("file_path"):
        args["file_path"] = str(confined(root, args["file_path"]))
    if name == "pai_adr_ops":
        if args.get("adr_number") and not str(args["adr_number"]).isdigit():
            raise ValueError("ADR number must be numeric")
        for symbol in args.get("symbols", []):
            path = symbol.removeprefix("@symbol:").split(":", 1)[0]
            confined(root, path)
    if name == "pai_ops_design_ops" and args.get("path"):
        args["path"] = str(confined(root, args["path"]))
    if name == "pai_terrain_ops":
        for key in ("path", "file"):
            if args.get(key):
                resolved = confined(root, args[key])
                # Terrain sees the workspace at the original shared container path.
                args[key] = str(Path("/opt/data/workspace") / project / resolved.relative_to(root))
        if args.get("project") and args["project"] != project:
            raise ValueError("Terrain project must match this ticket's project")
        if args.get("action") in {"projects", "unregister"}:
            raise ValueError("registry-wide actions are available in the native agent profiles")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(settings.data_dir.resolve()),
        "EXECUTION_DIR": str(root),
        "WORKSPACE_DIR": str(root),
        "WORKSPACE_PATH": str(root),
        "HERMES_DATA_DIR": str(root),
        "LOG_DIR": str(settings.data_dir.resolve() / "logs"),
        "LLM_GATEWAY_URL": settings.gateway_url,
        "LLM_GATEWAY_BASE_URL": settings.gateway_url,
        "LITELLM_MASTER_KEY": settings.gateway_key,
        "LLM_MODEL": settings.model,
        "PHOBOS_EXTENSION_ROOT": str(settings.extension_root.resolve()),
        "TERRAIN_URL": os.environ.get("TERRAIN_URL", "http://terrain:7878"),
        "TERRAIN_TOKEN": os.environ.get("TERRAIN_TOKEN", ""),
        "OD_HOST": os.environ.get("OD_HOST", "http://open-design:7456"),
        "OD_WORKSPACE": "/opt/data/workspace/" + project,
        "OD_API_TOKEN": os.environ.get("OD_API_TOKEN", ""),
    }
    module = settings.extension_root / MODULES[name]
    if not module.is_file():
        raise ValueError("native extensions not installed; set PHOBOS_EXTENSION_ROOT")
    result = subprocess.run(
        [sys.executable, "-m", "phobos.bridge", name],
        input=json.dumps(args),
        text=True,
        capture_output=True,
        timeout=110,
        env=environment,
    )
    if result.returncode:
        raise ValueError("native tool failed; check extension dependencies and configuration")
    output = result.stdout
    if len(output) > 48000:
        return {"output": output[:48000], "truncated": True}
    try:
        return json.loads(output)
    except ValueError:
        return {"output": output}


def main():
    name = sys.argv[1]
    root = Path(os.environ["PHOBOS_EXTENSION_ROOT"])
    namespace = runpy.run_path(str(root / MODULES[name]))
    print(namespace["handle"](json.load(sys.stdin)))


if __name__ == "__main__":
    main()
