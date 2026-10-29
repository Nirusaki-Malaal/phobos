#!/usr/bin/env python3
"""
adr_ops.py — Native Living Architecture Decision Record (L-ADR) & Drift Detection tool
(Hermes plugin).

Handles:
  - create_adr: Scaffolds a Living ADR in <EXECUTION_DIR>/.project/planning/research/ADR-XXX-<title>.md
    with SHA-256 symbol hashes from workspace code.
  - check_drift: Inspects all ADRs, recomputes symbol hashes against current workspace files,
    flags drift, and logs warnings into findings.md.
  - list_adrs: Lists all ADRs and their status.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


WORKSPACE_DIR = Path(
    os.environ.get("WORKSPACE_PATH")
    or os.environ.get("WORKSPACE_DIR")
    or os.environ.get("HERMES_DATA_DIR", "/opt/data/workspace")
)

SCHEMA = {
    "name": "pai_adr_ops",
    "description": "Create and verify Living Architecture Decision Records (L-ADRs). Tracks code symbol references with SHA-256 hashes to detect when architecture decisions drift from active codebase implementations. Actions: create_adr, check_drift, list_adrs.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create_adr", "check_drift", "list_adrs"],
                "description": "Operation to perform: create_adr (scaffold a new living ADR with symbol hashes), check_drift (scan existing ADRs and verify if referenced code symbols have changed), list_adrs (list all ADRs and their status).",
            },
            "adr_number": {
                "type": "string",
                "description": "ADR number, e.g. '001' or '002' (required for create_adr).",
            },
            "title": {
                "type": "string",
                "description": "ADR title, e.g. 'Use Redis for Distributed Caching' (required for create_adr).",
            },
            "context": {
                "type": "string",
                "description": "Context and problem statement (for create_adr).",
            },
            "decision": {
                "type": "string",
                "description": "The decision reached and rationale (for create_adr).",
            },
            "symbols": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of symbol references to anchor and hash, formatted as '@symbol:path/to/file.ext:SymbolName' (for create_adr).",
            },
            "execution_dir": {
                "type": "string",
                "description": "Optional project execution directory path (e.g. /opt/data/workspace/my-project). Defaults to finding .project/planning directory under workspace.",
            },
        },
        "required": ["action"],
    },
}


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[-\s]+", "-", text)
    return text[:60] or "untitled"


def compute_file_hash(file_path: Path) -> Optional[str]:
    """Compute sha256 of file content (first 16 chars)."""
    if not file_path.is_file():
        return None
    try:
        content = file_path.read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]
    except Exception:
        return None


def resolve_planning_dir(execution_dir_str: Optional[str] = None, create: bool = False) -> Optional[Path]:
    """Find or create the .project/planning directory for the project."""
    if execution_dir_str:
        exec_dir = Path(execution_dir_str)
        planning = exec_dir / ".project" / "planning"
        if create:
            try:
                planning.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        return planning

    # Check if WORKSPACE_DIR has a .project/planning directory
    if WORKSPACE_DIR.exists():
        # Direct check
        direct = WORKSPACE_DIR / ".project" / "planning"
        if direct.is_dir():
            return direct
        # Search subdirectories up to depth 4
        try:
            for p in WORKSPACE_DIR.glob("**/.project/planning"):
                if p.is_dir():
                    return p
        except Exception:
            pass

    # Check current directory
    cwd_planning = Path.cwd() / ".project" / "planning"
    if cwd_planning.is_dir():
        return cwd_planning

    # If creation is requested, try WORKSPACE_DIR, then cwd
    if create:
        if WORKSPACE_DIR.exists():
            target = WORKSPACE_DIR / ".project" / "planning"
        else:
            target = Path.cwd() / ".project" / "planning"
        target.mkdir(parents=True, exist_ok=True)
        return target

    return None


def resolve_file_path(rel_path: str, planning_dir: Optional[Path]) -> Path:
    """Resolve relative file path against execution directory or workspace."""
    root = planning_dir.parent.parent if planning_dir else WORKSPACE_DIR
    candidate = (root / rel_path).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("symbol path escapes project")
    return candidate

# ── Action Handlers ──────────────────────────────────────────────────────────


def action_create_adr(inp: Dict[str, Any]) -> str:
    adr_num_raw = str(inp.get("adr_number", "")).strip()
    if not adr_num_raw:
        return json.dumps({"error": "adr_number is required"})

    title = (inp.get("title") or "").strip()
    if not title:
        return json.dumps({"error": "title is required"})

    adr_num = adr_num_raw.zfill(3)
    context = (inp.get("context") or "Describe the architectural context and problem here.").strip()
    decision = (inp.get("decision") or "Describe the chosen architecture decision here.").strip()
    execution_dir = inp.get("execution_dir")

    planning_dir = resolve_planning_dir(execution_dir, create=True)
    if not planning_dir:
        return json.dumps({"error": "Could not determine or create .project/planning directory"})

    research_dir = planning_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    title_slug = slugify(title)
    adr_filename = f"ADR-{adr_num}-{title_slug}.md"
    adr_path = research_dir / adr_filename

    # Process symbols
    raw_symbols = inp.get("symbols") or []
    if isinstance(raw_symbols, str):
        raw_symbols = [s.strip() for s in raw_symbols.split() if s.strip()]

    symbol_lines = []
    symbol_records = []
    for sym in raw_symbols:
        sym = sym.strip()
        if not sym.startswith("@symbol:"):
            continue
        # Extract relative path and symbol name: @symbol:path/to/file.py:SymbolName
        sym_body = sym[len("@symbol:"):]
        parts = sym_body.split(":", 1)
        rel_path = parts[0]
        sym_name = parts[1] if len(parts) > 1 else ""

        full_path = resolve_file_path(rel_path, planning_dir)
        file_hash = compute_file_hash(full_path)
        if file_hash:
            entry = f"- {sym}#sha256:{file_hash}"
            symbol_records.append({"symbol": sym, "path": rel_path, "hash": file_hash, "found": True})
        else:
            entry = f"- {sym}#sha256:FILE_NOT_FOUND"
            symbol_records.append({"symbol": sym, "path": rel_path, "hash": None, "found": False})
        symbol_lines.append(entry)

    symbols_yaml = "\n".join(f"  {line}" for line in symbol_lines) if symbol_lines else "  []"
    symbols_doc = "\n".join(symbol_lines) if symbol_lines else "- (None specified)"
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    adr_content = f"""---
adr: "{adr_num}"
title: "{title}"
status: proposed
date: {date_str}
deciders: [Hermes, User]
symbols:
{symbols_yaml}
supersedes: null
superseded_by: null
---

