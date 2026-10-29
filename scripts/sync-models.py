#!/usr/bin/env python3
"""
sync-models.py — Keep llm-gateway serving only models that actually reply.

Pipeline (one goal, nothing else):
  1. Read provider API keys from .env.
  2. Ask each keyed provider for its model list.
  3. Probe every candidate with a tiny inference call; drop silent ones.
  4. Write llm-gateway/config.yaml with survivors only, reload the gateway.

Providers without a key are skipped. Nothing unprobed ever reaches the config,
so Hermes never routes to a listed-but-dead model.
"""

import argparse
import concurrent.futures
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("sync-models")


def setup_logging(debug: bool = False) -> None:
    """Configure stdlib logging: INFO by default, DEBUG with --debug."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False


# Terminal colors (banner headers only — log lines go through logging)
C_BOLD = "\033[1m"
C_RESET = "\033[0m"


def log_ok(msg: str):
    logger.info("✓ %s", msg)


def log_info(msg: str):
    logger.info("%s", msg)


def log_skip(msg: str):
    # Skipped providers (no key) are noise by default — debug only.
    logger.debug("○ %s", msg)


def log_warn(msg: str):
    logger.warning("%s", msg)


def parse_env_file(path: Path) -> dict:
    """Parse key=value pairs from .env file."""
    env = {}
    if not path.exists():
        return env
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.replace("export ", "").strip()
            v = v.strip().strip("'\"")
            env[k] = v
    return env


def resolve_probe_url(url: str) -> str:
    """Map host.docker.internal to 127.0.0.1 if running directly on host without DNS mapping."""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname and "host.docker.internal" in parsed.hostname:
        try:
            socket.gethostbyname(parsed.hostname)
        except socket.gaierror:
            return urllib.parse.urlunparse(parsed._replace(netloc=parsed.netloc.replace(parsed.hostname, "127.0.0.1")))
    return url


def fetch_json(url: str, headers: Optional[dict] = None, timeout: int = 5) -> Optional[dict]:
    """Fetch JSON with clean timeout and error handling."""
    probe_url = resolve_probe_url(url)
    req = urllib.request.Request(
        probe_url,
        headers={"User-Agent": "pai-stack-sync/2.0", "Accept": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def post_json(url: str, payload: dict, headers: Optional[dict] = None, timeout: int = 5) -> Optional[dict]:
    """Post JSON with clean timeout and error handling (HTTP errors count as no-reply)."""
    probe_url = resolve_probe_url(url)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        probe_url,
        data=data,
        headers={"User-Agent": "pai-stack-sync/2.0", "Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def probe_openai_chat(chat_base: str, model_id: str, api_key: str, timeout: int = 10) -> Optional[dict]:
    """Tiny chat completion against any OpenAI-compatible endpoint."""
    url = f"{chat_base.rstrip('/')}/chat/completions"
    payload = {"model": model_id, "messages": [{"role": "user", "content": "hi"}]}
    if "o1" in model_id or "o3" in model_id:
        payload["max_completion_tokens"] = 40
    else:
        payload["max_tokens"] = 40
    headers = {"Authorization": f"Bearer {api_key}"}
    return post_json(url, payload, headers=headers, timeout=timeout)


def probe_gemini(model_id: str, api_key: str, timeout: int = 10) -> Optional[dict]:
    """Tiny generateContent call (Gemini has no OpenAI-compatible chat shape here)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": "hi"}]}], "generationConfig": {"maxOutputTokens": 40}}
    return post_json(url, payload, headers={"x-goog-api-key": api_key}, timeout=timeout)


def probe_anthropic(model_id: str, api_key: str, timeout: int = 10) -> Optional[dict]:
    """Tiny Messages call (Anthropic has no /chat/completions shape)."""
    url = "https://api.anthropic.com/v1/messages"
    payload = {"model": model_id, "max_tokens": 40, "messages": [{"role": "user", "content": "hi"}]}
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    return post_json(url, payload, headers=headers, timeout=timeout)


def model_replies(prov: dict, model_id: str, api_key: str) -> Optional[dict]:
    """Route the liveness probe to the right API shape for this provider."""
    p_id = prov["id"]
    if p_id == "gemini":
        return probe_gemini(model_id, api_key)
    if p_id == "anthropic":
        return probe_anthropic(model_id, api_key)
    return probe_openai_chat(prov["chat_base"], model_id, api_key)


