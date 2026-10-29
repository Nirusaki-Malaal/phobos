# Phobos documentation

Phobos is a single-owner, self-hosted personal AI helper. It combines a persistent ticket queue, project files, explicit memory, model execution through a gateway, a browser interface and optional Telegram control.

## Implementation status

Available: local notes and reports; an eight-step assistant with project file inspection and note-writing tools; authenticated ticket, memory and feedback APIs; SQLite claims and event history; a dashboard; Telegram text commands; queued channel notifications and daily UTC reports; consistent database backups.

The complete pai-stack services, plugins, scripts and 54 skill bundles are now included. Phobos can directly invoke the portable notebook/ADR tools and discover/read all skills. Terrain and OpenDesign calls require their optional profiles. Native Hermes/DSH profiles retain shell, browser, Git, scheduling and memory features. Direct Phobos orchestration of agent sessions, voice/file ingestion through the Phobos bot, evaluated skill generation, new creative rendering workers, remote dispatch and Pi hardware certification remain pending. See [port details](PORTING.md).

## Local installation

Install Python 3.12+ and uv. From the repository:

```sh
uv sync --frozen
uv run phobos init
uv run phobos serve
```

`init` exclusively creates `.env` with mode 0600 and a random owner token. It refuses to overwrite an existing file. It also creates `workspace/personal`. Read the token locally and enter it in the dashboard at http://127.0.0.1:8080. It is held in page memory, not browser storage.

Run `uv run phobos worker` in a second terminal. Submit a local note through the dashboard or:

```sh
uv run phobos ticket 'My first local note' --workflow note
uv run phobos report
```

Without a worker, queued tickets remain queued. A healthy API does not imply a healthy worker or gateway. The dashboard refresh is manual.

## Configuration

Configuration comes from environment variables or the repository `.env`; exported environment variables take precedence. Unknown keys are ignored so deployment-only settings can coexist. Restart processes after changing settings.

| Variable | Default / purpose |
|---|---|
| `PHOBOS_API_TOKEN` | Required, at least 24 characters; single-owner bearer token |
| `PHOBOS_HOST` | `127.0.0.1`; Compose uses `0.0.0.0` inside its network |
| `PHOBOS_PORT` | `8080`; Compose publishes only to host loopback |
| `PHOBOS_DATA_DIR` | `./data`; SQLite and output artifacts |
| `PHOBOS_WORKSPACE_DIR` | `./workspace`; dedicated project directory root |
| `PHOBOS_GATEWAY_URL` | `http://127.0.0.1:4000/v1` for local execution |
| `PHOBOS_GATEWAY_KEY` | Gateway credential; no direct provider keys in the agent |
| `PHOBOS_MODEL` | Gateway model alias; required for assistant workflows |
| `PHOBOS_DOCKER_GATEWAY_URL` | Compose only; defaults to `http://host.docker.internal:4000/v1` |
| `PHOBOS_TELEGRAM_TOKEN` | Optional BotFather token |
| `PHOBOS_TELEGRAM_USERS` | Comma-separated numeric authorized user IDs |
| `PHOBOS_TELEGRAM_CHANNEL` | Channel ID or `@channelname`; optional |
| `PHOBOS_TELEGRAM_PROJECT` | `personal`; project used by ordinary messages and memory commands |
| `PHOBOS_REPORT_HOUR_UTC` | `18`; daily report is enqueued at or after this UTC hour |

Use `.env.example` as the complete setting reference. Do not add provider credentials to project files; agent tools can inspect visible files in the selected project.

## Projects and files

Each project is a lowercase slug, for example `personal` or `website-redesign`. Files are under `<workspace>/<project>/`. A ticket cannot select the workspace root itself. Symlink aliases to other projects and paths resolving outside the project are rejected. Hidden files and `.env`, `.pem`, `.key` targets are excluded from agent inspection.