## Context
{context}

## Decision
{decision}

## Consequences

### Positive
- 

### Negative
- 

### Neutral
- 

## Dialectical Record

### Counterpoints Considered
- 

### Anti-Patterns Avoided
- 

## Symbol Hashes (for drift detection)
{symbols_doc}

## Validation Probes
- 

## Review Triggers
- Any symbol hash mismatch detected by adr_ops(action="check_drift")
- Breaking changes or deprecation in referenced libraries
- Performance regression in validation probes
"""

    adr_path.write_text(adr_content, encoding="utf-8")

    return json.dumps(
        {
            "status": "proposed",
            "adr_number": adr_num,
            "title": title,
            "path": str(adr_path),
            "relative_path": str(adr_path.relative_to(planning_dir.parent)) if adr_path.is_relative_to(planning_dir.parent) else str(adr_path),
            "symbols": symbol_records,
            "message": f"Successfully created {adr_filename}",
        },
        indent=2,
    )


def action_check_drift(inp: Dict[str, Any]) -> str:
    execution_dir = inp.get("execution_dir")
    planning_dir = resolve_planning_dir(execution_dir, create=False)
    if not planning_dir:
        return json.dumps(
            {
                "drift_found": False,
                "adrs_checked": 0,
                "drifts": [],
                "message": "No .project/planning directory found",
            }
        )

    research_dir = planning_dir / "research"
    if not research_dir.is_dir():
        return json.dumps(
            {
                "drift_found": False,
                "adrs_checked": 0,
                "drifts": [],
                "message": f"No research directory found at {research_dir}",
            }
        )

    adr_files = sorted(research_dir.glob("ADR-*.md"))
    if not adr_files:
        return json.dumps(
            {
                "drift_found": False,
                "adrs_checked": 0,
                "drifts": [],
                "message": f"No ADR files found in {research_dir}",
            }
        )

    drifts = []
    adrs_checked = 0

    for adr_path in adr_files:
        adrs_checked += 1
        content = adr_path.read_text(encoding="utf-8", errors="replace")

        # Find symbol hash lines: - @symbol:<path>:<sym>#sha256:<hash>
        matches = re.findall(
            r"^[ \t]*-[ \t]*(@symbol:([^:\s]+):[^\s#]+)#sha256:([a-f0-9]+|FILE_NOT_FOUND)",
            content,
            re.MULTILINE,
        )

        for sym_ref, rel_path, old_hash in matches:
            if old_hash == "FILE_NOT_FOUND":
                continue

            full_path = resolve_file_path(rel_path, planning_dir)
            new_hash = compute_file_hash(full_path)

            if not full_path.exists():
                drifts.append(
                    {
                        "adr": adr_path.name,
                        "symbol": sym_ref,
                        "file": rel_path,
                        "type": "file_missing",
                        "old_hash": old_hash,
                        "new_hash": None,
                    }
                )
            elif new_hash != old_hash:
                drifts.append(
                    {
                        "adr": adr_path.name,
                        "symbol": sym_ref,
                        "file": rel_path,
                        "type": "hash_mismatch",
                        "old_hash": old_hash,
                        "new_hash": new_hash,
                    }
                )

    drift_found = len(drifts) > 0

    # Log warnings into findings.md if drift detected
    if drift_found:
        findings_file = planning_dir / "findings.md"
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [f"\n\n### ⚠️ DRIFT_DETECTED ({now_str})\n"]
        for d in drifts:
            lines.append(
                f"- **ADR**: `{d['adr']}` | **Symbol**: `{d['symbol']}` ({d['type']}: old `{d['old_hash']}` vs new `{d['new_hash']}`)"
            )
        lines.append("\n*Action required: Review ADR decisions against modified implementation.*")

        with open(findings_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))

    return json.dumps(
        {
            "drift_found": drift_found,
            "adrs_checked": adrs_checked,
            "drift_count": len(drifts),
            "drifts": drifts,
            "message": "Drift detected in one or more ADRs. Review needed."
            if drift_found
            else "No drift detected across all checked ADRs.",
        },
        indent=2,
    )


def action_list_adrs(inp: Dict[str, Any]) -> str:
    execution_dir = inp.get("execution_dir")
    planning_dir = resolve_planning_dir(execution_dir, create=False)
    if not planning_dir:
        return json.dumps([])

    research_dir = planning_dir / "research"
    if not research_dir.is_dir():
        return json.dumps([])

    results = []
    for p in sorted(research_dir.glob("ADR-*.md")):
        content = p.read_text(encoding="utf-8", errors="replace")
        adr_num = ""
        title = p.stem
        status = "proposed"
        date_val = ""

        # Extract basic YAML frontmatter fields
        m_num = re.search(r'^adr:\s*["\']?([^"\'\n]+)', content, re.MULTILINE)
        if m_num:
            adr_num = m_num.group(1).strip()
        m_title = re.search(r'^title:\s*["\']?([^"\'\n]+)', content, re.MULTILINE)
        if m_title:
            title = m_title.group(1).strip()
        m_status = re.search(r'^status:\s*["\']?([^"\'\n]+)', content, re.MULTILINE)
        if m_status:
            status = m_status.group(1).strip()
        m_date = re.search(r'^date:\s*["\']?([^"\'\n]+)', content, re.MULTILINE)
        if m_date:
            date_val = m_date.group(1).strip()

        # Count symbol anchors
        symbols = re.findall(r"^[ \t]*-[ \t]*@symbol:[^\s#]+", content, re.MULTILINE)

        results.append(
            {
                "adr_number": adr_num,
                "title": title,
                "status": status,
                "date": date_val,
                "filename": p.name,
                "symbol_count": len(symbols),
            }
        )

    return json.dumps(results, indent=2)


def handle(args, **kwargs) -> str:
    """Hermes tool handler — dispatch an action, always returning a JSON string."""
    if not isinstance(args, dict):
        return json.dumps({"error": "Invalid arguments"})

    action = args.get("action")
    dispatch = {
        "create_adr": action_create_adr,
        "check_drift": action_check_drift,
        "list_adrs": action_list_adrs,
    }

    handler = dispatch.get(action)
    if not handler:
        return json.dumps(
            {
                "error": f"Unknown action: '{action}'",
                "allowed": list(dispatch.keys()),
            }
        )

    try:
        return handler(args)
    except Exception as e:
        return json.dumps({"error": f"Execution error in {action}: {e}"})