def response_has_reply_text(data: Optional[dict]) -> bool:
    """True only if the probe response carries an actual reply.

    A bare HTTP 200 is not enough: some Gemini variants (transcribe, TTS,
    robotics, heavy-thinking) return 200 with empty content and no text
    parts. Those count as no-reply and are dropped.
    """
    if not data:
        return False
    try:
        if "choices" in data:  # OpenAI-compatible
            choice = data["choices"][0]
            msg = choice.get("message", {})
            if msg.get("content") or choice.get("text"):
                return True
            # Structured-only reply (e.g. tool calls) still counts as alive.
            if msg.get("tool_calls") or msg.get("function_call"):
                return True
            # Reasoning-only reply still counts as alive. Reasoning models
            # (DeepSeek-R1/Qwen3/Spark via LM Studio etc.) return 200 with
            # content="" + reasoning_content when max_tokens is consumed by
            # thinking (finish_reason=length). The server + model work;
            # the probe budget was just too small for content.
            if msg.get("reasoning_content") or msg.get("reasoning") or msg.get("thought"):
                return True
            return False
        if "content" in data:  # Anthropic
            parts = data["content"]
            if isinstance(parts, list):
                return any(isinstance(p, dict) and p.get("text") for p in parts)
            return False
        if "candidates" in data:  # Gemini
            for cand in data["candidates"]:
                parts = cand.get("content", {}).get("parts", [])
                if any(isinstance(p, dict) and p.get("text") for p in parts):
                    return True
            return False
    except Exception:
        return False
    # Unknown shape but valid 200 JSON — give it the benefit of the doubt.
    return True


def extract_response_text(data: Optional[dict]) -> str:
    """Extract the full human-readable reply text from a provider response."""
    if not data:
        return ""
    try:
        if "choices" in data:
            msg = data["choices"][0].get("message", {})
            content = msg.get("content", "")
            if content:
                return content
            # Reasoning-only reply — surface thinking text for the log line.
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or msg.get("thought") or ""
            if isinstance(reasoning, dict):
                reasoning = reasoning.get("content") or reasoning.get("text") or str(reasoning)
            return str(reasoning) if reasoning else str(data)
        if "content" in data:
            parts = data["content"]
            if isinstance(parts, list) and parts:
                return str(parts[0].get("text", ""))
        if "candidates" in data:
            parts = data["candidates"][0].get("content", {}).get("parts", [])
            if parts:
                return str(parts[0].get("text", ""))
    except Exception:
        pass
    return str(data)


# ══════════════════════════════════════════════════════════════════════════════
# Providers: key in .env → list ALL models → probe each → keep repliers.
# No curated allowlists — if a model is exhausted, it simply won't be listed
# the next time the script runs.
# ══════════════════════════════════════════════════════════════════════════════