The built-in project tools list up to 200 entries, read up to 16000 characters from a UTF-8 regular file, and save notes to `notes/ticket-<id>.md`. Ported native notebook/ADR operations write project vault/planning files through isolated subprocess calls. Phobos itself does not expose arbitrary shell execution; native agent profiles retain their upstream tool surfaces. Keep credentials outside the workspace regardless of filenames. Filesystem confinement assumes trusted host processes; concurrent malicious changes to mounted directories are outside the current isolation model.

## Ticket lifecycle and execution

```text
queued → running → completed / failed
queued → paused → queued
queued / paused / running → cancelled
failed / cancelled → queued (explicit retry)
```

Pausing is supported before execution. Cancelling a running task prevents subsequent agent steps and completion; an in-flight gateway call may take up to its timeout to return. Cancellation does not reverse prior tool effects. Review those effects before retrying.

SQLite `BEGIN IMMEDIATE` serializes claims across worker processes. Every claim has a unique token and a renewable 120-second lease. Workers heartbeat every ten seconds. A new claim pass marks expired running tickets failed; it never automatically replays them. Completion must present the current claim token and a live lease. A restarted worker can process other queued work.

`completed` means the selected workflow delivered its output: a note, a report, or the assistant's final response. It is not proof of external tasks the assistant merely describes. There is no generated percent-complete or speculative hours-saved number.

Outputs are stored in SQLite and `data/artifacts/<ticket-id>.md`. Local notes additionally live in the selected project. The artifact endpoint only exposes completed outputs. A cancelled task can leave partial local files; review and remove them manually when appropriate.

The model gets a maximum of eight turns, eight calls per turn, and 2048 requested output tokens per request. Timeouts and empty responses fail the task. Gateway responses must support OpenAI-compatible chat and tool calls.

## API

Public: `GET /healthz`, the dashboard at `/`, and API schema documentation at `/docs`.

All `/api/` routes require `Authorization: Bearer <PHOBOS_API_TOKEN>`. This is one trusted owner's installation, not a hosted multi-user tenant system. Allowlisted Telegram users share that owner's authority across projects.

| Method and route | Purpose |
|---|---|
| `POST /api/tickets` | JSON: `project`, `title`, `prompt`, `workflow` (`assistant`, `note`, `report`) |
| `GET /api/tickets?project=personal&limit=50` | List up to 100 tickets |
| `GET /api/tickets/{id}` | Ticket status and output |
| `GET /api/tickets/{id}/events` | Ordered execution events |
| `POST /api/tickets/{id}/{action}` | `pause`, `resume`, `cancel`, `retry` |
| `GET /api/artifacts/{id}` | Download completed Markdown output |
| `POST /api/memories/{project}` | JSON: `content`; explicit preference |
| `GET /api/memories/{project}` | Inspect saved preferences |
| `DELETE /api/memories/{project}/{id}` | Delete a preference |
| `POST /api/feedback/{id}` | JSON: `rating` (-1 or 1), optional `comment` |
| `GET /api/report` | Last 24 hours' completed/failed counts and current status totals |

Example body for a provider-free ticket:

```json
{"project":"personal","title":"Shopping list","prompt":"Milk, rice, coffee","workflow":"note"}
```

## Telegram setup and delivery

1. Create a bot using Telegram's BotFather and store the token in `.env`.
2. Configure `PHOBOS_TELEGRAM_USERS` with trusted numeric user IDs.
3. For channel updates, add the bot to your channel with permission to post and set `PHOBOS_TELEGRAM_CHANNEL`.
4. Run `uv run phobos telegram`, or enable the Compose `telegram` profile.
5. Send `/start` privately to the bot.

Long polling uses outbound HTTPS and needs no public webhook endpoint. Disable any existing webhook for this bot using Telegram's official `deleteWebhook` method before using polling. Only one polling process may use the token.

