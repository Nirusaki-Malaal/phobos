/**
 * dsh-pai-notebook — DSH plugin for Research Brain notebook operations.
 *
 * Provides: pai_notebook_ops tool with actions:
 * - list_notebooks, create_notebook, get_notebook
 * - add_note, add_source_url, add_source_file
 * - poll_source_status, get_source
 * - search, ask_notebook
 *
 * Manages a per-project research vault in .project/research/ (EXECUTION_DIR/.project/research/).
 * Each notebook is a markdown file with optional sources. Use ask_notebook to query with LLM synthesis.
 * Sources can be URLs, files, or inline text. Notes support categories and tags for organization.
 */

import z from "@deepseek-ai/schemastery";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { existsSync, mkdirSync, readFileSync, writeFileSync, readdirSync, statSync, globSync } from "node:fs";
import { join, relative, dirname } from "node:path";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

export const name = "pai-notebook";
export const inject = ["tools"];

export const Config = z.object({
  enabled: z.boolean().default(true),
});

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function getResearchDir() {
  const execDir = process.env.EXECUTION_DIR;
  if (execDir) {
    return join(execDir, ".project", "research");
  }
  // Fallback for backwards compatibility (should not be used in new architecture)
  return process.env.RESEARCH_DIR ||
         process.env.WIKI_PATH ||
         join(process.env.WORKSPACE_DIR || "/opt/data/workspace", "research");
}

function slugify(text) {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/[-\s]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60) || "untitled";
}

