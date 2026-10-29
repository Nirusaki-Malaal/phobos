<p align="center"><img src="src/phobos/static/phobos-mark.svg" width="76" alt="Phobos orbital P logo"></p>
<h1 align="center">Phobos</h1>
<p align="center"><strong>Your personal agent. Your workspace. Your machine.</strong></p>
<p align="center">A self-hosted AI helper for tracked work, connected memory, and procedures that improve with your feedback.</p>

[![Dashboard preview](docs/assets/dashboard.png)](docs/assets/phobos-dashboard-20s.mp4)

**[Watch the 20-second walkthrough](docs/assets/phobos-dashboard-20s.mp4)** · [Documentation](docs/DOCUMENTATION.md) · [Architecture](docs/architecture.md) · [Source port](docs/PORTING.md)

> **Alpha.** The walkthrough uses an isolated, labelled demo workspace and real local note execution. It does not show live model inference, Telegram delivery, or certified Raspberry Pi hardware.

## A workspace for getting things done

- **Talk to your agent:** create tasks from a project conversation, follow progress, inspect results, download outputs, and leave feedback.
- **Keep work visible:** durable tickets, event history, pause/resume/cancel/retry, worker leases, and persistent artifacts.
- **Remember the useful parts:** Graphify connects task outcomes, owner preferences, feedback, active procedures, and skill usage into a local project graph. Optional code indexing uses Graphify's deterministic extractors.
- **Find the right skill:** 70 bundled skills, including REA, huashu-motion, Python, Go, Rust, TypeScript, design, Docker and debugging. Search approved online repositories when knowledge is missing; downloaded bundles keep an immutable source revision, file hashes and licenses.
- **Improve through review:** feedback influences skill ranking. Reusable procedure drafts need an owner-recorded passing evaluation and activation before future reuse. This is context learning, not model training.
- **Use Telegram:** private-chat task commands, memory recall, durable notifications and daily channel reports, with an explicit numeric user allowlist.
- **Create locally:** FFmpeg clips and audio extraction, bounded editable Blender scenes, optional local Whisper transcription, and huashu-motion CLI integration.
- **Keep your stack:** optional Hermes, DSH, Terrain, OpenDesign and LiteLLM profiles are ported from pai-stack, along with all 54 original skill bundles.

## Start locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Nirusaki-Malaal/phobos.git
cd phobos
uv sync --frozen
uv run phobos init
uv run phobos serve
```

In another terminal:

```sh
uv run phobos worker
```

Open **http://127.0.0.1:8080** and select **Connect owner access**. Use `PHOBOS_API_TOKEN` from the generated `.env`. The token stays in the browser tab's memory and clears on reload.

Local notes and reports work without a model. For assistant tasks, configure `PHOBOS_MODEL`, `PHOBOS_GATEWAY_URL` and `PHOBOS_GATEWAY_KEY` in `.env`. All core model requests go through the configured gateway. Native upstream profiles retain their documented keyless Zen exceptions.

### Try the isolated dashboard demo

```sh
uv run python scripts/demo-dashboard.py
```

Open **http://127.0.0.1:8094/?demo=1**. The script prints the path of its generated demo-only owner token. Sample tasks, memories and procedures live under `/tmp/phobos-dashboard-demo`; your real application data is untouched.

## Run with Docker

```sh
uv run phobos init              # or configure .env from .env.example
make up
```

The default API and worker use a local SQLite volume, a project workspace mount, a non-root user and a read-only container filesystem.

```sh
make gateway-up hermes-up       # optional full native agent
make terrain-up                 # optional code intelligence
make design-up                  # optional OpenDesign

docker compose -p phobos --profile telegram up -d telegram
```

Configure the corresponding `.env.example` settings before starting an optional profile. Use separate Telegram bot tokens for separate pollers.

The optional creative image adds Blender, FFmpeg, Chromium and a pinned huashu-motion runtime:

```sh
docker compose -p phobos build api
docker compose -p phobos --profile creative build creative-worker
docker compose -p phobos --profile creative up -d creative-worker
```

Creative images are larger and their full runtime remains a separate validation target. Transcription additionally needs the `transcription` Python extra and a provisioned local model; weights are never downloaded by a transcription job.

## A small machine can be home

Phobos's control plane is designed for a 64-bit Raspberry Pi 4/5, a home server, or a laptop. Keep data on local storage and run heavy inference or rendering on a more capable machine. **ARM64 builds and physical Raspberry Pi execution are not yet certified.**

For private remote access, install Tailscale on the host and run:

```sh
tailscale serve --bg http://127.0.0.1:8080
```

Keep owner authentication enabled. See the [hosting guide](docs/DOCUMENTATION.md#raspberry-pi-and-tailscale).

## Project layout

```text
src/phobos/          API, worker, agent, Telegram, graph memory and dashboard
skills/              70 procedural skill bundles and retained licenses
hermes/ · dsh/       Optional native agents and plugins
terrain/             Optional code-intelligence service
opendesign/           Optional design service
llm-gateway/          Model routing and provider configuration
docker/              Core and optional creative Dockerfiles
scripts/             Demo, video recording, model management and smoke checks
tests/               Core and integration regression coverage
docs/                Guides, architecture, migration and walkthrough assets
```

## Check the implementation

```sh
make check
node --check src/phobos/static/dashboard.js
docker compose -p phobos --profile '*' config --quiet
```

The CI workflow is supplied as `ci/verify-phobos.yml`. To enable GitHub Actions, copy it to `.github/workflows/ci.yml` using a GitHub credential with workflow permission. The publishing token used for this initial upload does not have that scope.

The dashboard recording exercises a live API → worker → local artifact flow and checks for browser JavaScript errors. To reproduce it, install Playwright/Chromium and FFmpeg, start the isolated demo, and run `node scripts/record-dashboard.mjs`. The token and data are demo-only; no real owner credentials are included in the recording.

## Current boundaries

Live provider and Telegram checks require configured credentials. Full heavyweight service runtime parity, real Blender and Whisper-model runs, remote worker dispatch, voice/attachment ingestion, and physical Pi verification remain pending. A task marked completed means its workflow delivered an output; it does not prove an external action merely described by a model.

The core exposes bounded project tools. Full shell, browser and native-agent session features are supplied by the optional Hermes/DSH profiles. Skills are reference material and do not grant new permissions.

## License and provenance

New Phobos code and original SVG branding are [MIT licensed](LICENSE). Reused code and skill bundles retain their own notices and terms. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the [pai-stack source map](docs/PORTING.md). The repository starts with fresh Phobos history; upstream authorship is preserved.