| Command | Behavior |
|---|---|
| Plain text | Assistant ticket in the default project |
| `/new PROJECT PROMPT` | Assistant ticket in the named project |
| `/note TEXT` | Local note ticket |
| `/tasks`, `/status ID`, `/result ID` | Inspect work and output |
| `/pause ID`, `/resume ID`, `/cancel ID`, `/retry ID` | Lifecycle controls |
| `/remember TEXT`, `/memories`, `/forget ID` | Explicit default-project memory |
| `/report` | Work summary |

Updates from unlisted users or non-private chats are ignored. Ticket creation and memory writes deduplicate Telegram update IDs. Responses go through the persistent outbox before the polling offset advances. Channel messages include ticket titles and projects, so configure a channel appropriate for that information.

Delivery uses retries with exponential backoff capped at one hour. Delivery is **at least once**: if Telegram accepts a message but the process crashes before acknowledgment is saved, a notification can repeat. Daily reports are deduplicated by UTC date and cover a rolling 24-hour window. Polling and reporting resume when the bot process starts; there is no backfill for missed days. Long results are truncated in Telegram and remain available in full through the API. Telegram attachment uploads are not implemented.

## Memory and self-learning

Use `/remember` or the memory API to save a preference. The latest 20 saved preferences from the ticket's project are included in assistant context, with length bounded at write time. Preferences are project-scoped. Inspect and delete them any time; replace a preference by deleting the old one and creating another.

Feedback is recorded separately and is not automatically promoted into trusted instructions. The model is told to treat memories and file contents as data, but this is not proof of resistance to every prompt injection. Current tools expose only a limited file and note surface. Automatic skill generation, evaluation-based promotion and model training are future work.

## Docker deployment

The image uses Python 3.12 and a non-root UID/GID of 10001, a read-only root filesystem, a small temporary filesystem, and no Docker socket mount. API, worker and optional bot share one named data volume and a dedicated workspace mount. No mandatory Node runtime, Redis or external database is needed.

Generate `.env` first, then:

```sh
docker compose -p phobos config --quiet
docker compose -p phobos up -d --build
docker compose -p phobos --profile telegram up -d
docker compose -p phobos logs --tail=100
docker compose -p phobos down
```

The bound workspace must be writable by UID/GID 10001. For an existing workspace, grant that identity appropriate directory ACLs rather than recursively changing ownership of shared files. For a new dedicated workspace, the README's ownership command is sufficient. The named data volume initializes from the image-owned `/data` directory.

Compose defaults the gateway to the included `llm-gateway` service. Set `PHOBOS_DOCKER_GATEWAY_URL=http://host.docker.internal:4000/v1` to use a gateway on the Docker host. A gateway listening only on host loopback may not be reachable through the host gateway address on Linux; configure an interface reachable from Docker, a shared network endpoint, or a private remote gateway. Do not expose provider credentials or gateway ports publicly.

The application image and Compose files have automated validation in CI. Building and starting Docker still require a running Docker daemon; local implementation checks alone do not establish container runtime success.

## Raspberry Pi and Tailscale

Target a Raspberry Pi 4/5 with a **64-bit OS**, adequate storage and cooling. Raspberry Pi OS Lite is suitable for a headless control plane. Follow Docker's Debian ARM64 installation instructions for a 64-bit Pi OS installation. The 32-bit Docker package track is being deprecated; 32-bit Pi images are not a project target.

Run the lightweight API, queue, notes and bot on the Pi. Use a gateway on another private machine for larger models. Blender, transcription and video processing will be optional workers; remote dispatch is not implemented yet. CPU/GPU suitability, ARM64 image builds and actual Pi runtime performance remain release gates.

Install Tailscale on the host following its official instructions, join your private tailnet, and proxy the loopback API:

```sh
tailscale serve --bg http://127.0.0.1:8080
tailscale serve status
```

Open the HTTPS URL printed by Tailscale from another authorized tailnet device. Tailnet access policies still apply, and Phobos still requires its owner token. Use Serve for private access; public Funnel exposure is unnecessary. Telegram polling continues over outbound HTTPS.

