#!/usr/bin/env python3
"""check-dsh-versions.py — fail fast on DSH version-pin drift.

Version pins live in several places by design (.env.example template,
compose defaults, Dockerfile ARG defaults, plugin package.json). This
stdlib-only script asserts they agree, so a bump in one place without the
others fails here instead of surfacing as a half-upgraded container:

1. Registry pins agree across .env.example, docker-compose.yaml
   (:-defaults) and dsh/Dockerfile (ARG defaults):
   DSH_VERSION, DSH_REMOTE_PLUGIN_VERSION, DSH_MNEMON_PLUGIN_VERSION,
   DSH_AUTOMATION_PLUGIN_VERSION, PLAYWRIGHT_VERSION.
2. All dsh-pai-*/package.json share one version (the entrypoint upgrade
   gate keys the whole tree off dsh-pai-notebook — bump them in lockstep).
3. dsh-pai-web's `playwright` dependency matches PLAYWRIGHT_VERSION.
4. Every DSH_* gate read by dsh/docker-entrypoint.sh is wired through
   docker-compose.yaml and documented in .env.example (catches dead
   gates like the historical DSH_WITH_PAI_PLUGINS, which was read but
   never set anywhere).

Usage: make dsh-versions-check (exit 0 = in sync, 1 = drift found).
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO / ".env.example"
COMPOSE = REPO / "compose.agents.yaml"
DOCKERFILE = REPO / "dsh" / "Dockerfile"
ENTRYPOINT = REPO / "dsh" / "docker-entrypoint.sh"
PLUGINS = REPO / "dsh" / "plugins"

# .env name -> Dockerfile ARG name (compose maps them 1:1 via build args).
PIN_VARS = {
    "DSH_VERSION": "DSH_VERSION",
    "DSH_REMOTE_PLUGIN_VERSION": "REMOTE_PLUGIN_VERSION",
    "DSH_MNEMON_PLUGIN_VERSION": "MNEMON_PLUGIN_VERSION",
    "DSH_AUTOMATION_PLUGIN_VERSION": "AUTOMATION_PLUGIN_VERSION",
    "PLAYWRIGHT_VERSION": "PLAYWRIGHT_VERSION",
}

PAI_PLUGINS = [
    "dsh-pai-notebook",
    "dsh-pai-adr",
    "dsh-pai-design",
    "dsh-pai-terrain",
    "dsh-pai-git",
    "dsh-pai-web",
]

errors: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


def env_val(text: str, name: str) -> str | None:
    m = re.search(rf'^{name}="([^"]*)"', text, re.M)
    return m.group(1) if m else None


def main() -> int:
    env_text = ENV_EXAMPLE.read_text()
    compose_text = COMPOSE.read_text()
    docker_text = DOCKERFILE.read_text()
    entry_text = ENTRYPOINT.read_text()

    for env_name, arg_name in PIN_VARS.items():
        v_env = env_val(env_text, env_name)
        m_compose = re.search(rf"{arg_name}: \${{{env_name}:-(.*?)}}", compose_text)
        v_compose = m_compose.group(1) if m_compose else None
        m_docker = re.search(rf"^ARG {arg_name}=(.+)$", docker_text, re.M)
        v_docker = m_docker.group(1).strip() if m_docker else None
        check(v_env is not None, f"{env_name} present in .env.example")
        check(
            v_env is not None and v_env == v_compose == v_docker,
            f"{env_name} agrees (.env.example={v_env} compose={v_compose} Dockerfile={v_docker})",
        )

    pai_versions = {}
    for plugin in PAI_PLUGINS:
        pkg = json.loads((PLUGINS / plugin / "package.json").read_text())
        pai_versions[plugin] = pkg["version"]
    check(
        len(set(pai_versions.values())) == 1,
        f"pai plugins in lockstep {pai_versions}",
    )
    web_pkg = json.loads((PLUGINS / "dsh-pai-web" / "package.json").read_text())
    pw_pin = env_val(env_text, "PLAYWRIGHT_VERSION")
    check(
        web_pkg["dependencies"].get("playwright") == pw_pin,
        f"dsh-pai-web playwright dep matches PLAYWRIGHT_VERSION ({web_pkg['dependencies'].get('playwright')} vs {pw_pin})",
    )

    gates = sorted(set(re.findall(r"DSH_(?:WITH_\w+|DISABLE_WEB_SEARCH|SEED_LOCALE)", entry_text)))
    for gate in gates:
        in_compose = f"{gate}:" in compose_text or "${" + gate in compose_text
        check(in_compose, f"gate {gate} wired in docker-compose.yaml")
        check(f"{gate}=" in env_text, f"gate {gate} documented in .env.example")

    # Single joint tree: the old per-plugin /opt/dsh-*-seed dirs must not
    # come back (they duplicated ~300MB of image layers as strict subsets
    # of the final tree). /opt/dsh-seed is the only seed dir allowed.
    for path, label in ((DOCKERFILE, "dsh/Dockerfile"), (ENTRYPOINT, "dsh/docker-entrypoint.sh")):
        text = path.read_text()
        olds = sorted(set(re.findall(r"/opt/dsh-(?:remote|mnemon|auto|telegram|pai)-seed", text)))
        check(not olds, f"no per-plugin seed dirs in {label}" + (f" (found {olds})" if olds else ""))
        check("/opt/dsh-seed" in text, f"single joint seed dir referenced in {label}")

    if errors:
        print(f"\n{len(errors)} drift problem(s) found.", flush=True)
        return 1
    print("\nAll DSH pins and gates in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
