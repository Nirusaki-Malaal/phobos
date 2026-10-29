/**
 * Agent driver — ensure/reuse a live agent per bound session, followup, stop.
 * Mirrors @michengai/dsh-automation executeAutomationRun on DSH 0.2.0-rc.2.
 * Container-only (imports DSH packages; never imported by host tests).
 */
import { installModelSelection } from "@deepseek-ai/dsh-agent";
import { createUserMessage } from "@deepseek-ai/dsh-llm";
import { SessionId } from "@deepseek-ai/dsh-session";
import { WorkspaceId } from "@deepseek-ai/dsh-workspace";

const PRESET_CANDIDATES = ["default", "general", "coding"];

async function resolvePreset(ctx, log) {
  // 1. Any live agent's mounted preset is authoritative for this runtime.
  try {
    const roots = ctx.agents?.roots;
    const list = typeof roots === "function" ? await roots() : Array.isArray(roots) ? roots : [];
    for (const a of list ?? []) {
      const p = a?.session?.agentPreset ?? a?.preset ?? a?.meta?.agentPreset;
      if (typeof p === "string" && p) return { preset: p, via: "live-agent" };
    }
  } catch {}
  return { preset: null, via: "none" };
}

function currentSelection(ctx) {
  try {
    return ctx.agentDefaultModel?.currentSelection?.() ?? null;
  } catch {
    return null;
  }
}

function resolveWorkspace(ctx, cwd) {
  try {
    if (typeof ctx.workspaceRegistry?.resolveByPath === "function") {
      const ws = ctx.workspaceRegistry.resolveByPath(cwd);
      if (ws) return ws;
    }
  } catch {}
  try {
    const all = ctx.workspaceRegistry?.all?.() ?? ctx.workspaceRegistry?.list?.() ?? [];
    for (const ws of all) {
      if (ws?.path === cwd) return ws;
    }
  } catch {}
  return null;
}

/**
 * Ensure a live agent for (sessionId, cwd). Reuses ctx.agents.get when a live
 * agent already exists (e.g. attached web session); otherwise creates one.
 * modelOverride: {provider, model} | null. Returns {handle, created, preset}.
 */
export async function ensureAgent(ctx, log, { sessionId, cwd, modelOverride }) {
  const sid = SessionId(sessionId);
  try {
    const existing = await ctx.agents.get(sid);
    if (existing) return { handle: { agent: existing }, created: false, preset: null };
  } catch {}

  const fallback = currentSelection(ctx);
  const selection =
    modelOverride?.provider && modelOverride?.model
      ? { provider: modelOverride.provider, model: modelOverride.model }
      : fallback;
  if (!selection) throw new Error("no model selection (gateway default unavailable)");

  const { preset: livePreset, via } = await resolvePreset(ctx, log);
  const candidates = [...new Set([livePreset, ...PRESET_CANDIDATES].filter(Boolean))];
  let mountedPreset = null;
  const handle = await ctx.agents.withoutInitiator(() =>
    ctx.agents.create({
      sessionId: sid,
      meta: { cwd },
      agentOptions: { provider: selection.provider, model: selection.model },
      setup: async (agentCtx, createdAgent) => {
        for (const p of candidates) {
          try {
            await ctx.agentPresets.mount(agentCtx, p);
            mountedPreset = p;
            break;
          } catch {}
        }
        try {
          installModelSelection(agentCtx, { current: selection, assembled: undefined });
        } catch (e) {
          log?.info?.(`dsh-pai-telegram: installModelSelection failed: ${e?.message ?? e}`);
        }
        // NOTE: approval policy intentionally left at default ("ask") so tool
        // calls escalate to the inline Telegram/web race instead of auto-run.
      },
    }),
  );
  log?.info?.(`dsh-pai-telegram: agent created for ${sessionId} (preset=${mountedPreset ?? "unmounted"} via=${via}, model=${selection.provider}/${selection.model})`);

  await handle.agent.whenIdle();
  try {
    const ws = resolveWorkspace(ctx, cwd);
    if (ws && typeof ws.attachSession === "function") await ws.attachSession(sid);
  } catch (e) {
    log?.info?.(`dsh-pai-telegram: attachSession failed: ${e?.message ?? e}`);
  }
  try {
    await ctx.sessions?.flush?.(handle.agent.session);
  } catch {}
  return { handle, created: true, preset: mountedPreset, selection };
}

export function sendFollowup(handle, text, source) {
  handle.agent.followup(
    createUserMessage({
      content: [{ type: "text", text }],
      source: { kind: "telegram", ...source },
    }),
  );
}

export function cancelAgent(handle, reason) {
  try {
    handle.agent.cancel({ kind: "hook", reason });
  } catch {}
}