Owning the host gives you control of local files and state. It does not make external model providers, Telegram or Tailscale hosted infrastructure part of your hardware, or guarantee offline operation.

References: [Raspberry Pi OS](https://www.raspberrypi.com/documentation/computers/os.html), [Docker on Pi](https://docs.docker.com/engine/install/raspberry-pi-os/), [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve), [Telegram Bot API](https://core.telegram.org/bots/api).

## Backup, restore and updates

For a consistent SQLite snapshot locally:

```sh
uv run phobos backup backups/phobos.db
```

The command uses SQLite's backup API, including committed WAL state. A full backup also needs `data/artifacts`, `workspace`, and separately protected `.env`. Pause producers and stop workers when capturing a coordinated database-and-files snapshot. In Docker, use `docker compose -p phobos exec api phobos backup /data/backup.db`, then copy that snapshot and files out of the volume using your normal backup tooling.

To restore, stop API, worker and bot processes; restore the snapshot as `<data_dir>/phobos.db`, restore the matching artifacts/workspace, and remove stale WAL/SHM files only while all processes are stopped. Preserve ownership and restart. Never overwrite a live database. Test restoration on a separate data directory before relying on a backup.

Update dependencies deliberately and regenerate `uv.lock`. Run `make check` and Compose validation, build the image, back up state, and then restart. Database schema version 3 adds skill usage to version 2 procedures/evaluations; version 1 and 2 databases upgrade additively on startup. Back up before upgrades. Newer database versions are rejected rather than silently downgraded.

## Verification and troubleshooting

```sh
uv sync --frozen
make check
```

The CI template at `ci/verify-phobos.yml` checks lint, formatting, tests, Compose configuration, an image build and API startup on AMD64. Copy it to `.github/workflows/ci.yml` with a workflow-authorized GitHub credential to activate it; it is inactive in this initial publication. ARM hardware tests are separate pending release checks. Tests use a fake model gateway and fake Telegram API, so they make no paid inference calls or live channel posts.

After building the image, `uv run python scripts/smoke-compose.py` runs an API-to-worker-to-artifact check in a disposable `phobos-smoke` Compose project. It deletes only that test project's volumes on exit. Pass `--context default` when your selected Docker Desktop context is unavailable but the host daemon is running.

- **401:** use the generated owner token, not the gateway key.
- **Queued forever:** start a worker using the same data directory.
- **Assistant failed:** configure a tool-capable gateway model and check network access.
- **Permission denied:** check the workspace's UID/GID/ACLs and volume ownership.
- **Telegram silent:** check the private chat, numeric allowlist, webhook state and single polling instance.
- **Worker interrupted:** review event history and existing note files before explicit retry.
- **Database locked:** keep SQLite on a local filesystem; do not share the data directory over NFS between hosts.

## Migration and future integrations

The old installation and persistent volumes are untouched. This repository starts with new history and includes the full source/skill port. Existing notebook and ADR formats are retained by the imported tools; automatic data copying and memory-database conversion are not implemented. Do not point Phobos SQLite at upstream agent databases. Native profiles own their own new named volumes.

Hermes, DSH, Terrain, OpenDesign and the gateway are included as optional profiles through `compose.agents.yaml`. Four Phobos creative packs are included. Local clip/audio/Blender commands require optional host binaries; remote creative job dispatch and transcription remain pending. Upstream attribution is retained.

## Ported services and skills

See [PORTING.md](PORTING.md) for the service and capability map, source inventory, current validation and retained attribution. `GET /api/skills` and `GET /api/skills/{name}` expose the imported library to authenticated owners. The assistant also has `skills_list`, `skill_view`, `pai_notebook_ops`, `pai_adr_ops`, `pai_ops_design_ops` and `pai_terrain_ops` tools. Registry-wide Terrain actions remain in the native profiles to avoid cross-project operations through a ticket.


## Learning from reviewed work

Phobos can reuse versioned procedures reviewed by its owner. This changes its prompt context; it does not train model weights.

1. Complete a source ticket, then create a draft from its useful instructions with `POST /api/procedures/{project}`: `{"name":"weekly-report","instructions":"Use concise sections and cite input files","source_ticket":1}`.
2. Apply/check the draft manually against a different completed ticket in the same project. Record your assessment at `POST /api/procedures/{project}/{id}/evaluate`: `{"ticket_id":2,"passed":true,"notes":"Checked file references and section order"}`. Recording an evaluation does not execute the draft or prove it was used.
3. Activate via `POST /api/procedures/{project}/{id}/state/activate`. A passing latest evaluation is required. Only one version of a named procedure is active per project.
4. A failed evaluation returns the procedure to draft. `/state/retire` removes it from future context; prior ticket outputs are retained. Create a new draft to revise instructions.

All routes require the owner bearer token. List versions with `GET /api/procedures/{project}` and inspect evidence with `GET /api/procedures/{project}/{id}`. The dashboard offers the same draft/evaluate/activate flow. Up to eight active procedures are included per assistant turn; ticket events record the loaded IDs/versions. Feedback remains separate from promotion.

## Local creative commands

Run these on the host with FFmpeg and/or Blender installed. They are not tools in the model's autonomous execution loop and the default Docker image does not contain those heavy binaries.

```sh
uv run phobos creative capabilities
uv run phobos creative clip --project personal --source recording.mp4 --output clips/demo.mp4 --start 10 --duration 20
uv run phobos creative audio --project personal --source recording.mp4 --output audio/demo.wav --duration 20
uv run phobos creative blender --project personal --source scene.json --output renders/scene.blend
```

Inputs and outputs are project-relative. Jobs refuse overwrites, limit video segments to ten minutes, and have process timeouts. Audio output is mono 16kHz WAV. Blender accepts up to 32 cubes/spheres in a bounded JSON specification and produces editable `.blend` plus a PNG. See `skills/phobos-blender/SKILL.md` for the specification. Rendering may be slow on a Pi; a separate host can run the CLI against a copied project.

The new `phobos-design`, `phobos-motion`, `phobos-blender`, and `phobos-stream-clipping` packs complement all 54 imported skills. OpenDesign remains the design renderer. The motion pack contains workflow guidance and an upstream huashu-motion link; the external huashu renderer is not bundled. Automatic highlights, transcription, captions and publishing remain pending.


## Detailed dashboard

The dashboard at `/` includes overview metrics, a project conversation, searchable/filterable tasks, event history, output download, feedback, saved preferences, graph exploration, a skill library, reviewed learning and integration configuration status. It uses local assets and the original Phobos orbital-P SVG; no external fonts or trackers are loaded.

Connect with your owner token. API access remains authenticated, and the browser keeps the token only in tab memory. Use **Talk to Phobos** to select an AI assistant task, local note or work report. When no gateway model is configured, the assistant choice is disabled and local workflows remain available. The visible worker state reflects a recent heartbeat, not a provider health probe. Integration cards show configuration status and do not claim live connectivity.

The 20-second walkthrough in `docs/assets/phobos-dashboard-20s.mp4` uses `scripts/demo-dashboard.py` with isolated sample data. It performs a real local note workflow and does not call a live model or Telegram. `scripts/record-dashboard.mjs` captures the live browser and verifies the final H.264 video's duration. Playwright and a Chromium build are optional recording dependencies; FFmpeg/ffprobe must be installed.

## Deep memory with Graphify

The official `graphifyy==0.9.97` package is included. Phobos builds a project graph from the latest 200 completed/failed tickets, 200 explicit preferences, active procedures among the latest 100 versions, and bounded feedback/skill-usage records. SQLite retains the full history beyond that recall window. Record links have extracted provenance; associations based on shared words are labelled inferred. Recall uses lexical relevance plus one-hop relationship expansion, not embeddings or an automatic semantic extractor. All retrieved content remains reference data.

```sh
uv run phobos memory recall 'SQLite backups' --project personal
uv run phobos memory rebuild --project personal
uv run phobos memory index --project personal
```

Code indexing uses local Graphify AST extractors with a 200-file/8-MB budget, skips hidden files, dependency/build directories and paths escaping the project, and reports truncation. Re-run indexing after edits. It writes only under the Phobos data directory, never Terrain's `.terrain` index. Every assistant task recalls graph evidence before its model loop. Queries rebuild experience snapshots from current records so feedback and deletion are reflected; forgetting a preference removes the previous snapshot immediately.

Authenticated API: `GET /api/graph/{project}?query=...`, `POST /api/graph/{project}/index`. Telegram: `/recall QUERY`.

## Skill discovery and the feedback loop

70 skills are bundled, including REA, huashu-motion, Go, Rust, TypeScript and original debugging/performance guidance. REA's skill does not register its MCP server or install analysis engines; use the bundled skill's connection guide for the intended native agent. Native profiles see the bundled library; dynamic downloads currently belong to the Phobos core library.

```sh
uv run phobos skill list
uv run phobos skill find rust --project personal
uv run phobos skill find 'your-topic' --online
uv run phobos skill install SKILL_NAME
uv run phobos skill view SKILL_NAME
```

The assistant can call `find_skills`, `install_skill`, `skill_view`, `memory_recall` and `propose_procedure`. It searches installed guidance first. Missing relevant candidates may be installed automatically when `PHOBOS_SKILL_AUTO_INSTALL=true`. Online discovery searches skill paths in repositories listed by `PHOBOS_SKILL_SOURCES`, resolves an immutable commit, and retains license/provenance records. The installer rejects oversized bundles, traversal, hidden files, symlinks and sources with no license. It never runs scripts or package hooks. Approved source membership is an owner's policy choice, not proof that all content is safe.

Loading a skill records its content digest and source against the task. Latest owner feedback contributes a bounded ranking adjustment within that project. Graph memory connects outcomes and feedback with skill usage. `propose_procedure` creates an inactive draft; an owner must evaluate and activate it. Graph recall, ranking and reviewed procedures form the learning loop; downloaded instructions never become owner authority simply by being stored.

API: `GET /api/skill-search?query=...&project=...&online=true`, `POST /api/skill-install/{name}`. Telegram: `/skills QUERY`. GitHub rate limits or unavailable sources return errors; no direct-provider key is needed for discovery.

## Creative tickets and optional runtimes

`clip`, `audio`, `blender`, and `transcribe` workflows accept a JSON prompt with `source`, `output`, optional `start` and `duration`. Paths remain project-relative. Core workers skip creative workflows whose executable/module is absent. Start a compatible host worker or the optional Docker creative-worker profile to consume them.

```sh
uv run phobos ticket '{"source":"recording.mp4","output":"clips/demo.mp4","start":10,"duration":20}' --workflow clip
uv run phobos creative motion-create --project personal --output motion/launch
uv run phobos creative motion-render --project personal --source motion/launch
uv sync --extra transcription
uv run phobos creative transcribe --project personal --source audio/demo.wav --output audio/transcript.json --model-dir ./models/whisper
```

Motion commands require the `om` CLI, Node 22+ and Chrome/Chromium for rendering. They are explicit operator commands; arbitrary motion source is not exposed as an autonomous core model tool. The creative Docker image provides a pinned huashu runtime and native render tools; its complete runtime is still unverified. The default core image remains independent of these binaries. The transcription extra is optional and is not installed in that image; provision it on a host worker and supply a local faster-whisper model directory containing `model.bin`. Transcription sets `local_files_only=True` and never downloads weights during a job. Real transcription requires a real provisioned model and remains a separate validation gate.
