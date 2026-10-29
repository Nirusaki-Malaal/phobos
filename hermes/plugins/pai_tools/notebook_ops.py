#!/usr/bin/env python3
"""
notebook_ops.py — Native File-Based Research Brain tool (Hermes plugin).

Stores notebooks, research notes, and external sources as human-readable
Markdown files with YAML frontmatter in <EXECUTION_DIR>/.project/research/.

Actions:
  - list_notebooks
  - create_notebook
  - get_notebook
  - add_note
  - add_source_url
  - add_source_file
  - poll_source_status
  - get_source
  - search
  - ask_notebook
"""

import json
import os
import re
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional


# EXECUTION_DIR is REQUIRED - set by Hermes per session
# No fallback to workspace root - each project owns its vault
_execution_dir = os.environ.get("EXECUTION_DIR", "")
EXECUTION_DIR = Path(_execution_dir).resolve()
if not _execution_dir or not EXECUTION_DIR.is_dir():
    raise RuntimeError(
        "EXECUTION_DIR environment variable not set or path does not exist. "
        "Hermes must set EXECUTION_DIR before invoking pai_notebook_ops."
    )

RESEARCH_DIR = EXECUTION_DIR / ".project" / "research"
# Per-project wiki (llm-wiki pattern): SCHEMA.md, index.md, log.md, raw/,
# entities/, concepts/, comparisons/, queries/ live at RESEARCH_DIR root.
# Legacy notebook vaults (<notebook>/{notes/,sources/,notebook.json}) are
# kept readable alongside the wiki. WIKI_PATH aliases RESEARCH_DIR.
LLM_GATEWAY_URL = os.environ.get(
    "LLM_GATEWAY_URL",
    os.environ.get("LLM_GATEWAY_BASE_URL", "http://llm-gateway:4000/v1"),
)
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "not-needed")

SCHEMA = {
    "name": "pai_notebook_ops",
    "description": "Query and manage the per-project Research Brain vault. Use for external knowledge: RFCs, papers, API docs, web articles, Living ADRs, and evidence notes. Backed by human-readable Markdown files in <EXECUTION_DIR>/.project/research/.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_notebooks", "create_notebook", "search", "add_note", "add_source_url", "poll_source_status", "get_source", "add_source_file", "ask_notebook", "get_notebook"],
                "description": "Operation to perform. list_notebooks: list all notebooks. create_notebook: create a new notebook by name. search: full-text search across notebook content. add_note: save text content as a permanent note. add_source_url: ingest a URL (article, doc, RFC) into a notebook. poll_source_status: check async ingestion status of a source. get_source: retrieve full extracted text of a source. add_source_file: upload a local file (PDF, markdown, etc.) as a source. ask_notebook: grounded RAG query across notebook sources/notes. get_notebook: fetch notebook metadata including source/note IDs.",
            },
            "notebook_id": {
                "type": "string",
                "description": "Target notebook ID (e.g. 'notebook:abc123'). Required for search, add_note, add_source_url, add_source_file, ask_notebook.",
            },
            "notebook_name": {
                "type": "string",
                "description": "Notebook name for create_notebook action.",
            },
            "notebook_description": {
                "type": "string",
                "description": "Optional description for create_notebook action.",
            },
            "query": {
                "type": "string",
                "description": "Search query string (for search action) or question (for ask_notebook action).",
            },
            "title": {
                "type": "string",
                "description": "Title for add_note, add_source_url, or add_source_file actions.",
            },
            "content": {
                "type": "string",
                "description": "Text content to save as a note (for add_note action).",
            },
            "url": {
                "type": "string",
                "description": "URL to ingest as a source (for add_source_url action).",
            },
            "source_id": {
                "type": "string",
                "description": "Source ID (e.g. 'source:abc123') for poll_source_status and get_source actions.",
            },
            "file_path": {
                "type": "string",
                "description": "Path to file inside container (e.g. /opt/data/whitepaper.pdf) for add_source_file action.",
            },
            "answer_model": {
                "type": "string",
                "description": "Optional answer model override for ask_notebook action.",
            },
        },
        "required": ["action"],
    },
}


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[-\s]+", "-", text)
    return text[:60] or "untitled"


