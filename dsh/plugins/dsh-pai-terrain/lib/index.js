/**
 * dsh-pai-terrain — DSH plugin for code intelligence via Terrain service.
 *
 * Provides: pai_terrain_ops tool with actions:
 * - index, refresh, search, read, overview, projects, unregister, source, init, ask
 *
 * Connects to Terrain service (TERRAIN_URL, default http://terrain:7878).
 * Indexing (index, refresh) is free — no LLM calls. Only init/ask use LLM (requires TERRAIN_ALLOW_LLM=1).
 * Each scan writes .terrain/ inside the repo (gitignored, per-host).
 * Paths are resolved inside WORKSPACE_DIR (/opt/data/workspace).
 */

import z from "@deepseek-ai/schemastery";
import { defineTool } from "@deepseek-ai/dsh-tools";

export const name = "pai-terrain";
export const inject = ["tools"];

export const Config = z.object({
  enabled: z.boolean().default(true),
});

const SERVICE = process.env.TERRAIN_URL || "http://terrain:7878";
const TOKEN = process.env.TERRAIN_TOKEN || "";
const TIMEOUT = parseInt(process.env.TERRAIN_TIMEOUT || "600", 10);
const WORKSPACE = process.env.TERRAIN_WORKSPACE || "/opt/data/workspace";

const ALLOWED_ACTIONS = [
  "index", "refresh", "search", "read", "overview", 
  "projects", "unregister", "source", "init", "ask"
];
const LLM_ACTIONS = ["init", "ask"];

function headers() {
  const h = { "content-type": "application/json" };
  if (TOKEN) h["x-terrain-token"] = TOKEN;
  return h;
}

async function post(action, params) {
  const body = JSON.stringify({ action, params });
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT * 1000);
  
  try {
    const response = await fetch(`${SERVICE}/call`, {
      method: "POST",
      headers: headers(),
      body,
      signal: controller.signal,
    });
    clearTimeout(timeout);
    const text = await response.text();
    if (!response.ok) {
      let detail = text;
      try { detail = JSON.parse(text).error || detail; } catch {}
      return [1, JSON.stringify({ error: detail || `HTTP ${response.status}` })];
    }
    return [0, text];
  } catch (e) {
    clearTimeout(timeout);
    if (e.name === "AbortError") {
      return [1, JSON.stringify({ error: `Request timeout after ${TIMEOUT}s` })];
    }
    return [1, JSON.stringify({ 
      error: `Cannot reach terrain service at ${SERVICE}: ${e.message}. Start with 'make terrain-up' or fall back to grep.` 
    })];
  }
}

async function checkAvailable() {
  try {
    const response = await fetch(`${SERVICE}/healthz`, { 
      headers: headers(), 
      signal: AbortSignal.timeout(3000) 
    });
    return response.ok;
  } catch {
    return false;
  }
}

export function apply(ctx, config) {
  const cfg = { enabled: true, ...(config ?? {}) };
  if (!cfg.enabled) return;

  const terrainTool = defineTool({
    name: "pai_terrain_ops",
    description: "Code intelligence over the Terrain index service. Terrain scans a project repo into .terrain/ (Markdown knowledge docs + agent context); this tool triggers those scans and reads the result. Check action='projects' first: if the repo is indexed, this is far more precise than grep; if it is not, fall back to grep + targeted reads. Indexing ('index', 'refresh') makes NO LLM call and costs nothing. Only 'ask' and 'init' use an LLM — Terrain's keyless Zen models, not the llm-gateway — and the service refuses them unless TERRAIN_ALLOW_LLM=1. Write scope: each scan writes .terrain/ inside the repo you pass. Paths are resolved inside /opt/data/workspace; other paths are rejected. Allowed actions: index, refresh, search, read, overview, projects, unregister, source, init, ask.",
    parameters: {
      action: {
        type: "string",
        required: true,
        enum: ALLOWED_ACTIONS,
        description: "Operation to perform"
      },
      path: {
        type: "string",
        description: "Repo directory to index, relative to /opt/data/workspace or absolute within it (e.g. 'github.com/ayedaemon/pai-stack'). Required for 'index', 'refresh', 'init'. Optional for 'source'."
      },
      slug: {
        type: "string",
        description: "Registry name for this project. Defaults to the directory name."
      },
      query: {
        type: "string",
        description: "Search string ('search') or question ('ask')."
      },
      project: {
        type: "string",
        description: "Project slug ('search', 'overview', 'ask', 'source', 'unregister')."
      },
      file: {
        type: "string",
        description: "Repo-relative file path ('source' only)."
      },
      limit: {
        type: "integer",
        description: "Max search results ('search' only). Default: 20.",
        default: 20,
      },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }]
    },
    async execute(args) {
      const action = args.action;
      const start = Date.now();
      
      try {
        if (!ALLOWED_ACTIONS.includes(action)) {
          return { error: `Unknown action: ${action}. Allowed: ${ALLOWED_ACTIONS.join(", ")}` };
        }
        
        if (["index", "refresh", "init"].includes(action) && !args.path) {
          return { error: `path is required for '${action}'. Pass the repo directory, e.g. path='github.com/ayedaemon/pai-stack' (resolved inside ${WORKSPACE}).` };
        }
        if (["search", "ask"].includes(action) && !args.query) {
          return { error: `query is required for '${action}'` };
        }
        if (action === "overview" && !args.project) {
          return { error: "project is required for 'overview'" };
        }
        
        const params = {};
        for (const key of ["path", "slug", "query", "project", "file", "limit"]) {
          if (args[key] !== undefined) params[key] = args[key];
        }

        // Validate limit if provided
        if (params.limit !== undefined && (params.limit < 1 || params.limit > 200)) {
          return { error: "limit must be between 1 and 200" };
        }
        
        const [code, out] = await post(action, params);
        
        let parsed;
        try { parsed = JSON.parse(out); } catch { parsed = { raw: out }; }
        
        if (parsed && typeof parsed === "object") {
          parsed._duration_ms = Date.now() - start;
          parsed._action = action;
          if (code === 0 && ["index", "refresh", "init"].includes(action)) {
            parsed._hint = `'.terrain/' now exists inside ${params.path}. It is gitignored; re-run 'refresh' after meaningful code changes.`;
          }
        }
        return parsed;
      } catch (err) {
        return { error: `${action}: ${err.message}` };
      }
    },
  });

  // Also expose health check as a provided service
  ctx.provide("paiTerrainHealth", {
    async check() {
      return await checkAvailable();
    },
    getServiceUrl() {
      return SERVICE;
    }
  });

  ctx.tools.register(terrainTool);
}

export default { Config, apply, inject, name };