PROVIDERS = [
    {
        "id": "primary",
        "name": "Primary OpenAI-Compatible",
        "env_key": "OPENAI_COMPATIBLE_API_KEY",
        "env_url": "OPENAI_COMPATIBLE_BASE_URL",
        "default_url": "http://host.docker.internal:8080/v1",
        "type": "openai_compatible",
    },
    {
        "id": "kilo",
        "name": "Kilo Gateway",
        "env_key": "KILO_GATEWAY_API_KEY",
        "endpoint": "https://api.kilo.ai/api/gateway/models",
        "chat_base": "https://api.kilo.ai/api/gateway",
        "prefix": "kilo",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "env_key": "OPENROUTER_API_KEY",
        "endpoint": "https://openrouter.ai/api/v1/models",
        "chat_base": "https://openrouter.ai/api/v1",
        "prefix": "openrouter",
    },
    {
        "id": "mistral",
        "name": "Mistral AI",
        "env_key": "MISTRAL_API_KEY",
        "endpoint": "https://api.mistral.ai/v1/models",
        "chat_base": "https://api.mistral.ai/v1",
        "prefix": "mistral",
    },
    {
        "id": "groq",
        "name": "Groq",
        "env_key": "GROQ_API_KEY",
        "endpoint": "https://api.groq.com/openai/v1/models",
        "chat_base": "https://api.groq.com/openai/v1",
        "prefix": "groq",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "env_key": "DEEPSEEK_API_KEY",
        "endpoint": "https://api.deepseek.com/models",
        "chat_base": "https://api.deepseek.com/v1",
        "prefix": "deepseek",
    },
    {
        "id": "nvidia",
        "name": "NVIDIA NIM",
        "env_key": "NVIDIA_API_KEY",
        "endpoint": "https://integrate.api.nvidia.com/v1/models",
        "chat_base": "https://integrate.api.nvidia.com/v1",
        "prefix": "nvidia",
        # OpenAI-compatible; explicit api_base (LiteLLM's built-in nvidia/ routing
        # 404s on this endpoint — verified via probe).
        "api_base": "https://integrate.api.nvidia.com/v1",
        # Model ids already carry vendor prefixes (nvidia/, meta/, google/...) —
        # strip the provider's own prefix for clean aliases.
        "strip_prefix": "nvidia/",
    },
    {
        # Ollama Cloud is OpenAI-compatible at ollama.com/v1 (no cloud.ollama.com).
        # api_base → emitted as openai/ mapping with explicit base in the config.
        "id": "ollama_cloud",
        "name": "Ollama Cloud",
        "env_key": "OLLAMA_CLOUD_API_KEY",
        "endpoint": "https://ollama.com/v1/models",
        "chat_base": "https://ollama.com/v1",
        "prefix": "ollama",
        "api_base": "https://ollama.com/v1",
    },
    {
        # Aion Labs (api.aionlabs.ai) — OpenAI-compatible; model ids carry the
        # aion-labs/ vendor prefix, so openai/ mapping keeps them intact.
        "id": "aion",
        "name": "Aion Labs",
        "env_key": "AION_LABS_API_KEY",
        "endpoint": "https://api.aionlabs.ai/v1/models",
        "chat_base": "https://api.aionlabs.ai/v1",
        "prefix": "aion",
        "api_base": "https://api.aionlabs.ai/v1",
        # Model ids carry the aion-labs/ vendor prefix — strip it for aliases.
        "strip_prefix": "aion-labs/",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "env_key": "OPENAI_API_KEY",
        "endpoint": "https://api.openai.com/v1/models",
        "chat_base": "https://api.openai.com/v1",
        "prefix": "openai",
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "endpoint": "https://api.anthropic.com/v1/models",
        "prefix": "anthropic",
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "env_key": "GEMINI_API_KEY",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models",
        "prefix": "gemini",
    },
    {
        "id": "zen",
        "name": "OpenCode Zen",
        "env_key": "",
        "endpoint": "https://opencode.ai/zen/v1/models",
        "chat_base": "https://opencode.ai/zen/v1",
        "prefix": "zen",
    },
]


def parse_listing(p_id: str, data: dict) -> List[str]:
    """Extract all model ids from a /models response."""
    found_ids: List[str] = []
    if p_id == "gemini" and isinstance(data.get("models"), list):
        for m in data["models"]:
            if isinstance(m, dict) and m.get("name") and "generateContent" in m.get("supportedGenerationMethods", []):
                found_ids.append(m["name"].replace("models/", ""))
    elif isinstance(data.get("data"), list):
        found_ids = [m.get("id") for m in data["data"] if isinstance(m, dict) and m.get("id")]
    return found_ids


def auth_headers(prov: dict, api_key: str) -> dict:
    if prov["id"] == "gemini":
        return {"x-goog-api-key": api_key}
    if prov["id"] == "anthropic":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    if prov["id"] == "zen":
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def probe_candidates(prov: dict, candidates: List[str], api_key: str) -> Dict[str, float]:
    """Probe candidates; return {model_id: probe latency in seconds} for repliers."""
    log_info(f"Probing {len(candidates)} {prov['name']} models for liveness...")
    live: Dict[str, float] = {}

    def check_model(m_id):
        start = time.monotonic()
        response = model_replies(prov, m_id, api_key)
        return m_id, response, time.monotonic() - start

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_model, m_id): m_id for m_id in candidates}
        for future in concurrent.futures.as_completed(futures):
            m_id, response, elapsed = future.result()
            if response and response_has_reply_text(response):
                live[m_id] = elapsed
                reply = extract_response_text(response)
                logger.info("✓ %s → %s (%.2fs)", m_id, reply, elapsed)
            else:
                # No reply text (or HTTP error) — debug-only, use --debug to list.
                logger.debug("○ %s (no reply text, dropped)", m_id)
    return live


