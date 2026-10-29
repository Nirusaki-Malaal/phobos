/**
 * dsh-pai-git — DSH plugin for git operations with worktree support.
 *
 * Provides: pai_git_ops tool with actions:
 * - status, log, diff, branch, checkout, commit, push, pull, merge, rebase
 * - worktree_create, worktree_list, worktree_remove
 * - stash, tag, remote, config
 *
 * Runs git commands safely within the workspace (cwd=WORKSPACE_DIR).
 * Use worktree_* actions for isolated feature branches without disrupting main branch.
 */

import z from "@deepseek-ai/schemastery";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { spawn } from "node:child_process";
import { join, resolve } from "node:path";

export const name = "pai-git";
export const inject = ["tools"];

export const Config = z.object({
  enabled: z.boolean().default(true),
});

function runGit(args, cwd) {
  return new Promise((resolve, reject) => {
    const child = spawn("git", args, { cwd, env: { ...process.env, GIT_TERMINAL_PROMPT: "0" } });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", d => stdout += d);
    child.stderr.on("data", d => stderr += d);
    child.on("close", code => {
      if (code === 0) resolve({ stdout: stdout.trim(), stderr: stderr.trim() });
      else reject(new Error(stderr.trim() || `git exited with code ${code}`));
    });
    child.on("error", reject);
  });
}

function parseWorktreeLine(line) {
  // Format: <path> <hash> [<branch>]
  const match = line.match(/^(.+?)\s+([a-f0-9]+)\s*(?:\[(.+?)\])?$/);
  if (!match) return null;
  return { path: match[1], hash: match[2], branch: match[3] || null };
}

