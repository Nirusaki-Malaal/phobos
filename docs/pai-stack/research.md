# Research

How Hermes does file-based research. Vault lives at `<EXECUTION_DIR>/.project/research/` (per-project, derived from EXECUTION_DIR).

## Vault layout

Per-project wiki: `SCHEMA.md`, `index.md`, `log.md`, `raw/`, `entities/`, `concepts/`, `comparisons/`, `queries/` — no legacy notebook vaults. Procedure lives in the `research` skill (`skill_view(name="research")`): index-first, top-3 pages max.

Cross-brain anchors: embed `@symbol:path/to/file.ext:SymbolName` in every note; link note → symbol → decision via Mnemosyne triples. Before new research, `mnemosyne_recall` + `pai_notebook_ops(search)` for existing notes on the symbol.

## Empirical probes

When docs are ambiguous, run a micro-probe in `/opt/data/probes/`:

```
Hypothesis → micro-script (Python/shell) → execute (30s / 256MB guardrails) → EVIDENCE: note → Research Brain
```

Evidence notes carry status `supported | refuted | inconclusive`, always include `@symbol:` anchors, and store the probe script alongside the result. Full protocol: `autonomous-tech-learner` skill, Hypothesis-Testing section.

## Inquiry trees + Living ADRs

Decompose complex questions into 4 perspectives: Systems Architecture, Security & Threats, Developer Ergonomics, Failure Modes. Actively seek counterpoints (GitHub issues, anti-patterns, version gotchas, incident reports).

Living ADRs live in `<EXECUTION_DIR>/.project/planning/research/ADR-XXX.md`:

- YAML frontmatter (status, symbols, supersession chain)
- Symbol hashes for drift detection (`@symbol:path:Symbol#sha256:…`, via `pai_adr_ops check_drift`)
- Dialectical record, validation probes, review triggers
- Lifecycle: `proposed` → `accepted` → `superseded` / `deprecated`

## Kanban swarms

Configured in `hermes/config.yaml` (`kanban.workers`, `kanban.patterns`).

| Worker | Role | Budget |
|---|---|---|
| `researcher` | Deep-dive, ingest, probe, write evidence notes | 8k tokens, temp 0.3 |
| `synthesizer` | Consolidate evidence, surface counterpoints | 16k tokens, temp 0.2 |
| `adr_author` | Generate Living ADR with symbol hashes | 16k tokens, temp 0.1 |
| `coder` | Supervise OpenCode handoff (see [delegation.md](delegation.md)) | 8k tokens, temp 0.2 |

Patterns: `deep_research` (4-perspective parallel → synthesize → ADR), `quick_fact_check` (question → evidence → answer), `empirical_validation` (hypothesis → probe → note), `build_task` (code handoff).

Handoff context: `notebook_id`, `execution_dir`, `anchor_symbols`, `inquiry_question`, `perspective`; required returns: `evidence_note_ids`, `symbol_anchors`, `confidence`, `unresolved_questions`. Workers are research-only (no code changes).