def discover_primary(prov: dict, env: dict) -> Tuple[bool, Dict[str, float]]:
    """List + probe the local/remote OpenAI-compatible box."""
    base_url = (
        env.get(prov["env_url"])
        or env.get("PRIMARY_LLM_BASE_URL")
        or env.get("LLM_BASE_URL")
        or prov["default_url"]
    )
    api_key = env.get(prov["env_key"]) or "not-needed"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key not in ("not-needed", "none", "") else {}

    clean_url = base_url.rstrip("/")
    models_url = f"{clean_url}/models" if clean_url.endswith("/v1") else f"{clean_url}/v1/models"
    data = fetch_json(models_url, headers=headers)
    if not data and not clean_url.endswith("/v1"):
        data = fetch_json(f"{clean_url}/models", headers=headers)
    if not data:
        return False, {}

    raw = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    ids = [m.get("id") if isinstance(m, dict) else str(m) for m in raw if m]
    ids = [i for i in ids if i]
    if not ids:
        return True, {}
    return True, probe_candidates(prov | {"chat_base": clean_url}, ids, api_key)


def discover_provider_models(prov: dict, env: dict) -> Tuple[bool, Dict[str, float]]:
    """List + probe one cloud provider. No key → skipped. No reply → dropped."""
    if prov.get("type") == "openai_compatible":
        return discover_primary(prov, env)

    api_key = env.get(prov["env_key"], "").strip() if prov.get("env_key") else ""
    if prov.get("env_key") and (not api_key or api_key == "not-needed"):
        return False, {}

    data = fetch_json(prov["endpoint"], headers=auth_headers(prov, api_key), timeout=6) or {}
    candidates = parse_listing(prov["id"], data)
    if not candidates:
        return True, {}
    return True, probe_candidates(prov, candidates, api_key)


