# LLM Gateway

LiteLLM proxy on `:4000`. The only model endpoint Hermes uses.

## Routing

- Hermes requests model `default` from `http://llm-gateway:4000/v1`. All provider mapping, fallbacks, and credentials live in `llm-gateway/config.yaml`, templated by `scripts/sync-models.py`.
- Point it at anything OpenAI-compatible via `.env`: local (LM Studio, Ollama, llama.cpp, vLLM), LAN node, or cloud OpenAI-compatible API. Cloud keys (`OPENROUTER_*`, `MISTRAL_*`, etc.) enable fallback chains.
- `OPENAI_COMPATIBLE_MODEL` defaults to `default`; most local servers just use the loaded model.

## Sync

```bash
make sync      # discover models on keyed providers, liveness-probe, reload gateway
```

## Pinning + reasoning caveat

- Image is digest-pinned in `docker-compose.yaml` — the gateway is a single point of failure for every route, so upgrades are deliberate.
- Routes use mistral/provider mapping with per-model `drop_params`: our LiteLLM synthesizes a conflicting nested object if reasoning params pass through (Sep-25 incident). Verify any routing change with a pair-shape probe (reasoning_effort + forced tool call) before committing.

## Telemetry

Optional Langfuse tracing via `LANGFUSE_*` in `.env` — this is the audit trail for gateway traffic.
