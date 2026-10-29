---
name: research
description: Per-project persistent wiki (Karpathy llm-wiki pattern) in .project/research/. Create, ingest, query, and lint interlinked Markdown knowledge. Use when user mentions wiki, knowledge base, notes, or starts/queries/lints research.
---

# Research — Per-Project Wiki

> Persistent, compounding knowledge base as interlinked Markdown at `.project/research/` in each project.
> Based on Karpathy's LLM Wiki pattern (via `llm-wiki` on hermesskins.io), adapted for pai-stack.

## Wiki Location (per-project, derived from EXECUTION_DIR)

```bash
WIKI="${EXECUTION_DIR}/.project/research"
```

- `EXECUTION_DIR` is set by Hermes on Turn 1 (see `agents` skill).
- In-container path = `/opt/data/workspace/<project>/.project/research`.
- Host path = `<WORKSPACE_DIR>/<project>/.project/research`.
- No global `research/` at workspace root — each project owns its vault.
- Legacy notebook vaults (`<notebook>/{notes/,sources/,notebook.json}`) are **not** migrated; new knowledge goes in wiki dirs below.

## Architecture: per-project wiki

```
<EXECUTION_DIR>/.project/research/   ← $WIKI
├── SCHEMA.md                        ← conventions, tag taxonomy, domain
├── index.md                         ← sectioned catalog, one-line summaries
├── log.md                           ← chronological action log (append-only, rotate at 500)
├── raw/articles|papers|.../         ← Layer 1: immutable sources (never edit)
├── entities/                        ← Layer 2: people, orgs, products, models
├── concepts/                        ← Layer 2: topics, techniques
├── comparisons/                     ← Layer 2: side-by-side analyses
├── queries/                         ← Layer 2: filed answers worth keeping
└── _archive/                        ← superseded pages (removed from index)
```

Layer 1 raw = immutable. Layer 2 wiki = agent-owned. Layer 3 SCHEMA = rules.

## Orient First (every session touching research)

1. Read `SCHEMA.md` (domain, conventions, tags).
2. Read `index.md` (what exists).
3. Read last 20 lines of `log.md` (recent activity).
4. For 100+ pages, `pai_notebook_ops(action="search")` the topic before creating anything.

Skipping this causes duplicates and missed cross-refs.

## Init New Wiki (vault empty)

1. Confirm `$WIKI` resolves (echo path; must be under `<EXECUTION_DIR>/.project/research`).
2. Ask domain (be specific, e.g. "Aurora Postgres tuning").
3. Write `SCHEMA.md` (template below, customized), `index.md` (section headers, `Total pages: 0`), `log.md` (`create | Wiki initialized`).
4. Suggest first sources.

### SCHEMA.md (adapt to domain)

- Filenames: lowercase, hyphens (`transformer-architecture.md`).
- Every page: YAML frontmatter (`title, created, updated, type: entity|concept|comparison|query|summary, tags, sources`), ≥2 `[[wikilinks]]`, bump `updated` on edit, add to `index.md`, append to `log.md`.
- Tags only from taxonomy (add to SCHEMA first).
- Thresholds: create when entity appears in 2+ sources or is central to one; split at ~200 lines; archive (move to `_archive/`, delink) when superseded.
- Contradictions: keep both claims with dates, set `contested: true` + `contradictions: [slug]`, surface in lint.
- Confidence: `high|medium|low` for opinion-heavy/fast-moving/single-source claims.
- Raw files get `source_url, ingested, sha256` (body hash; skip re-ingest when unchanged, flag drift when changed).

## Ingest (URL, file, paste)

1. Capture raw → `raw/<articles|papers|...>/<slug>.md` with frontmatter. Re-ingest: recompute sha, skip if same, flag drift if different. Never modify `raw/` afterwards.
2. Check existing: `index.md` + `pai_notebook_ops(action="search")` for mentioned entities.
3. Write/update Layer-2 pages per thresholds. Cross-ref ≥2 `[[wikilinks]]` (both directions). Tags from taxonomy. Append `^[raw/...]` provenance on 3+-source syntheses. Set `confidence` honestly.
4. Update `index.md` (alphabetical, count, date) + append `log.md` (`ingest | Title — files created/updated`).
5. Report files changed. If 10+ pages touched, confirm scope with user first.
6. Hermes glue (mandatory): embed `@symbol:path/to/file.ext:Symbol` anchors for code claims; `mnemosyne_triple_add(subject="notebook:<nb>:note:<id>", predicate="anchors_symbol", object="@symbol:...")`; reverse-lookup via `mnemosyne_triple_query` before creating.

Tool mapping: `add_source_url`/`add_source_file` → raw capture; `add_note` → Layer-2 page body; `search` → duplicate check; `ask_notebook` → grounded draft.

## Query

1. `index.md` → relevant pages (+ `pai_notebook_ops(action="search")` for 100+ pages).
2. Read top-3 pages max (never bulk-read).
3. Synthesize with citations (`[[page]]`, `@symbol:` for code).
4. File back substantial novel synthesis to `queries/` or `comparisons/` (skip trivial lookups).
5. Append `log.md` (`query | question — filed? y/n`).

## Lint (health-check)

Check and report grouped by severity (broken links > orphans > drift > contested > stale > style), then append `log.md` (`lint | N issues`): orphans (0 inbound links), broken `[[links]]`, index completeness (fs vs index), frontmatter validity + taxonomy, stale (`updated` >90d behind newest source), `contested:true` / `contradictions:` / `confidence:low` / single-source-no-confidence, raw `sha256` drift, pages >200 lines, tag sprawl, log rotation (>500 → `log-YYYY.md`).

## Hermes Integration

- Vault is plain Markdown on the mounted workspace — new pages are immediately searchable with `grep`.
- Planning files (`task_plan/findings/progress.md`) stay in `<EXECUTION_DIR>/.project/planning/`; research conclusions graduate to wiki + Living ADRs (`.project/planning/research/ADR-XXX.md` via `pai_adr_ops`).
- Probes: `/opt/data/probes/<slug>/` scripts; results ingested as `EVIDENCE:` notes with `@symbol:` anchors.
- Turn 1: load this skill only when research task or `<EXECUTION_DIR>/.project/research/SCHEMA.md` exists; orient reads are SCHEMA + index + log-tail-20 only.

## Pitfalls

- Never edit `raw/`. Never skip orient/index/log updates. No pages for passing mentions. No isolated pages. Frontmatter required. Tags from taxonomy only. Split >200 lines. Rotate log. Contradictions explicit, never silent overwrite.