def extract_anchors(text: str) -> List[str]:
    if not text:
        return []
    raw = re.findall(r"@symbol:[^\s\)\`\"\']+", text)
    cleaned = [r.rstrip(".,;:)!?'\"") for r in raw]
    return sorted(list(set(cleaned)))


def clean_html(html_content: str) -> tuple[str, str]:
    """Extract page title and clean text from raw HTML without external dependencies."""
    # 1. Title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_content, re.IGNORECASE | re.DOTALL)
    title = unescape(title_match.group(1)).strip() if title_match else "Extracted Source"
    title = re.sub(r"\s+", " ", title)

    # 2. Strip scripts and styles
    text = re.sub(r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<nav[^>]*>.*?</nav>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<footer[^>]*>.*?</footer>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 3. Headings to markdown
    text = re.sub(r"<h1[^>]*>(.*?)</h1>", r"\n# \1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<h2[^>]*>(.*?)</h2>", r"\n## \1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<h3[^>]*>(.*?)</h3>", r"\n### \1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<p[^>]*>(.*?)</p>", r"\n\1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", text, flags=re.DOTALL | re.IGNORECASE)

    # 4. Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)

    # 5. Normalize whitespace
    lines = [line.strip() for line in text.splitlines()]
    clean_lines = []
    blank_streak = 0
    for line in lines:
        if not line:
            blank_streak += 1
            if blank_streak <= 1:
                clean_lines.append("")
        else:
            blank_streak = 0
            clean_lines.append(line)

    return title, "\n".join(clean_lines).strip()


def parse_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    """Parse YAML-style frontmatter from markdown file."""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    meta_raw = parts[1]
    body = parts[2].strip()

    metadata: Dict[str, Any] = {}
    current_list_key = None

    for line in meta_raw.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("  - ") and current_list_key:
            val = line[4:].strip().strip('"').strip("'")
            metadata.setdefault(current_list_key, []).append(val)
            continue

        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if not val:
                current_list_key = key
                metadata[key] = []
            else:
                current_list_key = None
                metadata[key] = val

    return metadata, body


def resolve_notebook_dir(notebook_id: str) -> Optional[Path]:
    """Resolve notebook ID or name to its directory on disk (scoped to project vault)."""
    if not RESEARCH_DIR.exists():
        return None

    norm = notebook_id.replace("notebook:", "").strip()
    # Check exact folder match
    direct = (RESEARCH_DIR / norm).resolve()
    if not direct.is_relative_to(RESEARCH_DIR.resolve()):
        return None
    if direct.is_dir():
        return direct

    # Check notebook.json id/name match
    for d in RESEARCH_DIR.iterdir():
        if d.is_dir() and d.resolve().is_relative_to(RESEARCH_DIR.resolve()):
            meta_file = d / "notebook.json"
            if meta_file.exists():
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    if data.get("id") == notebook_id or data.get("name") == norm or d.name == norm:
                        return d
                except Exception:
                    pass

    return None


# ── Action Handlers ──────────────────────────────────────────────────────────


def action_list_notebooks(_input: Dict[str, Any]) -> str:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for d in sorted(RESEARCH_DIR.iterdir()):
        if not d.is_dir():
            continue

        meta_file = d / "notebook.json"
        meta: Dict[str, Any] = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        notes_dir = d / "notes"
        sources_dir = d / "sources"
        note_count = len(list(notes_dir.glob("*.md"))) if notes_dir.exists() else 0
        source_count = len(list(sources_dir.glob("*.md"))) if sources_dir.exists() else 0

        results.append(
            {
                "id": meta.get("id", f"notebook:{d.name}"),
                "name": meta.get("name", d.name),
                "description": meta.get("description", ""),
                "archived": meta.get("archived", False),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "note_count": note_count,
                "source_count": source_count,
            }
        )

    return json.dumps(results)


def action_create_notebook(inp: Dict[str, Any]) -> str:
    name = inp.get("notebook_name") or ""
    if not name.strip():
        return json.dumps({"error": "notebook_name is required"})

    desc = inp.get("notebook_description") or ""
    folder_slug = slugify(name)
    nb_dir = RESEARCH_DIR / folder_slug
    nb_dir.mkdir(parents=True, exist_ok=True)
    (nb_dir / "notes").mkdir(exist_ok=True)
    (nb_dir / "sources").mkdir(exist_ok=True)

    nb_id = f"notebook:{folder_slug}"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
    meta = {
        "id": nb_id,
        "name": name,
        "description": desc,
        "created": now_str,
        "updated": now_str,
        "archived": False,
    }
    (nb_dir / "notebook.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return json.dumps(
        {
            "id": nb_id,
            "name": name,
            "description": desc,
            "created": now_str,
            "updated": now_str,
            "note_count": 0,
            "source_count": 0,
        }
    )


def action_get_notebook(inp: Dict[str, Any]) -> str:
    nb_id = inp.get("notebook_id") or ""
    nb_dir = resolve_notebook_dir(nb_id)
    if not nb_dir:
        return json.dumps({"error": f"Notebook not found: {nb_id}"})

    meta_file = nb_dir / "notebook.json"
    meta = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    notes_dir = nb_dir / "notes"
    sources_dir = nb_dir / "sources"
    note_count = len(list(notes_dir.glob("*.md"))) if notes_dir.exists() else 0
    source_count = len(list(sources_dir.glob("*.md"))) if sources_dir.exists() else 0

    return json.dumps(
        {
            "id": meta.get("id", f"notebook:{nb_dir.name}"),
            "name": meta.get("name", nb_dir.name),
            "description": meta.get("description", ""),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
            "note_count": note_count,
            "source_count": source_count,
        }
    )


def action_add_note(inp: Dict[str, Any]) -> str:
    nb_id = inp.get("notebook_id") or ""
    content = inp.get("content") or ""
    title = inp.get("title") or "Hermes Note"

    if not nb_id:
        return json.dumps({"error": "notebook_id is required"})
    if not content:
        return json.dumps({"error": "content is required"})

    nb_dir = resolve_notebook_dir(nb_id)
    if not nb_dir:
        # Auto-create if missing
        action_create_notebook({"notebook_name": nb_id.replace("notebook:", "")})
        nb_dir = resolve_notebook_dir(nb_id)

    notes_dir = nb_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    note_uuid = f"note:{uuid.uuid4().hex[:20]}"
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    now_full = now.strftime("%Y-%m-%d %H:%M:%S+00:00")
    file_slug = slugify(title)
    filename = f"{date_str}-{file_slug}.md"

    anchors = extract_anchors(content)

    frontmatter = [
        "---",
        f'id: "{note_uuid}"',
        f"title: {json.dumps(title)}",
        f'notebook: "{nb_dir.name}"',
        f'notebook_id: "{nb_id}"',
        'note_type: "human"',
        f'created: "{now_full}"',
        f'updated: "{now_full}"',
    ]
    if anchors:
        frontmatter.append("anchors:")
        for a in anchors:
            frontmatter.append(f'  - "{a}"')
    frontmatter.append("---\n")

    full_doc = "\n".join(frontmatter) + f"\n# {title}\n\n" + content.strip() + "\n"
    (notes_dir / filename).write_text(full_doc, encoding="utf-8")

    return json.dumps(
        {
            "id": note_uuid,
            "title": title,
            "content": content,
            "note_type": "human",
            "notebook_id": nb_id,
            "created": now_full,
            "updated": now_full,
            "file_path": str(notes_dir / filename),
        }
    )


def action_add_source_url(inp: Dict[str, Any]) -> str:
    nb_id = inp.get("notebook_id") or ""
    url = inp.get("url") or ""
    custom_title = inp.get("title") or ""

    if not nb_id or not url:
        return json.dumps({"error": "notebook_id and url are required"})

    nb_dir = resolve_notebook_dir(nb_id)
    if not nb_dir:
        return json.dumps({"error": f"Notebook not found: {nb_id}"})

    sources_dir = nb_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    # Ingest URL
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw_html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch URL: {e}"})

    # Try trafilatura if installed, else clean_html
    title = custom_title
    extracted_text = ""
    try:
        import trafilatura

        extracted_text = trafilatura.extract(raw_html, include_links=True) or ""
        meta = trafilatura.extract_metadata(raw_html)
        if not title and meta and meta.title:
            title = meta.title
    except ImportError:
        pass

    if not extracted_text:
        fallback_title, fallback_text = clean_html(raw_html)
        title = title or fallback_title
        extracted_text = fallback_text

    title = title or "Web Source"
    source_uuid = f"source:{uuid.uuid4().hex[:20]}"
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    now_full = now.strftime("%Y-%m-%d %H:%M:%S+00:00")
    file_slug = slugify(title)
    filename = f"{date_str}-{file_slug}.md"

    frontmatter = [
        "---",
        f'id: "{source_uuid}"',
        f"title: {json.dumps(title)}",
        f'notebook: "{nb_dir.name}"',
        f'notebook_id: "{nb_id}"',
        f'url: "{url}"',
        'file_path: ""',
        f'created: "{now_full}"',
        f'updated: "{now_full}"',
        "---\n",
    ]

    full_doc = (
        "\n".join(frontmatter)
        + f"\n# {title}\n\n**Source URL**: [{url}]({url})\n\n"
        + extracted_text.strip()
        + "\n"
    )
    (sources_dir / filename).write_text(full_doc, encoding="utf-8")

    return json.dumps(
        {
            "id": source_uuid,
            "title": title,
            "url": url,
            "notebook_id": nb_id,
            "status": "completed",
            "file_path": str(sources_dir / filename),
        }
    )


def action_add_source_file(inp: Dict[str, Any]) -> str:
    nb_id = inp.get("notebook_id") or ""
    fpath = inp.get("file_path") or ""
    title = inp.get("title") or Path(fpath).name

    if not nb_id or not fpath:
        return json.dumps({"error": "notebook_id and file_path are required"})

    file_obj = Path(fpath)
    if not file_obj.exists():
        return json.dumps({"error": f"File not found: {fpath}"})

    nb_dir = resolve_notebook_dir(nb_id)
    if not nb_dir:
        return json.dumps({"error": f"Notebook not found: {nb_id}"})

    sources_dir = nb_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    try:
        content = file_obj.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return json.dumps({"error": f"Failed to read file: {e}"})

    source_uuid = f"source:{uuid.uuid4().hex[:20]}"
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    now_full = now.strftime("%Y-%m-%d %H:%M:%S+00:00")
    file_slug = slugify(title)
    filename = f"{date_str}-{file_slug}.md"

    frontmatter = [
        "---",
        f'id: "{source_uuid}"',
        f"title: {json.dumps(title)}",
        f'notebook: "{nb_dir.name}"',
        f'notebook_id: "{nb_id}"',
        'url: ""',
        f'file_path: "{fpath}"',
        f'created: "{now_full}"',
        f'updated: "{now_full}"',
        "---\n",
    ]

    full_doc = (
        "\n".join(frontmatter)
        + f"\n# {title}\n\n**Source File**: `{fpath}`\n\n"
        + content.strip()
        + "\n"
    )
    (sources_dir / filename).write_text(full_doc, encoding="utf-8")

    return json.dumps(
        {
            "id": source_uuid,
            "title": title,
            "file_path": str(sources_dir / filename),
            "notebook_id": nb_id,
            "status": "completed",
        }
    )


def action_poll_source_status(inp: Dict[str, Any]) -> str:
    source_id = inp.get("source_id") or ""
    return json.dumps({"id": source_id, "status": "completed"})


def action_get_source(inp: Dict[str, Any]) -> str:
    source_id = inp.get("source_id") or ""
    if not source_id:
        return json.dumps({"error": "source_id is required"})

    for path in RESEARCH_DIR.rglob("sources/*.md"):
        try:
            raw = path.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(raw)
            if meta.get("id") == source_id or path.stem == source_id:
                return json.dumps(
                    {
                        "id": meta.get("id", source_id),
                        "title": meta.get("title", path.stem),
                        "url": meta.get("url", ""),
                        "file_path": meta.get("file_path", ""),
                        "notebook": meta.get("notebook", path.parent.parent.name),
                        "full_text": body,
                    }
                )
        except Exception:
            pass

    return json.dumps({"error": f"Source not found: {source_id}"})


def action_search(inp: Dict[str, Any]) -> str:
    query = inp.get("query") or ""
    nb_id = inp.get("notebook_id")

    if not query.strip():
        return json.dumps({"error": "query is required"})

    target_dirs = []
    if nb_id:
        single_dir = resolve_notebook_dir(nb_id)
        if single_dir:
            target_dirs = [single_dir]
    else:
        target_dirs = [d for d in RESEARCH_DIR.iterdir() if d.is_dir()]

    terms = [re.escape(t.lower()) for t in query.split() if len(t) > 1]
    if not terms:
        terms = [re.escape(query.lower())]

    results = []

    for d in target_dirs:
        for f in list((d / "notes").glob("*.md")) + list((d / "sources").glob("*.md")):
            try:
                raw = f.read_text(encoding="utf-8")
                meta, body = parse_frontmatter(raw)
                title = meta.get("title", f.stem)
                anchors = " ".join(meta.get("anchors", []))

                score = 0
                snippet = ""
                full_searchable = f"{title}\n{anchors}\n{body}".lower()

                # Scoring
                for term in terms:
                    if term in title.lower():
                        score += 15
                    if term in anchors.lower():
                        score += 10
                    matches = list(re.finditer(term, full_searchable))
                    score += len(matches) * 2

                    if matches and not snippet:
                        idx = matches[0].start()
                        start = max(0, idx - 80)
                        end = min(len(full_searchable), idx + 140)
                        snippet = full_searchable[start:end].replace("\n", " ").strip()

                if score > 0:
                    results.append(
                        {
                            "id": meta.get("id", f.stem),
                            "title": title,
                            "type": "note" if "notes" in str(f) else "source",
                            "notebook": d.name,
                            "notebook_id": meta.get("notebook_id", f"notebook:{d.name}"),
                            "score": score,
                            "snippet": f"...{snippet}..." if snippet else body[:180],
                            "file_path": str(f),
                        }
                    )
            except Exception:
                pass

    results.sort(key=lambda x: x["score"], reverse=True)
    return json.dumps(results[:15])


def action_ask_notebook(inp: Dict[str, Any]) -> str:
    question = inp.get("query") or inp.get("question") or ""
    nb_id = inp.get("notebook_id")

    if not question:
        return json.dumps({"error": "query is required"})

    # 1. Search for context
    search_json = action_search({"query": question, "notebook_id": nb_id})
    search_results = json.loads(search_json)

    if not search_results or "error" in search_results:
        context_block = "No specific research context found in notebook."
        sources_used = []
    else:
        context_parts = []
        sources_used = []
        for res in search_results[:5]:
            try:
                fpath = Path(res["file_path"])
                raw = fpath.read_text(encoding="utf-8")
                _, body = parse_frontmatter(raw)
                context_parts.append(f"### {res['title']} ({res['type']})\n{body[:2500]}")
                sources_used.append(res["title"])
            except Exception:
                pass
        context_block = "\n\n---\n\n".join(context_parts)

    # 2. Synthesize with LLM gateway
    system_prompt = (
        "You are an empirical research synthesis assistant. "
        "Answer the user's question accurately using the research context provided. "
        "Cite relevant source titles and preserve any @symbol: anchors when discussing code."
    )
    user_prompt = f"RESEARCH CONTEXT:\n{context_block}\n\nQUESTION: {question}"

    payload = {
        "model": inp.get("answer_model") or "default",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }

    try:
        req = urllib.request.Request(
            f"{LLM_GATEWAY_URL}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
            },
            data=json.dumps(payload).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            answer = data["choices"][0]["message"]["content"]
    except Exception as e:
        answer = f"(Failed to call LLM gateway for synthesis: {e})\n\nTop context found:\n{context_block[:1000]}"

    return json.dumps(
        {
            "answer": answer,
            "sources": sources_used,
            "notebook_id": nb_id,
        }
    )


def handle(args, **kwargs) -> str:
    """Hermes tool handler — dispatch an action, always returning a JSON string."""
    if not isinstance(args, dict):
        return json.dumps({"error": "Invalid arguments"})

    action = args.get("action")
    dispatch = {
        "list_notebooks": action_list_notebooks,
        "create_notebook": action_create_notebook,
        "get_notebook": action_get_notebook,
        "add_note": action_add_note,
        "add_source_url": action_add_source_url,
        "add_source_file": action_add_source_file,
        "poll_source_status": action_poll_source_status,
        "get_source": action_get_source,
        "search": action_search,
        "ask_notebook": action_ask_notebook,
    }

    handler = dispatch.get(action)
    if not handler:
        return json.dumps(
            {"error": f"Unknown action: {action}", "allowed": list(dispatch.keys())}
        )

    try:
        return handler(args)
    except Exception as e:
        return json.dumps({"error": f"Execution error in {action}: {e}"})