def generate_litellm_config(
    discovered: Dict[str, Dict[str, float]],
    env: dict,
) -> Tuple[str, List[str], List[str]]:
    """Generate LiteLLM config.yaml string with all discovered models as independent entries.

    `discovered` maps provider id → {model_id: probe latency in seconds}.

    Returns (config_text, ordered_aliases, fallback_order):
    - ordered_aliases is alphabetically sorted (case-insensitive) and drives
      the `model_list` emission order and the DSH seed model list, so those
      stay deterministic across runs.
    - fallback_order is latency-ranked (fastest probe first, ties broken
      alphabetically) and drives the `default` pin + router fallback chain,
      so `model: default` hits the fastest-replying live model first.
    """
    lines = [
        "# LiteLLM Proxy Configuration for pai-stack",
        "# Centralized LLM gateway managing all model routing and provider credentials.",
        "# NOTE: routes use mistral/ provider mapping (not openai/) so reasoning params",
        "# are stripped pre-flight — see note at primary-models section below.",
        "# NOTE: model entries are alphabetically sorted (case-insensitive) — do not",
        "# hand-reorder; re-running scripts/sync-models.py regenerates this order.",
        "# NOTE: the `default` entry + fallback chain below are latency-ranked",
        "# (fastest probe first) — re-ranked on every run.",
        "",
        "model_list:",
    ]

    seen = set()
    alias_params: Dict[str, List[str]] = {}
    alias_section: Dict[str, str] = {}
    alias_latency: Dict[str, float] = {}

    PRIMARY_SECTION = "  # ── Upstream Language Models (Clean Canonical Names) ──────────────────"

    # 1. Primary language models (clean short canonical names, no duplicates)
    # NOTE (2026-09-25): primary routes are mistral-mapped (not openai/) + drop_params
    # because our LiteLLM synthesizes a conflicting nested `reasoning` object from ANY
    # `reasoning_effort` value, and strict OpenAI-compatible upstreams 400 on the pair.
    # The mistral provider spec excludes reasoning params so they are stripped pre-flight
    # (verified live: 200 + forced tool-call + SSE streaming).
    primary_models = discovered.get("primary", {})
    primary_chat_unique = []
    for m_id, latency in primary_models.items():
        clean_id = m_id.rstrip("/")
        short_name = clean_id.split("/")[-1] if "/" in clean_id else clean_id
        if not short_name:
            short_name = m_id.strip("/") or "primary-model"
        if short_name not in seen:
            seen.add(short_name)
            primary_chat_unique.append((short_name, m_id, latency))

    for short_name, m_id, latency in primary_chat_unique:
        block = [
            f"      model: mistral/{m_id}",
            "      api_base: os.environ/OPENAI_COMPATIBLE_BASE_URL",
            "      api_key: os.environ/OPENAI_COMPATIBLE_API_KEY",
            "      timeout: 1800",
            "      drop_params: true",
        ]
        alias_params.setdefault(short_name, block)
        alias_section.setdefault(short_name, PRIMARY_SECTION)
        alias_latency.setdefault(short_name, latency)

    # 2. Other providers (clean prefixed names, no aliases) — collect only.
    for prov in PROVIDERS:
        p_id = prov["id"]
        if p_id == "primary":
            continue
        models = discovered.get(p_id, {})
        if not models:
            continue

        prefix = prov["prefix"]
        env_key = prov["env_key"]
        section = f"  # ── {prov['name']} ───────────────────────────────────────────────────"

        strip_prefix = prov.get("strip_prefix")  # ids carrying their own vendor prefix (nvidia/, aion-labs/)

        for m, latency in models.items():
            clean = m.replace(strip_prefix, "") if strip_prefix else m.replace(f"{prefix}/", "")
            alias = f"{prefix}/{clean}"
            if alias in seen:
                continue
            seen.add(alias)

            if p_id == "kilo":
                block = [
                    f"      model: mistral/{clean}",
                    "      api_base: https://api.kilo.ai/api/gateway",
                    f"      api_key: os.environ/{env_key}",
                    "      timeout: 1800",
                    "      drop_params: true",
                ]
            elif p_id == "openrouter":
                block = [
                    f"      model: openrouter/{clean}",
                    f"      api_key: os.environ/{env_key}",
                    "      timeout: 1800",
                ]
            elif p_id == "nvidia":
                # NVIDIA NIM provider prefix is nvidia_nim/ (bare nvidia/
                # is not a LiteLLM provider and fails router init with
                # "LLM Provider NOT provided"). Alias keeps the nvidia/
                # gateway name; only the litellm routing model changes.
                block = [
                    f"      model: nvidia_nim/{clean}",
                    f"      api_key: os.environ/{env_key}",
                    "      timeout: 1800",
                ]
            elif p_id == "zen":
                block = [
                    f"      model: openai/{clean}",
                    "      api_base: https://opencode.ai/zen/v1",
                    "      timeout: 1800",
                ]
            elif prov.get("api_base"):
                # OpenAI-compatible providers whose ids carry vendor prefixes
                # (nvidia/nemotron-*, aion-labs/*, ollama cloud) — openai/ mapping
                # with explicit api_base keeps the id intact. Use the full id `m`
                # for routing; `clean` is only used for the alias name above.
                block = [
                    f"      model: openai/{m}",
                    f"      api_base: {prov['api_base']}",
                    f"      api_key: os.environ/{env_key}",
                    "      timeout: 1800",
                    "      drop_params: true",
                ]
            else:
                block = [
                    f"      model: {prefix}/{clean}",
                    f"      api_key: os.environ/{env_key}",
                    "      timeout: 1800",
                ]
            alias_params.setdefault(alias, block)
            alias_section.setdefault(alias, section)
            alias_latency.setdefault(alias, latency)

    # 3. Two orders from here on:
    # - model_list emission stays alphabetically sorted (case-insensitive) so
    #   the file diff and the DSH seed stay deterministic across runs.
    # - the `default` pin + fallback chain is latency-ranked (fastest first,
    #   ties broken alphabetically) so `model: default` hits the quickest
    #   replier first and walks down by speed.
    ordered_aliases: List[str] = sorted(alias_params.keys(), key=lambda a: (a.casefold(), a))
    fallback_order: List[str] = sorted(
        alias_params.keys(),
        key=lambda a: (alias_latency.get(a, float("inf")), a.casefold(), a),
    )

    # Emit model_list in that sorted order; provider comment follows the entry
    # (emitted once per contiguous provider run) for readability only.
    last_section: Optional[str] = None
    for alias in ordered_aliases:
        section = alias_section.get(alias)
        if section and section != last_section:
            lines.append(section)
            last_section = section
        lines.extend([
            f"  - model_name: {alias}",
            "    litellm_params:",
            *alias_params[alias],
            "",
        ])

    # 4. Default alias — Hermes/DSH `model: default` must always resolve.
    # Pinned last (outside the sorted order). Deployment mirrors the fastest
    # live model; router fallbacks walk every remaining model fastest-first
    # until one replies. Regenerated on every run so `default` never goes
    # stale after `make sync`.
    if fallback_order:
        first = fallback_order[0]
        lines.append("  # ── Default (fastest live model first, rest via fallbacks) ────────────")
        lines.extend([
            "  - model_name: default",
            "    litellm_params:",
            *alias_params[first],
            "",
        ])

    # Router Settings (default fallbacks walk the live list fastest-first)
    lines.extend([
        "# ── Router Settings ─────────────────────────────────────────────────────",
        "router_settings:",
        "  timeout: 1800              # 30m request timeout for large models & deep reasoning",
        "  stream_timeout: 1800       # 30m chunk timeout for slow reasoning token streams",
    ])
    if fallback_order:
        rest = fallback_order[1:]
        if rest:
            quoted = ", ".join(f'"{a}"' for a in rest)
            lines.extend([
                "  fallbacks:",
                f'    - {{"default": [{quoted}]}}',
            ])
    lines.extend([
        "",
        "# ── General Settings ─────────────────────────────────────────────────────",
        "general_settings:",
        "  drop_params: true",
        "  request_timeout: 1800      # 30m global HTTP timeout",
        "",
    ])

    return "\n".join(lines), ordered_aliases, fallback_order


