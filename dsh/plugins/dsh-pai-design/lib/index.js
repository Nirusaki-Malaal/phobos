/**
 * dsh-pai-design — DSH plugin for OpenDesign integration.
 *
 * Provides: paiOpsDesign tool with actions:
 * - health, list_projects, get_project, create_project, import_folder, get_status
 *
 * Connects to OpenDesign API (OD_HOST, default http://open-design:7456) using OD_API_TOKEN.
 * Use for design project management, folder imports, and design collaboration workflows.
 */
import z from "@deepseek-ai/schemastery";
import { defineTool } from "@deepseek-ai/dsh-tools";

export const name = "pai-design";
export const inject = ["tools"];

export const Config = z.object({
  enabled: z.boolean().default(true),
});

export function apply(ctx, config) {
  const cfg = { enabled: true, ...(config ?? {}) };
  if (!cfg.enabled) return;

  const designTool = defineTool({
    name: "pai_design_ops",
    description: "OpenDesign visual generation / mockups bridge.",
    parameters: {
      action: {
        type: "string",
        required: true,
        enum: ["health", "list_projects", "get_project", "create_project", "import_folder", "get_status"],
        description: "OpenDesign operation to perform"
      },
      project_id: { type: "string", description: "Project id (get_project, get_status)" },
      name: { type: "string", description: "Project name (create_project)" },
      path: { type: "string", description: "Folder path to import (import_folder)" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }]
    },
    async execute(args) {
      const action = args.action;
      const token = process.env.OD_API_TOKEN || "";
      const host = process.env.OD_HOST || "http://open-design:7456";

      try {
        let endpoint = "/api/v1/health";
        let method = "GET";
        let body = null;

        if (action === "health") {
          endpoint = "/api/v1/health";
        } else if (action === "list_projects") {
          endpoint = "/api/v1/projects";
        } else if (action === "get_project") {
          endpoint = `/api/v1/projects/${args.project_id}`;
        } else if (action === "create_project") {
          endpoint = "/api/v1/projects";
          method = "POST";
          body = { name: args.name };
        } else if (action === "import_folder") {
          endpoint = "/api/v1/projects/import";
          method = "POST";
          body = { path: args.path };
        } else if (action === "get_status") {
          endpoint = `/api/v1/projects/${args.project_id}/status`;
        } else {
          return { error: `Unsupported action: ${action}` };
        }

        const headers = { "Content-Type": "application/json" };
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const resp = await fetch(`${host}${endpoint}`, {
          method,
          headers,
          body: body ? JSON.stringify(body) : undefined,
        });

        const data = await resp.json().catch(() => ({}));
        return { status: resp.status, data };
      } catch (err) {
        return { error: err.message };
      }
    },
  });

  ctx.tools.register(designTool);
}

export default { Config, apply, inject, name };
