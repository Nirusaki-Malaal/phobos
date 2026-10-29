# Phobos architecture

One owner's local control plane. API, worker and bot are separate processes using the same application package and local SQLite volume. Each project has its own workspace directory. There is no mandatory rendering or inference runtime in the core image.

```mermaid
graph LR
    UI["Browser dashboard"] --> API["Phobos API"]
    TG["Telegram service"] <--> BOT["Phobos bot process"]
    API --> DB[("Local SQLite: tickets, events, memory, procedures, outbox")]
    BOT --> DB
    WORKER["Phobos worker process"] <--> DB
    WORKER --> MEMORY["Graphify project memory"]
    API --> MEMORY
    MEMORY --> DB
    MEMORY --> GRAPHS["Local graph snapshots and code index"]
    WORKER --> LIBRARY["Skill discovery and learning history"]
    LIBRARY --> GITHUB["Approved GitHub skill sources"]
    LIBRARY --> SK
    WORKER --> FILES["Project files and output artifacts"]
    WORKER --> GATEWAY["Configured model gateway"]
    TS["Host Tailscale Serve"] --> API
    CLI["Local creative CLI"] --> RENDER["Host FFmpeg and Blender"]
    RENDER --> FILES
    H["Hermes native agent"] --> GATEWAY
    D["DSH native agent"] --> GATEWAY
    O["OpenDesign studio"] --> GATEWAY
    T["Terrain index service"] --> FILES
    H --> T
    D --> T
    WORKER --> T
    WORKER --> O
    SK["Ported skills and native tools"] --> H
    SK --> D
    SK --> WORKER
    H --> FILES
    D --> FILES
    O --> FILES
    click UI "../src/phobos/dashboard.html"
    click API "../src/phobos/api.py"
    click TG "https://core.telegram.org/bots/api"
    click BOT "../src/phobos/telegram.py"
    click DB "../src/phobos/store.py"
    click MEMORY "../src/phobos/memory.py"
    click GRAPHS "DOCUMENTATION.md#deep-memory-with-graphify"
    click LIBRARY "../src/phobos/skills.py"
    click GITHUB "../src/phobos/skill_catalog.json"
    click WORKER "../src/phobos/worker.py"
    click FILES "DOCUMENTATION.md#projects-and-files"
    click GATEWAY "../src/phobos/agent.py"
    click TS "DOCUMENTATION.md#raspberry-pi-and-tailscale"
    click CLI "../src/phobos/cli.py"
    click RENDER "../src/phobos/creative.py"
    click H "../hermes/"
    click D "../dsh/"
    click O "../opendesign/"
    click T "../terrain/"
    click SK "../skills/"
```

Ticket state and event/outbox writes share transactions. API routes do no model work. Workers claim one ticket at a time and run bounded model/tool loops. Bot delivery is at least once and retries independently from task execution. Memory is scoped by project and explicitly saved by the owner.

All original agent profiles, native tools and 54 skill bundles are ported, with 16 additional packs (70 total), including REA, huashu-motion and new coding guidance. The native agents keep their embedded memory stores; those databases are separate from Phobos's ticket database. Terrain remains the sole index writer. Its optional ACP and Hermes's OpenCode delegate retain the documented keyless Zen exceptions to gateway routing. Pi hosting is not hardware-certified. Creative jobs run through explicit CLI commands or compatible workers; core workers skip workflows whose native dependencies are absent. An optional creative-worker profile provides heavy rendering dependencies. Graphify snapshots live in the application data volume; Terrain remains the sole writer of .terrain indexes.

Owner-reviewed learning stores versioned drafts, evaluation records and activation state. The latest evaluation must pass before activation; a failed evaluation deactivates the procedure. Active procedures are scoped to the project and their IDs/versions are recorded in ticket events when loaded. Evaluation results are owner attestations, not automated benchmarks or model training.