DSH_ENTRY_ID = "llm-pi-ai"
DSH_PROVIDER_ID = "pai-gateway"


def generate_dsh_seed(ordered_aliases: List[str]) -> str:
    """Generate the dsh/settings.yaml seed snippet from the live gateway alias list.

    Single source of truth: every `make sync` regenerates this, so the DSH
    model picker never drifts from the gateway. `ordered_aliases` arrives
    alphabetically sorted from generate_litellm_config — emitted as-is so DSH
    (and any future consumer) stays sorted without re-sorting. `default`
    (full fallback chain) is always first, so DSH keeps working even if its
    live volume copy is stale — run `make dsh-refresh` to push the full list.
    """
    lines = [
        "# GENERATED by scripts/sync-models.py — do not hand-edit.",
        "# Rebuilt on every `make sync` from the same liveness-probed alias list",
        "# as llm-gateway/config.yaml, then pushed live with `make dsh-refresh`.",
        "#",
        "# FORMAT: a Cordis profile-patch snippet (entry-list dialect), NOT a",
        "# $DSH_HOME/settings.yaml document. The legacy user-layer import consumes that",
        "# path without merging providers (verified: file renamed to .imported, tree",
        "# unchanged, UI still DeepSeek-only), so this snippet is appended to",
        "# profiles/web/cordis.patch.yml instead — see `make dsh-settings`, which does",
        "# it idempotently and restarts (HMR is off, so file edits need a restart;",
        "# per-request re-read applies to credentials, not to the provider list).",
        "# The Models page merges UI edits into the same patch file; provider ID is",
        "# permanent — sessions reference it.",
        f"- id: {DSH_ENTRY_ID}",
        "  config:",
        "    providers:",
        f"      {DSH_PROVIDER_ID}:",
        "        api: openai-completions",
        "        baseURL: http://llm-gateway:4000/v1",
        "        apiKeyEnv: OPENAI_API_KEY",
        "        models:",
        "          - id: default   # full fallback chain — always works, even if stale",
    ]
    for alias in ordered_aliases:
        lines.append(f"          - id: {alias}")
    lines.extend([
        "",
        "# ── Gateway-only overlays (static — same every run) ─────────────────────",
        "# DSH serves ONLY the stack gateway: the native DeepSeek chat provider is",
        "# unmounted and the agent default points at the `default` fallback chain.",
        "# A user-patch row addresses a base row by id with last-write-wins",
        "# (same mechanism as the documented tool-ralph overlay), so `disabled`",
        "# here removes deepseek-official from the picker without touching the image.",
        "- id: llm-deepseek",
        "  disabled: true",
        "",
        "- id: llm-deepseek-account",
        "  disabled: true",
        "",
        "- id: agent-default-model",
        "  config:",
        "    provider: pai-gateway",
        "    model: default",
        "",
    ])
    return "\n".join(lines)


