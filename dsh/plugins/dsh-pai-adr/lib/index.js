/**
 * dsh-pai-adr — DSH plugin for Architecture Decision Records (ADRs).
 *
 * Provides: pai_adr_ops tool with actions:
 * - list_adrs: list ADRs in .planning/research/
 * - create_adr: create new ADR markdown file
 * - check_drift: detect code symbol drift against ADRs
 *
 * ADRs are stored as markdown in .planning/research/ (per-project).
 * check_drift compares code symbols against ADR references to detect drift.
 */
import z from "@deepseek-ai/schemastery";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { existsSync, mkdirSync, readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { createHash } from "node:crypto";

export const name = "pai-adr";
export const inject = ["tools"];

export const Config = z.object({
  enabled: z.boolean().default(true),
});

function computeFileHash(filePath) {
  try {
    if (!existsSync(filePath)) return null;
    const content = readFileSync(filePath);
    return createHash("sha256").update(content).digest("hex").slice(0, 16);
  } catch {
    return null;
  }
}

function resolvePlanningDir(executionDir) {
  if (executionDir) {
    const planning = join(executionDir, ".planning");
    mkdirSync(planning, { recursive: true });
    return planning;
  }
  const ws = process.env.WORKSPACE_DIR || "/opt/data/workspace";
  const planning = join(ws, ".planning");
  if (existsSync(planning)) return planning;
  return null;
}

export function apply(ctx, config) {
  const cfg = { enabled: true, ...(config ?? {}) };
  if (!cfg.enabled) return;

  const adrTool = defineTool({
    name: "pai_adr_ops",
    description: "Living ADR creation & code symbol drift detection.",
    parameters: {
      action: {
        type: "string",
        required: true,
        enum: ["list_adrs", "create_adr", "check_drift"],
        description: "ADR operation to perform"
      },
      execution_dir: { type: "string", description: "Project execution directory (holds .planning/research/)" },
      // create_adr
      adr_number: { type: "string", description: "ADR number (create_adr)", default: "001" },
      title: { type: "string", description: "ADR title (create_adr)" },
      context: { type: "string", description: "Context section content (create_adr)" },
      decision: { type: "string", description: "Decision section content (create_adr)" },
      symbols: {
        type: "array",
        items: { type: "string" },
        description: "Symbols to track (create_adr), each formatted @symbol:<file>:<symbol>"
      },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }]
    },
    execute(args) {
      const action = args.action;
      try {
        if (action === "list_adrs") {
          const planning = resolvePlanningDir(args.execution_dir);
          if (!planning) return { adrs: [] };
          const researchDir = join(planning, "research");
          if (!existsSync(researchDir)) return { adrs: [] };
          const files = readdirSync(researchDir).filter(f => f.endsWith(".md"));
          return { adrs: files };
        }
        if (action === "create_adr") {
          const adrNumber = args.adr_number || "001";
          const title = args.title || "Untitled ADR";
          const context = args.context || "";
          const decision = args.decision || "";
          const symbols = args.symbols || [];
          const planning = resolvePlanningDir(args.execution_dir);
          if (!planning) return { error: "No .planning directory found" };
          const researchDir = join(planning, "research");
          mkdirSync(researchDir, { recursive: true });
          
          const symbolEntries = symbols.map(sym => {
            const match = sym.match(/@symbol:(.+):(.+)/);
            if (!match) return null;
            const [, filePath, symbolName] = match;
            const hash = computeFileHash(join(process.env.WORKSPACE_DIR || "/opt/data/workspace", filePath));
            return { symbol: sym, file: filePath, name: symbolName, hash };
          }).filter(Boolean);
          
          const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
          const filename = `ADR-${adrNumber}-${slug}.md`;
          const filepath = join(researchDir, filename);
          
          const content = `# ${title}\n\n## Context\n${context}\n\n## Decision\n${decision}\n\n## Symbols\n${symbolEntries.map(s => `- \`${s.symbol}\` — ${s.file}:${s.name} (${s.hash || "NOT FOUND"})`).join("\n")}\n`;
          
          writeFileSync(filepath, content);
          return { success: true, adr_path: filepath, adr_number: adrNumber };
        }
        if (action === "check_drift") {
          const ws = process.env.WORKSPACE_DIR || "/opt/data/workspace";
          const planning = resolvePlanningDir(args.execution_dir);
          if (!planning) return { drift: [] };
          const researchDir = join(planning, "research");
          if (!existsSync(researchDir)) return { drift: [] };
          const files = readdirSync(researchDir).filter(f => f.endsWith(".md"));
          const drift = [];
          for (const f of files) {
            const content = readFileSync(join(researchDir, f), "utf8");
            const symMatches = content.match(/@symbol:[^:\s]+:[^\s]+/g) || [];
            for (const sym of symMatches) {
              const match = sym.match(/@symbol:(.+):(.+)/);
              if (!match) continue;
              const [, filePath, symbolName] = match;
              const currentHash = computeFileHash(join(ws, filePath));
              const escapedSym = sym.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
              const storedMatch = content.match(new RegExp('`' + escapedSym + '`.*?\\(([^)]+)\\)'));
              const storedHash = storedMatch ? storedMatch[1] : null;
              if (currentHash && storedHash && currentHash !== storedHash) {
                drift.push({ adr: f, symbol: sym, stored: storedHash, current: currentHash });
              }
            }
          }
          return { drift };
        }
        return { error: `Unsupported action: ${action}` };
      } catch (err) {
        return { error: err.message };
      }
    },
  });

  ctx.tools.register(adrTool);
}

export default { Config, apply, inject, name };