export function apply(ctx, config) {
  const cfg = { enabled: true, ...(config ?? {}) };
  if (!cfg.enabled) return;

  const workspaceRoot = process.env.WORKSPACE_DIR || "/opt/data/workspace";

  const gitTool = defineTool({
    name: "pai_git_ops",
    description: "Git operations with worktree support. All commands run in the execution directory or specified workspace.",
    parameters: {
      action: {
        type: "string",
        required: true,
        enum: [
          "status", "log", "diff", "branch", "checkout", "commit", "push", "pull", 
          "merge", "rebase", "stash", "tag", "remote", "config",
          "worktree_create", "worktree_list", "worktree_remove"
        ],
        description: "Git operation to perform"
      },
      cwd: { type: "string", description: "Working directory (default: execution dir or workspace root)" },
      // status
      short: { type: "boolean", description: "Short format for status" },
      // log
      oneline: { type: "boolean", description: "One line per commit" },
      max_count: { type: "integer", description: "Max commits to show", default: 20 },
      since: { type: "string", description: "Show commits since date" },
      author: { type: "string", description: "Filter by author" },
      // diff
      staged: { type: "boolean", description: "Show staged changes" },
      name_only: { type: "boolean", description: "Show only file names" },
      // branch
      list_all: { type: "boolean", description: "List all branches (local + remote)" },
      create: { type: "string", description: "Create new branch" },
      delete: { type: "string", description: "Delete branch" },
      force_delete: { type: "boolean", description: "Force delete unmerged branch" },
      // checkout
      target: { type: "string", description: "Branch, commit, or file to checkout" },
      new_branch: { type: "string", description: "Create and checkout new branch (-b)" },
      // commit
      message: { type: "string", description: "Commit message" },
      all: { type: "boolean", description: "Stage all tracked files (-a)" },
      amend: { type: "boolean", description: "Amend last commit" },
      no_verify: { type: "boolean", description: "Skip pre-commit hooks" },
      // push
      remote_name: { type: "string", description: "Remote name", default: "origin" },
      branch_name: { type: "string", description: "Branch to push" },
      force: { type: "boolean", description: "Force push" },
      force_with_lease: { type: "boolean", description: "Force push with lease" },
      set_upstream: { type: "boolean", description: "Set upstream branch" },
      // pull
      rebase_pull: { type: "boolean", description: "Rebase instead of merge" },
      // merge
      merge_target: { type: "string", description: "Branch to merge into current" },
      no_ff: { type: "boolean", description: "No fast-forward" },
      squash: { type: "boolean", description: "Squash merge" },
      // rebase
      rebase_target: { type: "string", description: "Branch to rebase onto" },
      interactive: { type: "boolean", description: "Interactive rebase" },
      onto: { type: "string", description: "Rebase onto specific commit" },
      // stash
      stash_action: { type: "string", enum: ["push", "pop", "apply", "drop", "list", "clear"], description: "Stash action" },
      stash_index: { type: "integer", description: "Stash index for pop/apply/drop" },
      stash_message: { type: "string", description: "Stash message for push" },
      // tag
      tag_name: { type: "string", description: "Tag name" },
      tag_message: { type: "string", description: "Tag message (annotated)" },
      tag_delete: { type: "string", description: "Tag to delete" },
      // remote
      remote_action: { type: "string", enum: ["add", "remove", "rename", "set-url", "get-url", "list"], description: "Remote action" },
      remote_url: { type: "string", description: "Remote URL for add/set-url" },
      // config
      config_action: { type: "string", enum: ["get", "set", "unset", "list"], description: "Config action" },
      config_key: { type: "string", description: "Config key" },
      config_value: { type: "string", description: "Config value for set" },
      config_scope: { type: "string", enum: ["local", "global", "system"], description: "Config scope", default: "local" },
      // worktree
      worktree_path: { type: "string", description: "Path for new worktree (relative to workspace root)" },
      worktree_branch: { type: "string", description: "Branch for new worktree" },
      worktree_create_branch: { type: "boolean", description: "Create new branch for worktree (-b)" },
      worktree_force: { type: "boolean", description: "Force worktree creation" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }]
    },
    async execute(args) {
      const action = args.action;
      const cwd = args.cwd ? resolve(args.cwd) : workspaceRoot;
      
      try {
        if (action === "status") {
          const gitArgs = ["status"];
          if (args.short) gitArgs.push("--short");
          if (args.branch) gitArgs.push("--branch");
          const { stdout } = await runGit(gitArgs, cwd);
          return { status: stdout };
        }

        if (action === "log") {
          const gitArgs = ["log"];
          if (args.oneline) gitArgs.push("--oneline");
          if (args.max_count) gitArgs.push(`-n${args.max_count}`);
          if (args.since) gitArgs.push(`--since=${args.since}`);
          if (args.author) gitArgs.push(`--author=${args.author}`);
          gitArgs.push("--pretty=format:%H|%an|%ad|%s", "--date=iso");
          const { stdout } = await runGit(gitArgs, cwd);
          const commits = stdout.split("\n").filter(l => l).map(line => {
            const [hash, author, date, ...msgParts] = line.split("|");
            return { hash, author, date, message: msgParts.join("|") };
          });
          return { commits };
        }

        if (action === "diff") {
          const gitArgs = ["diff"];
          if (args.staged) gitArgs.push("--staged");
          if (args.name_only) gitArgs.push("--name-only");
          const { stdout } = await runGit(gitArgs, cwd);
          return { diff: stdout };
        }

        if (action === "branch") {
          const gitArgs = ["branch"];
          if (args.list_all) gitArgs.push("-a");
          if (args.create) {
            gitArgs.push(args.create);
            if (args.target) gitArgs.push(args.target);
            return { created: await runGit(gitArgs, cwd) };
          }
          if (args.delete) {
            gitArgs.push(args.force_delete ? "-D" : "-d", args.delete);
            return { deleted: await runGit(gitArgs, cwd) };
          }
          const { stdout } = await runGit(gitArgs, cwd);
          const branches = stdout.split("\n").filter(l => l).map(l => {
            const current = l.startsWith("*");
            return { current, name: l.replace(/^\*?\s+/, "") };
          });
          return { branches };
        }

        if (action === "checkout") {
          if (!args.target && !args.new_branch) return { error: "target or new_branch required" };
          const gitArgs = ["checkout"];
          if (args.new_branch) {
            gitArgs.push("-b", args.new_branch);
            if (args.target) gitArgs.push(args.target);
          } else {
            gitArgs.push(args.target);
          }
          const { stdout } = await runGit(gitArgs, cwd);
          return { checked_out: stdout };
        }

        if (action === "commit") {
          if (!args.message) return { error: "message required" };
          const gitArgs = ["commit", "-m", args.message];
          if (args.all) gitArgs.push("-a");
          if (args.amend) gitArgs.push("--amend");
          if (args.no_verify) gitArgs.push("--no-verify");
          const { stdout } = await runGit(gitArgs, cwd);
          return { committed: stdout };
        }

        if (action === "push") {
          const gitArgs = ["push"];
          const remote = args.remote_name || "origin";
          const branch = args.branch_name;
          if (args.force) gitArgs.push("--force");
          if (args.force_with_lease) gitArgs.push("--force-with-lease");
          if (args.set_upstream) gitArgs.push("-u");
          gitArgs.push(remote);
          if (branch) gitArgs.push(branch);
          const { stdout } = await runGit(gitArgs, cwd);
          return { pushed: stdout };
        }

        if (action === "pull") {
          const gitArgs = ["pull"];
          if (args.rebase_pull) gitArgs.push("--rebase");
          if (args.remote_name) gitArgs.push(args.remote_name);
          if (args.branch_name) gitArgs.push(args.branch_name);
          const { stdout } = await runGit(gitArgs, cwd);
          return { pulled: stdout };
        }

        if (action === "merge") {
          if (!args.merge_target) return { error: "merge_target required" };
          const gitArgs = ["merge", args.merge_target];
          if (args.no_ff) gitArgs.push("--no-ff");
          if (args.squash) gitArgs.push("--squash");
          const { stdout } = await runGit(gitArgs, cwd);
          return { merged: stdout };
        }

        if (action === "rebase") {
          if (!args.rebase_target && !args.onto) return { error: "rebase_target or onto required" };
          const gitArgs = ["rebase"];
          if (args.interactive) gitArgs.push("-i");
          if (args.onto) {
            gitArgs.push("--onto", args.onto);
            if (args.rebase_target) gitArgs.push(args.rebase_target);
          } else {
            gitArgs.push(args.rebase_target);
          }
          const { stdout } = await runGit(gitArgs, cwd);
          return { rebased: stdout };
        }

        if (action === "stash") {
          const subAction = args.stash_action || "push";
          const gitArgs = ["stash", subAction];
          if (["pop", "apply", "drop"].includes(subAction) && args.stash_index !== undefined) {
            gitArgs.push(`stash@{${args.stash_index}}`);
          }
          if (subAction === "push" && args.stash_message) {
            gitArgs.push("-m", args.stash_message);
          }
          const { stdout } = await runGit(gitArgs, cwd);
          return { stash: stdout };
        }

        if (action === "tag") {
          if (args.tag_delete) {
            const { stdout } = await runGit(["tag", "-d", args.tag_delete], cwd);
            return { deleted_tag: stdout };
          }
          if (args.tag_name) {
            const gitArgs = ["tag"];
            if (args.tag_message) {
              gitArgs.push("-a", args.tag_name, "-m", args.tag_message);
            } else {
              gitArgs.push(args.tag_name);
            }
            const { stdout } = await runGit(gitArgs, cwd);
            return { created_tag: stdout };
          }
          const { stdout } = await runGit(["tag", "-l"], cwd);
          return { tags: stdout.split("\n").filter(l => l) };
        }

        if (action === "remote") {
          const subAction = args.remote_action || "list";
          let gitArgs = ["remote"];
          switch (subAction) {
            case "add":
              if (!args.remote_name || !args.remote_url) return { error: "remote_name and remote_url required for add" };
              gitArgs.push("add", args.remote_name, args.remote_url);
              break;
            case "remove":
              if (!args.remote_name) return { error: "remote_name required for remove" };
              gitArgs.push("remove", args.remote_name);
              break;
            case "rename":
              if (!args.remote_name || !args.branch_name) return { error: "remote_name and branch_name (new name) required for rename" };
              gitArgs.push("rename", args.remote_name, args.branch_name);
              break;
            case "set-url":
              if (!args.remote_name || !args.remote_url) return { error: "remote_name and remote_url required for set-url" };
              gitArgs.push("set-url", args.remote_name, args.remote_url);
              break;
            case "get-url":
              if (!args.remote_name) return { error: "remote_name required for get-url" };
              gitArgs.push("get-url", args.remote_name);
              break;
            default:
              gitArgs.push("-v");
          }
          const { stdout } = await runGit(gitArgs, cwd);
          return { remote: stdout };
        }

        if (action === "config") {
          const subAction = args.config_action || "list";
          let gitArgs = ["config"];
          if (args.config_scope !== "local") gitArgs.push(`--${args.config_scope}`);
          switch (subAction) {
            case "get":
              if (!args.config_key) return { error: "config_key required for get" };
              gitArgs.push("get", args.config_key);
              break;
            case "set":
              if (!args.config_key || !args.config_value) return { error: "config_key and config_value required for set" };
              gitArgs.push("set", args.config_key, args.config_value);
              break;
            case "unset":
              if (!args.config_key) return { error: "config_key required for unset" };
              gitArgs.push("unset", args.config_key);
              break;
            default:
              gitArgs.push("--list");
          }
          const { stdout } = await runGit(gitArgs, cwd);
          return { config: stdout };
        }

        if (action === "worktree_list") {
          const { stdout } = await runGit(["worktree", "list", "--porcelain"], cwd);
          const worktrees = [];
          let current = {};
          for (const line of stdout.split("\n")) {
            if (!line) {
              if (current.path) worktrees.push(current);
              current = {};
              continue;
            }
            if (line.startsWith("worktree ")) current.path = line.slice(9);
            else if (line.startsWith("HEAD ")) current.hash = line.slice(5);
            else if (line.startsWith("branch ")) current.branch = line.slice(7).replace(/^refs\/heads\//, "");
            else if (line === "bare") current.bare = true;
            else if (line === "detached") current.detached = true;
          }
          if (current.path) worktrees.push(current);
          return { worktrees };
        }

        if (action === "worktree_create") {
          if (!args.worktree_path || !args.worktree_branch) return { error: "worktree_path and worktree_branch required" };
          const gitArgs = ["worktree", "add"];
          if (args.worktree_force) gitArgs.push("-f");
          if (args.worktree_create_branch) gitArgs.push("-b");
          const fullPath = join(workspaceRoot, args.worktree_path);
          gitArgs.push(fullPath, args.worktree_branch);
          const { stdout } = await runGit(gitArgs, cwd);
          return { worktree_created: stdout, path: fullPath };
        }

        if (action === "worktree_remove") {
          if (!args.worktree_path) return { error: "worktree_path required" };
          const gitArgs = ["worktree", "remove"];
          if (args.worktree_force) gitArgs.push("--force");
          const fullPath = join(workspaceRoot, args.worktree_path);
          gitArgs.push(fullPath);
          const { stdout } = await runGit(gitArgs, cwd);
          return { worktree_removed: stdout };
        }

        return { error: `Unsupported action: ${action}` };
      } catch (err) {
        return { error: `${action}: ${err.message}` };
      }
    },
  });

  ctx.tools.register(gitTool);
}

export default { Config, apply, inject, name };