def restart_gateway(repo_root: Path) -> bool:
    """Restart the llm-gateway container if running; no-op otherwise."""
    try:
        res = subprocess.run(
            ["docker", "compose", "-p", os.environ.get("COMPOSE_PROJECT_NAME", "phobos"), "-f", "compose.yaml", "ps", "-q", "llm-gateway"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        if res.returncode != 0 or not res.stdout.strip():
            log_info("llm-gateway container is not currently running (skipped reload).")
            return False

        subprocess.run(
            ["docker", "compose", "-p", os.environ.get("COMPOSE_PROJECT_NAME", "phobos"), "-f", "compose.yaml", "restart", "llm-gateway"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        log_ok("llm-gateway restarted. Changes are live!")
        return True
    except Exception as e:
        log_warn(f"Could not auto-restart llm-gateway: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Write llm-gateway config with only models that reply (keyed providers only)."
    )
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview generated config without saving.")
    parser.add_argument("--debug", "-d", "--verbose", "-v", action="store_true",
                        help="Show dropped / skipped (not-connected) models. Hidden by default.")
    parser.add_argument("--env", type=Path, default=Path(".env"), help="Path to .env (default: .env).")
    parser.add_argument("--output", type=Path, default=Path("llm-gateway/config.yaml"), help="Config destination.")
    parser.add_argument("--dsh-output", type=Path, default=Path("dsh/settings.yaml"), help="DSH seed snippet destination.")
    args = parser.parse_args()

    setup_logging(debug=args.debug)

    repo_root = Path(__file__).resolve().parent.parent
    env_file = args.env if args.env.is_absolute() else (repo_root / args.env)
    output_file = args.output if args.output.is_absolute() else (repo_root / args.output)
    dsh_output_file = args.dsh_output if args.dsh_output.is_absolute() else (repo_root / args.dsh_output)

    print(f"\n{C_BOLD}pai-stack — Model Synchronizer{C_RESET}", flush=True)

    env = parse_env_file(env_file)
    for k, v in os.environ.items():
        if k not in env:
            env[k] = v

    discovered: Dict[str, Dict[str, float]] = {}
    skipped: List[str] = []

    for prov in PROVIDERS:
        active, live = discover_provider_models(prov, env)
        p_name = prov["name"]
        if active and live:
            discovered[prov["id"]] = live
            fastest = sorted(live, key=lambda m: (live[m], m.casefold(), m))
            shown = ", ".join(f"{m} ({live[m]:.2f}s)" for m in fastest[:4])
            log_ok(f"{p_name:<26} → {len(live)} live models, fastest first: {shown}{'...' if len(live) > 4 else ''}")
        elif active:
            log_warn(f"{p_name:<26} → key present but 0 models replied")
        else:
            skipped.append(p_name)
            log_skip(f"{p_name:<26} (no key in .env, skipped)")

    if skipped:
        # Default output stays quiet — one summary line, details need --debug.
        log_info(f"{len(skipped)} provider(s) skipped (no key) — rerun with --debug to list them.")

    yaml_content, ordered_aliases, fallback_order = generate_litellm_config(discovered, env)
    dsh_seed = generate_dsh_seed(ordered_aliases)

    if fallback_order:
        log_ok(f"`default` pinned to fastest live model: {fallback_order[0]}")

    if args.dry_run:
        print(f"\n{C_BOLD}── Preview (Dry Run) ──{C_RESET}\n{yaml_content}", flush=True)
        log_info(f"DSH seed preview: {len(ordered_aliases)} live models + default (not saved).")
        return

    # Backup & Write
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists():
        shutil.copy2(output_file, output_file.with_suffix(".yaml.bak"))

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(yaml_content)

    print(flush=True)
    log_ok(f"Saved configuration to {output_file.relative_to(repo_root)}")

    dsh_output_file.parent.mkdir(parents=True, exist_ok=True)
    if dsh_output_file.exists():
        shutil.copy2(dsh_output_file, dsh_output_file.with_suffix(".yaml.bak"))

    with open(dsh_output_file, "w", encoding="utf-8") as f:
        f.write(dsh_seed)

    log_ok(f"Saved DSH seed ({len(ordered_aliases)} models + default) to {dsh_output_file.relative_to(repo_root)}")
    log_info("Push it live with: make dsh-refresh (DSH must be up)")

    restart_gateway(repo_root)
    print(flush=True)


if __name__ == "__main__":
    main()