function extractAnchors(text) {
  if (!text) return [];
  const raw = text.match(/@symbol:[^\s\)\`\"\']+/g) || [];
  return [...new Set(raw.map(r => r.replace(/[.,;:)!?'"]+$/, "")))].sort();
}

function parseFrontmatter(content) {
  if (!content.startsWith("---")) return { meta: {}, body: content };
  const parts = content.split("---", 3);
  if (parts.length < 3) return { meta: {}, body: content };
  
  const meta = {};
  let currentListKey = null;
  for (const line of parts[1].split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    if (trimmed.startsWith("- ") && currentListKey) {
      const val = trimmed.slice(2).replace(/^["']|["']$/g, "");
      meta[currentListKey] = [...(meta[currentListKey] || []), val];
      continue;
    }
    const idx = trimmed.indexOf(":");
    if (idx > 0) {
      const key = trimmed.slice(0, idx).trim();
      const val = trimmed.slice(idx + 1).trim().replace(/^["']|["']$/g, "");
      if (!val) {
        currentListKey = key;
        meta[key] = [];
      } else {
        currentListKey = null;
        meta[key] = val;
      }
    }
  }
  return { meta, body: parts[2].trim() };
}

function resolveNotebookDir(researchDir, notebookId) {
  if (!existsSync(researchDir)) return null;
  const norm = notebookId.replace(/^notebook:/, "").trim();
  
  // Direct match
  const direct = join(researchDir, norm);
  if (existsSync(direct) && statSync(direct).isDirectory()) return direct;
  
  // Check notebook.json
  for (const entry of readdirSync(researchDir)) {
    const full = join(researchDir, entry);
    if (!statSync(full).isDirectory()) continue;
    const metaFile = join(full, "notebook.json");
    if (existsSync(metaFile)) {
      try {
        const data = JSON.parse(readFileSync(metaFile, "utf8"));
        if (data.id === notebookId || data.name === norm || entry === norm) {
          return full;
        }
      } catch {}
    }
  }
  return null;
}

function cleanHtml(html) {
  // Extract title
  const titleMatch = html.match(/<title[^>]*>(.*?)<\/title>/is);
  let title = titleMatch ? titleMatch[1].replace(/&[^;]+;/g, " ").trim() : "Extracted Source";
  title = title.replace(/\s+/g, " ");
  
  // Strip scripts, styles, nav, footer
  let text = html
    .replace(/<script[^>]*>.*?<\/script>/gis, "")
    .replace(/<style[^>]*>.*?<\/style>/gis, "")
    .replace(/<nav[^>]*>.*?<\/nav>/gis, "")
    .replace(/<footer[^>]*>.*?<\/footer>/gis, "");
  
  // Headings to markdown
  text = text.replace(/<h1[^>]*>(.*?)<\/h1>/gis, "\n# $1\n");
  text = text.replace(/<h2[^>]*>(.*?)<\/h2>/gis, "\n## $1\n");
  text = text.replace(/<h3[^>]*>(.*?)<\/h3>/gis, "\n### $1\n");
  text = text.replace(/<p[^>]*>(.*?)<\/p>/gis, "\n$1\n");
  text = text.replace(/<li[^>]*>(.*?)<\/li>/gis, "\n- $1");
  
  // Strip remaining tags
  text = text.replace(/<[^>]+>/g, " ");
  text = text.replace(/&[^;]+;/g, " ");
  
  // Normalize whitespace
  const lines = text.split("\n").map(l => l.trim());
  const clean = [];
  let blankStreak = 0;
  for (const line of lines) {
    if (!line) {
      blankStreak++;
      if (blankStreak <= 1) clean.push("");
    } else {
      blankStreak = 0;
      clean.push(line);
    }
  }
  return { title, text: clean.join("\n").trim() };
}

function nowUtc() {
  return new Date().toISOString().replace("T", " ").replace(/\.\d+Z/, "+00:00");
}

function dateStr() {
  return new Date().toISOString().slice(0, 10);
}

export function apply(ctx, config) {
  const cfg = { enabled: true, ...(config ?? {}) };
  if (!cfg.enabled) return;

  const researchDir = getResearchDir();
  mkdirSync(researchDir, { recursive: true });

  // LLM Gateway config
  const LLM_GATEWAY_URL = process.env.LLM_GATEWAY_URL || 
    process.env.LLM_GATEWAY_BASE_URL || 
    "http://llm-gateway:4000/v1";
  const LITELLM_MASTER_KEY = process.env.LITELLM_MASTER_KEY || "not-needed";

  const notebookTool = defineTool({
    name: "pai_notebook_ops",
    description: "Research Brain vault: notes, sources, search, synthesis. Backed by Markdown files in research/. Root-level wiki (SCHEMA.md, index.md, log.md) at research/ root.",
    parameters: {
      action: {
        type: "string",
        required: true,
        enum: [
          "list_notebooks", "create_notebook", "get_notebook",
          "add_note", "add_source_url", "add_source_file",
          "poll_source_status", "get_source",
          "search", "ask_notebook"
        ],
        description: "Operation to perform"
      },
      notebook_id: { type: "string", description: "Target notebook ID (e.g. 'notebook:abc123')" },
      notebook_name: { type: "string", description: "Notebook name for create_notebook" },
      notebook_description: { type: "string", description: "Optional description for create_notebook" },
      query: { type: "string", description: "Search query or question for ask_notebook" },
      title: { type: "string", description: "Title for note or source" },
      content: { type: "string", description: "Text content for add_note" },
      url: { type: "string", description: "URL to ingest as source" },
      source_id: { type: "string", description: "Source ID for poll_source_status/get_source" },
      file_path: { type: "string", description: "Path to file inside container for add_source_file" },
      answer_model: { type: "string", description: "Answer model override for ask_notebook" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }]
    },
    async execute(args) {
      const action = args.action;
      try {
        mkdirSync(researchDir, { recursive: true });
        
        if (action === "list_notebooks") {
          const items = readdirSync(researchDir)
            .filter(f => statSync(join(researchDir, f)).isDirectory())
            .map(d => {
              const metaFile = join(researchDir, d, "notebook.json");
              let meta = {};
              if (existsSync(metaFile)) {
                try { meta = JSON.parse(readFileSync(metaFile, "utf8")); } catch {}
              }
              const notesDir = join(researchDir, d, "notes");
              const sourcesDir = join(researchDir, d, "sources");
              const noteCount = existsSync(notesDir) ? readdirSync(notesDir).filter(f => f.endsWith(".md")).length : 0;
              const sourceCount = existsSync(sourcesDir) ? readdirSync(sourcesDir).filter(f => f.endsWith(".md")).length : 0;
              return {
                id: meta.id || `notebook:${d}`,
                name: meta.name || d,
                description: meta.description || "",
                archived: meta.archived || false,
                created: meta.created || "",
                updated: meta.updated || "",
                note_count: noteCount,
                source_count: sourceCount,
              };
            });
          return { notebooks: items };
        }

        if (action === "create_notebook") {
          const name = args.notebook_name || "";
          if (!name.trim()) return { error: "notebook_name is required" };
          const desc = args.notebook_description || "";
          const folderSlug = slugify(name);
          const nbDir = join(researchDir, folderSlug);
          mkdirSync(nbDir, { recursive: true });
          mkdirSync(join(nbDir, "notes"), { recursive: true });
          mkdirSync(join(nbDir, "sources"), { recursive: true });
          
          const nbId = `notebook:${folderSlug}`;
          const now = nowUtc();
          const meta = {
            id: nbId,
            name: name,
            description: desc,
            created: now,
            updated: now,
            archived: false,
          };
          writeFileSync(join(nbDir, "notebook.json"), JSON.stringify(meta, null, 2));
          
          return { ...meta, note_count: 0, source_count: 0 };
        }

        if (action === "get_notebook") {
          const nbId = args.notebook_id || "";
          if (!nbId) return { error: "notebook_id is required" };
          const nbDir = resolveNotebookDir(researchDir, nbId);
          if (!nbDir) return { error: `Notebook not found: ${nbId}` };
          
          const metaFile = join(nbDir, "notebook.json");
          let meta = {};
          if (existsSync(metaFile)) {
            try { meta = JSON.parse(readFileSync(metaFile, "utf8")); } catch {}
          }
          const notesDir = join(nbDir, "notes");
          const sourcesDir = join(nbDir, "sources");
          const noteCount = existsSync(notesDir) ? readdirSync(notesDir).filter(f => f.endsWith(".md")).length : 0;
          const sourceCount = existsSync(sourcesDir) ? readdirSync(sourcesDir).filter(f => f.endsWith(".md")).length : 0;
          
          return {
            id: meta.id || `notebook:${nbDir.split("/").pop()}`,
            name: meta.name || nbDir.split("/").pop(),
            description: meta.description || "",
            created: meta.created || "",
            updated: meta.updated || "",
            note_count: noteCount,
            source_count: sourceCount,
          };
        }

        if (action === "add_note") {
          const nbId = args.notebook_id || "";
          const content = args.content || "";
          const title = args.title || "Hermes Note";
          
          if (!nbId) return { error: "notebook_id is required" };
          if (!content) return { error: "content is required" };
          
          let nbDir = resolveNotebookDir(researchDir, nbId);
          if (!nbDir) {
            // Auto-create
            const createResult = await notebookTool.execute({ action: "create_notebook", notebook_name: nbId.replace(/^notebook:/, "") });
            if (createResult.error) return createResult;
            nbDir = resolveNotebookDir(researchDir, nbId);
          }
          
          const notesDir = join(nbDir, "notes");
          mkdirSync(notesDir, { recursive: true });
          
          const noteUuid = `note:${randomUUID().slice(0, 20)}`;
          const now = nowUtc();
          const fileSlug = slugify(title);
          const filename = `${dateStr()}-${fileSlug}.md`;
          
          const anchors = extractAnchors(content);
          const fmLines = [
            "---",
            `id: "${noteUuid}"`,
            `title: ${JSON.stringify(title)}`,
            `notebook: "${nbDir.split("/").pop()}"`,
            `notebook_id: "${nbId}"`,
            `note_type: "human"`,
            `created: "${now}"`,
            `updated: "${now}"`,
          ];
          if (anchors.length) {
            fmLines.push("anchors:");
            for (const a of anchors) fmLines.push(`  - "${a}"`);
          }
          fmLines.push("---\n");
          
          const fullDoc = fmLines.join("\n") + `\n# ${title}\n\n${content.trim()}\n`;
          writeFileSync(join(notesDir, filename), fullDoc);
          
          return {
            id: noteUuid,
            title: title,
            content: content,
            note_type: "human",
            notebook_id: nbId,
            created: now,
            updated: now,
            file_path: join(notesDir, filename),
          };
        }

        if (action === "add_source_url") {
          const nbId = args.notebook_id || "";
          const url = args.url || "";
          const customTitle = args.title || "";
          
          if (!nbId || !url) return { error: "notebook_id and url are required" };
          
          const nbDir = resolveNotebookDir(researchDir, nbId);
          if (!nbDir) return { error: `Notebook not found: ${nbId}` };
          
          const sourcesDir = join(nbDir, "sources");
          mkdirSync(sourcesDir, { recursive: true });
          
          // Fetch URL
          let rawHtml;
          try {
            const response = await fetch(url, {
              headers: {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
              },
              signal: AbortSignal.timeout(20000),
            });
            rawHtml = await response.text();
          } catch (e) {
            return { error: `Failed to fetch URL: ${e.message}` };
          }
          
          // Try trafilatura via dynamic import (optional)
          let extractedText = "";
          let title = customTitle;
          try {
            // eslint-disable-next-line @typescript-eslint/no-require-imports
            const trafilatura = await import("trafilatura");
            extractedText = trafilatura.extract(rawHtml, { include_links: true }) || "";
            const meta = trafilatura.extract_metadata(rawHtml);
            if (!title && meta?.title) title = meta.title;
          } catch {
            // Fallback to cleanHtml
          }
          
          if (!extractedText) {
            const cleaned = cleanHtml(rawHtml);
            title = title || cleaned.title;
            extractedText = cleaned.text;
          }
          
          title = title || "Web Source";
          const sourceUuid = `source:${randomUUID().slice(0, 20)}`;
          const now = nowUtc();
          const fileSlug = slugify(title);
          const filename = `${dateStr()}-${fileSlug}.md`;
          
          const fmLines = [
            "---",
            `id: "${sourceUuid}"`,
            `title: ${JSON.stringify(title)}`,
            `notebook: "${nbDir.split("/").pop()}"`,
            `notebook_id: "${nbId}"`,
            `url: "${url}"`,
            `file_path: ""`,
            `created: "${now}"`,
            `updated: "${now}"`,
            "---\n",
          ];
          
          const fullDoc = fmLines.join("\n") + `\n# ${title}\n\n**Source URL**: [${url}](${url})\n\n${extractedText.trim()}\n`;
          writeFileSync(join(sourcesDir, filename), fullDoc);
          
          return {
            id: sourceUuid,
            title: title,
            url: url,
            notebook_id: nbId,
            status: "completed",
            file_path: join(sourcesDir, filename),
          };
        }

        if (action === "add_source_file") {
          const nbId = args.notebook_id || "";
          const fpath = args.file_path || "";
          const title = args.title || fpath.split("/").pop() || "File Source";
          
          if (!nbId || !fpath) return { error: "notebook_id and file_path are required" };
          
          if (!existsSync(fpath)) return { error: `File not found: ${fpath}` };
          
          const nbDir = resolveNotebookDir(researchDir, nbId);
          if (!nbDir) return { error: `Notebook not found: ${nbId}` };
          
          const sourcesDir = join(nbDir, "sources");
          mkdirSync(sourcesDir, { recursive: true });
          
          let content;
          try {
            content = readFileSync(fpath, "utf8");
          } catch (e) {
            return { error: `Failed to read file: ${e.message}` };
          }
          
          const sourceUuid = `source:${randomUUID().slice(0, 20)}`;
          const now = nowUtc();
          const fileSlug = slugify(title);
          const filename = `${dateStr()}-${fileSlug}.md`;
          
          const fmLines = [
            "---",
            `id: "${sourceUuid}"`,
            `title: ${JSON.stringify(title)}`,
            `notebook: "${nbDir.split("/").pop()}"`,
            `notebook_id: "${nbId}"`,
            `url: ""`,
            `file_path: "${fpath}"`,
            `created: "${now}"`,
            `updated: "${now}"`,
            "---\n",
          ];
          
          const fullDoc = fmLines.join("\n") + `\n# ${title}\n\n**Source File**: \`${fpath}\`\n\n${content.trim()}\n`;
          writeFileSync(join(sourcesDir, filename), fullDoc);
          
          return {
            id: sourceUuid,
            title: title,
            file_path: join(sourcesDir, filename),
            notebook_id: nbId,
            status: "completed",
          };
        }

        if (action === "poll_source_status") {
          return { id: args.source_id || "", status: "completed" };
        }

        if (action === "get_source") {
          const sourceId = args.source_id || "";
          if (!sourceId) return { error: "source_id is required" };
          
          const pattern = join(researchDir, "**", "sources", "*.md");
          for (const filepath of globSync(pattern)) {
            try {
              const raw = readFileSync(filepath, "utf8");
              const { meta, body } = parseFrontmatter(raw);
              if (meta.id === sourceId || filepath.endsWith(`${sourceId}.md`)) {
                return {
                  id: meta.id || sourceId,
                  title: meta.title || filepath.split("/").pop(),
                  url: meta.url || "",
                  file_path: meta.file_path || "",
                  notebook: meta.notebook || "",
                  full_text: body,
                };
              }
            } catch {}
          }
          return { error: `Source not found: ${sourceId}` };
        }

        if (action === "search") {
          const query = args.query || "";
          const nbId = args.notebook_id;
          
          if (!query.trim()) return { error: "query is required" };
          
          let targetDirs = [];
          if (nbId) {
            const single = resolveNotebookDir(researchDir, nbId);
            if (single) targetDirs = [single];
          } else {
            targetDirs = readdirSync(researchDir)
              .map(d => join(researchDir, d))
              .filter(d => statSync(d).isDirectory());
          }
          
          const terms = query.toLowerCase().split(/\s+/).filter(t => t.length > 1).map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
          if (terms.length === 0) terms.push(query.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
          
          const results = [];
          
          for (const d of targetDirs) {
            for (const subdir of ["notes", "sources"]) {
              const fullDir = join(d, subdir);
              if (!existsSync(fullDir)) continue;
              for (const file of readdirSync(fullDir)) {
                if (!file.endsWith(".md")) continue;
                try {
                  const filepath = join(fullDir, file);
                  const raw = readFileSync(filepath, "utf8");
                  const { meta, body } = parseFrontmatter(raw);
                  const title = meta.title || file;
                  const anchors = (meta.anchors || []).join(" ");
                  
                  let score = 0;
                  let snippet = "";
                  const searchable = `${title}\n${anchors}\n${body}`.toLowerCase();
                  
                  for (const term of terms) {
                    if (title.toLowerCase().includes(term)) score += 15;
                    if (anchors.toLowerCase().includes(term)) score += 10;
                    const regex = new RegExp(term, "g");
                    const matches = [...searchable.matchAll(regex)];
                    score += matches.length * 2;
                    if (matches.length && !snippet) {
                      const idx = matches[0].index || 0;
                      snippet = searchable.slice(Math.max(0, idx - 80), idx + 140).replace(/\n/g, " ").trim();
                    }
                  }
                  
                  if (score > 0) {
                    results.push({
                      id: meta.id || file,
                      title: title,
                      type: subdir === "notes" ? "note" : "source",
                      notebook: d.split("/").pop(),
                      notebook_id: meta.notebook_id || `notebook:${d.split("/").pop()}`,
                      score: score,
                      snippet: snippet ? `...${snippet}...` : body.slice(0, 180),
                      file_path: filepath,
                    });
                  }
                } catch {}
              }
            }
          }
          
          results.sort((a, b) => b.score - a.score);
          return { results: results.slice(0, 15) };
        }

        if (action === "ask_notebook") {
          const question = args.query || args.question || "";
          const nbId = args.notebook_id;
          
          if (!question) return { error: "query is required" };
          
          // Search for context
          const searchResult = await notebookTool.execute({ action: "search", query: question, notebook_id: nbId });
          const searchResults = searchResult.results || [];
          
          let contextBlock = "No specific research context found in notebook.";
          const sourcesUsed = [];
          
          if (searchResults.length > 0) {
            const contextParts = [];
            for (const res of searchResults.slice(0, 5)) {
              try {
                const raw = readFileSync(res.file_path, "utf8");
                const { body } = parseFrontmatter(raw);
                contextParts.push(`### ${res.title} (${res.type})\n${body.slice(0, 2500)}`);
                sourcesUsed.push(res.title);
              } catch {}
            }
            contextBlock = contextParts.join("\n\n---\n\n");
          }
          
          // Synthesize with LLM gateway
          const systemPrompt = 
            "You are an empirical research synthesis assistant. " +
            "Answer the user's question accurately using the research context provided. " +
            "Cite relevant source titles and preserve any @symbol: anchors when discussing code.";
          const userPrompt = `RESEARCH CONTEXT:\n${contextBlock}\n\nQUESTION: ${question}`;
          
          const payload = {
            model: args.answer_model || "default",
            messages: [
              { role: "system", content: systemPrompt },
              { role: "user", content: userPrompt },
            ],
            temperature: 0.2,
          };
          
          let answer;
          try {
            const response = await fetch(`${LLM_GATEWAY_URL}/chat/completions`, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${LITELLM_MASTER_KEY}`,
              },
              body: JSON.stringify(payload),
              signal: AbortSignal.timeout(60000),
            });
            const data = await response.json();
            answer = data.choices?.[0]?.message?.content || "(No response from LLM gateway)";
          } catch (e) {
            answer = `(Failed to call LLM gateway for synthesis: ${e.message})\n\nTop context found:\n${contextBlock.slice(0, 1000)}`;
          }
          
          return {
            answer: answer,
            sources: sourcesUsed,
            notebook_id: nbId,
          };
        }

        return { error: `Unsupported action: ${action}` };
      } catch (err) {
        return { error: `${action}: ${err.message}` };
      }
    },
  });

  ctx.tools.register(notebookTool);
}

export default { Config, apply, inject, name };