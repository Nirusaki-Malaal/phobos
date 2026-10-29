/**
 * dsh-pai-telegram — First-party Telegram bot for DSH (host-only).
 *
 * Provides full Telegram ↔ DSH integration:
 * - Chat round-trip: agents.create / followup for seamless chat
 * - Live drafts: real-time editMessageText updates as agent works
 * - Commands: /stop, /new, /model override
 * - Inline approvals & questions (waterfall race vs web UI)
 * - Custom free-text answers, group chats, bot-to-bot loop guard
 * - Failure-contained: errors become error cards, never crash host
 *
 * Config via env: HERMES_TELEGRAM_BOT_TOKEN, HERMES_TELEGRAM_ALLOWED_USERS,
 * HERMES_TELEGRAM_GROUP_ALLOWED_USERS, HERMES_TELEGRAM_GROUP_ALLOWED_CHATS
 *
 * Design: .planning/2026-10-07-telegram-bot/design.md
 * Failure-contained: driver/watcher/gate errors become error cards, never host crash.
 * Token via env only, never logged.
 */
import z from "@deepseek-ai/schemastery";
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync, renameSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { join } from "node:path";
import { escapeHtml, parseAllowedUsers, splitMessage, resultCard, errorCard, stoppedCard, groupAdmission, groupAddressed, botStreakTrips } from "./util.js";
import { ensureAgent, sendFollowup, cancelAgent } from "./driver.js";
import { attachWatcher } from "./watch.js";
import { createDecisions } from "./decision.js";
import { createApprovalHandler, createQuestionHandler, answerQuestionCustom } from "./gate.js";

export const name = "telegram";
export const inject = ["agents", "sessions", "workspaceRegistry", "agentDefaultModel", "agentPresets"];

export const Config = z.object({
  enabled: z.boolean().default(true),
  mode: z.string().default("rich"),
  approvalTimeoutMinutes: z.number().default(10),
  dataDir: z.string().default(""),
});

const API_BASE = "https://api.telegram.org";

function atomicWriteJson(path, obj) {
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, JSON.stringify(obj, null, 2) + "\n");
  renameSync(tmp, path);
}

function readJson(path, fallback) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return fallback;
  }
}

function listWorkspaces(root) {
  try {
    return readdirSync(root).filter((e) => {
      try {
        return statSync(join(root, e)).isDirectory() && !e.startsWith(".");
      } catch {
        return false;
      }
    });
  } catch {
    return [];
  }
}

export function apply(ctx, config) {
  const cfg = { enabled: true, mode: "rich", approvalTimeoutMinutes: 10, dataDir: "", ...(config ?? {}) };
  const log = ctx.logger ?? console;
  if (!cfg.enabled) {
    log.info("dsh-pai-telegram: disabled via config");
    return;
  }

  const token =
    process.env.DSH_TELEGRAM_BOT_TOKEN ||
    process.env.DSH_TELEGRAM_TOKEN ||
    process.env.TELEGRAM_BOT_TOKEN ||
    "";
  const allowed = parseAllowedUsers(process.env.DSH_TELEGRAM_ALLOWED_USERS);
  const groupUsers = parseAllowedUsers(process.env.DSH_TELEGRAM_GROUP_ALLOWED_USERS);
  const groupChats = parseAllowedUsers(process.env.DSH_TELEGRAM_GROUP_ALLOWED_CHATS);
  const groupsOn = groupUsers.length > 0 || groupChats.length > 0;
  let botUsername = (process.env.DSH_TELEGRAM_BOT_USERNAME ?? "").replace(/^@/, "").toLowerCase();
  const timeoutMs = Math.max(1, Number(cfg.approvalTimeoutMinutes) || 10) * 60 * 1000;
  const workspaceRoot = process.env.WORKSPACE_DIR || "/opt/data/workspace";

  const home = process.env.DSH_HOME || "/data/dsh";
  const dataDir = cfg.dataDir || join(home, "storages", "telegram");
  try {
    mkdirSync(dataDir, { recursive: true });
  } catch {}
  const statePath = join(dataDir, "state.json");
  const bindingsPath = join(dataDir, "bindings.json");
  let state = readJson(statePath, { offset: 0 });
  let bindings = readJson(bindingsPath, { chats: {} });
  const saveState = () => {
    try {
      atomicWriteJson(statePath, state);
    } catch {}
  };
  const saveBindings = () => {
    try {
      atomicWriteJson(bindingsPath, bindings);
    } catch {}
  };
  const bindingOf = (chatId) => {
    let b = bindings.chats[String(chatId)];
    if (!b) {
      b = { sessionId: `telegram-${chatId}`, sessions: [`telegram-${chatId}`], cwd: workspaceRoot, model: null };
      bindings.chats[String(chatId)] = b;
      saveBindings();
    }
    if (!Array.isArray(b.sessions)) b.sessions = [b.sessionId];
    return b;
  };

  // Live maps (process lifetime; bindings file is the durable half).
  const seen = new Set();
  const handles = new Map(); // sessionId -> {agent sessionObj modelKey}
  const liveSessionChat = new Map(); // sessionObj -> chatId
  const busy = new Map(); // chatId -> turn ctx
  const stopping = new Set(); // sessionIds flagged by /stop or ⏹
  const botStreak = new Map(); // chatId -> consecutive bot-triggered turns (loop guard)
  let stopped = false;
  let timer = null;
  let backoffMs = 1000;

  const sessionChat = (session) => {
    if (!session) return null;
    if (liveSessionChat.has(session)) return liveSessionChat.get(session);
    const sid = String(session?.id ?? session ?? "");
    for (const [chat, b] of Object.entries(bindings.chats)) {
      if (b.sessionId === sid || (b.sessions ?? []).includes(sid)) return chat;
    }
    return null;
  };

  async function api(method, params = {}) {
    const res = await fetch(`${API_BASE}/bot${token}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(params),
      signal: AbortSignal.timeout(45000),
    });
    if (res.status === 429) {
      const body = await res.json().catch(() => ({}));
      const wait = Math.min((body?.parameters?.retry_after ?? 5) * 1000, 30000);
      await new Promise((r) => setTimeout(r, wait));
      return api(method, params);
    }
    const body = await res.json().catch(() => ({}));
    if (!body.ok) throw new Error(`telegram ${method}: ${body.description ?? res.status}`);
    return body.result;
  }

  async function send(chatId, html, extra = {}) {
    // Forum topics: reply inside the bound thread so group chats stay tidy.
    const thread = bindings.chats[String(chatId)]?.thread;
    const base = thread && extra.message_thread_id === undefined ? { message_thread_id: thread } : {};
    let firstId = null;
    for (const chunk of splitMessage(html)) {
      const r = await api("sendMessage", { chat_id: chatId, text: chunk, parse_mode: "HTML", ...base, ...extra });
      if (firstId === null) firstId = r?.message_id ?? null;
    }
    return firstId;
  }

  async function refreshMe() {
    if (!token || botUsername) return;
    try {
      const me = await api("getMe");
      if (me?.username) botUsername = String(me.username).toLowerCase();
    } catch (e) {
      log.info(`dsh-pai-telegram: getMe failed (mention-gating degraded): ${e?.message ?? e}`);
    }
  }

  async function sendCard(chatId, html, buttons) {
    const kb = buttons?.length ? { reply_markup: { inline_keyboard: buttons.map((row) => row.map((b) => ({ text: b.text, callback_data: b.data }))) } } : {};
    return send(chatId, html, kb);
  }

  async function editCard(chatId, msgId, html, buttons) {
    if (!msgId) return send(chatId, html);
    const kb = buttons?.length ? { reply_markup: { inline_keyboard: buttons.map((row) => row.map((b) => ({ text: b.text, callback_data: b.data }))) } } : {};
    for (const chunk of splitMessage(html)) {
      try {
        await api("editMessageText", { chat_id: chatId, message_id: msgId, text: chunk, parse_mode: "HTML", ...kb });
        return;
      } catch (e) {
        if (!/message is not modified/i.test(e?.message ?? "")) throw e;
        return;
      }
    }
  }

  const decisions = createDecisions({
    notify: (chatId, text) => send(chatId, escapeHtml(text)).catch(() => {}),
  });

  const tg = {
    sendCard: (chatId, text, buttons) => sendCard(chatId, text, buttons),
    editCard: (chatId, msgId, text, buttons) => editCard(chatId, msgId, text, buttons),
    settleKey: (key, text) => {
      decisions.settle(key);
      return Promise.resolve();
    },
  };

  // Approval + question gates (outermost waterfall race vs web answerers).
  const approvalGate = createApprovalHandler({ sessionChat, decisions, tg, log, timeoutMs });
  const questionGate = createQuestionHandler({ sessionChat, decisions, tg, log, timeoutMs });
  try {
    ctx.on?.("approval/request", approvalGate, { prepend: true });
  } catch {
    try {
      ctx.on?.("approval/request", approvalGate);
    } catch (e) {
      log.info(`dsh-pai-telegram: approval gate not registered: ${e?.message ?? e}`);
    }
  }
  try {
    ctx.on?.("user-questions/request", questionGate, { prepend: true });
  } catch {
    try {
      ctx.on?.("user-questions/request", questionGate);
    } catch (e) {
      log.info(`dsh-pai-telegram: question gate not registered: ${e?.message ?? e}`);
    }
  }

  function modelKeyOf(b) {
    return b?.model ?? null;
  }

  async function dropHandle(sessionId) {
    const h = handles.get(sessionId);
    if (h) {
      try {
        cancelAgent(h.handle, "telegram session switch");
      } catch {}
      handles.delete(sessionId);
    }
  }

  /** Run one user turn: ensure agent, followup, live drafts, result card. */
  async function runTurn(chatId, text, { steer = false } = {}) {
    const b = bindingOf(chatId);
    const turn = { delivered: false, draftMsgId: null, lastEdit: 0, pendingDraft: null, heartbeat: null, watcher: null };
    busy.set(chatId, turn);
    const startedAt = Date.now();
    const flushDraft = async (force = false) => {
      if (turn.pendingDraft === null || turn.draftMsgId === null) return;
      const now = Date.now();
      if (!force && now - turn.lastEdit < 1000) return;
      turn.lastEdit = now;
      const body = turn.pendingDraft;
      turn.pendingDraft = null;
      try {
        await editCard(chatId, turn.draftMsgId, `⌛ working…\n${body}`, [[{ text: "⏹ Stop", data: `stop:${chatId}` }]]);
      } catch {}
    };
    try {
      let h = handles.get(b.sessionId);
      const wantKey = modelKeyOf(b);
      if (h && h.modelKey !== wantKey) {
        await dropHandle(b.sessionId);
        h = null;
      }
      if (!h) {
        const created = await ensureAgent(ctx, log, {
          sessionId: b.sessionId,
          cwd: b.cwd ?? workspaceRoot,
          modelOverride: wantKey ? parseModel(wantKey) : null,
        });
        h = { handle: created.handle, modelKey: wantKey, sessionObj: created.handle?.agent?.session ?? null };
        handles.set(b.sessionId, h);
        if (h.sessionObj) liveSessionChat.set(h.sessionObj, String(chatId));
      }
      const session = h.handle?.agent?.session ?? null;
      if (session && !liveSessionChat.has(session)) liveSessionChat.set(session, String(chatId));

      turn.watcher = attachWatcher(ctx, session, {
        onStart: async () => {
          try {
            turn.draftMsgId = await send(chatId, "⌛ working…", {
              reply_markup: { inline_keyboard: [[{ text: "⏹ Stop", callback_data: `stop:${chatId}` }]] },
            });
          } catch {}
          turn.heartbeat = setInterval(() => {
            api("sendChatAction", { chat_id: chatId, action: "typing" }).catch(() => {});
          }, 4000);
        },
        onDraft: (draft) => {
          turn.pendingDraft = draft.length > 3500 ? `${draft.slice(0, 3499)}…` : draft;
          flushDraft().catch(() => {});
        },
        onDone: async ({ text: body, reason, ms }) => {
          if (turn.delivered) return;
          turn.delivered = true;
          if (turn.heartbeat) clearInterval(turn.heartbeat);
          const sel = h.modelKey ? ` · ${h.modelKey}` : "";
          if (stopping.has(b.sessionId)) {
            stopping.delete(b.sessionId);
            try {
              await editCard(chatId, turn.draftMsgId, stoppedCard({ sessionId: b.sessionId }));
            } catch {}
            return;
          }
          const kind = reason?.kind ?? "end";
          const head = kind === "error" ? "❌" : "✅";
          const card = `${head} <b>Done</b> · ⏱ ${Math.round(ms / 1000)}s${sel}\n${body || "(empty turn)"}\n<i>${escapeHtml(`${b.sessionId} · ${b.cwd ?? workspaceRoot}`)}</i>`;
          try {
            await editCard(chatId, turn.draftMsgId, card);
          } catch {}
          try {
            await ctx.sessions?.flush?.(session);
          } catch {}
        },
        onError: async (err) => {
          if (turn.delivered) return;
          turn.delivered = true;
          if (turn.heartbeat) clearInterval(turn.heartbeat);
          try {
            await editCard(chatId, turn.draftMsgId, errorCard({ message: err?.message ?? String(err), sessionId: b.sessionId }));
          } catch {}
        },
      });

      sendFollowup(h.handle, steer ? text.slice(1).trim() || text : text, { chatId: String(chatId) });
      try {
        await h.handle.agent.whenIdle();
      } catch (e) {
        log.info(`dsh-pai-telegram: whenIdle settled with error: ${e?.message ?? e}`);
      }
      await flushDraft(true);
      if (!turn.delivered) {
        turn.delivered = true;
        const card = resultCard({ text: turn.pendingDraft ?? "(empty turn)", ms: Date.now() - startedAt, sessionId: b.sessionId, cwd: b.cwd ?? workspaceRoot, model: h.modelKey });
        try {
          await editCard(chatId, turn.draftMsgId, card);
        } catch {}
      }
    } catch (e) {
      try {
        await send(chatId, errorCard({ message: e?.message ?? String(e), sessionId: b.sessionId }));
      } catch {}
    } finally {
      if (turn.heartbeat) clearInterval(turn.heartbeat);
      try {
        turn.watcher?.stop?.();
      } catch {}
      busy.delete(chatId);
    }
  }

  function parseModel(s) {
    const i = String(s).indexOf("/");
    if (i <= 0) return null;
    return { provider: String(s).slice(0, i), model: String(s).slice(i + 1) };
  }

  function currentDefault() {
    try {
      const sel = ctx.agentDefaultModel?.currentSelection?.();
      if (sel?.provider && sel?.model) return `${sel.provider}/${sel.model}`;
    } catch {}
    return null;
  }

  async function recentSessions() {
    try {
      const list = await ctx.sessions?.list?.();
      const arr = Array.isArray(list) ? list : list?.sessions ?? list?.items ?? [];
      return arr.slice(0, 10).map((s) => ({ id: String(s?.id ?? s?.sessionId ?? s), title: s?.title ?? s?.name ?? null }));
    } catch {
      return [];
    }
  }

  async function handleCommand(chatId, text) {
    const [cmd, ...rest] = text.split(/\s+/);
    const arg = rest.join(" ").trim();
    const b = bindingOf(chatId);
    switch (cmd) {
      case "/help":
        await send(
          chatId,
          `<b>dsh-pai-telegram</b>\n` +
            `plain text — chat with the bound session\n!text — steer mid-turn\n` +
            `/sessions — your sessions (+ recent, tap to attach)\n/workspace [name] — list/switch repo root\n` +
            `/new [name] — fresh session (agent starts on first message)\n/model [provider/id|clear] — model override (recreates agent)\n` +
            `/status — model/session/workspace\n/stop — cancel running turn\n` +
            `/unbind — detach phone, session keeps running\n/help — this card` +
            `${groupsOn ? "\n\nGroups: mention me (@…) or /command, or reply to my messages." : ""}`,
        );
        return true;
      case "/status": {
        const def = currentDefault();
        await send(
          chatId,
          `📊 <b>status</b>\nsession: <code>${escapeHtml(b.sessionId)}</code>\nworkspace: <code>${escapeHtml(b.cwd ?? workspaceRoot)}</code>\n` +
            `model: <code>${escapeHtml(b.model ?? def ?? "?")}</code>${b.model ? "" : " (gateway default)"}\n` +
            `busy: ${busy.has(chatId) ? "yes" : "no"} · offset: ${state.offset ?? 0}`,
        );
        return true;
      }
      case "/sessions": {
        const mine = (b.sessions ?? [b.sessionId]).map((id) => `${id === b.sessionId ? "●" : "○"} <code>${escapeHtml(id)}</code>`).join("\n");
        const recent = await recentSessions();
        const others = recent.filter((s) => !(b.sessions ?? []).includes(s.id)).slice(0, 5);
        let out = `📎 <b>your sessions</b>\n${mine || "(none)"}`;
        if (others.length) {
          out += `\n\n<b>recent</b>\n${others.map((s) => `○ <code>${escapeHtml(s.id)}</code>${s.title ? ` — ${escapeHtml(String(s.title).slice(0, 40))}` : ""}`).join("\n")}\nAttach: <code>/attach &lt;id&gt;</code>`;
        }
        await send(chatId, out);
        return true;
      }
      case "/attach": {
        if (!arg) {
          await send(chatId, "Usage: <code>/attach &lt;session-id&gt;</code> (see /sessions).");
          return true;
        }
        if (busy.has(chatId)) {
          await send(chatId, "⏳ finish the running turn first (/stop to cancel).");
          return true;
        }
        b.sessionId = arg;
        if (!(b.sessions ?? []).includes(arg)) b.sessions = [...(b.sessions ?? []), arg];
        saveBindings();
        await send(chatId, `📎 attached → <code>${escapeHtml(arg)}</code>. Send a message to drive it.`);
        return true;
      }
      case "/workspace": {
        const all = listWorkspaces(workspaceRoot);
        if (!arg) {
          await send(chatId, `📁 <b>workspaces</b>\n${all.map((w) => `• <code>${escapeHtml(w)}</code>`).join("\n") || "(empty)"}\n\nUse <code>/workspace &lt;name&gt;</code> to switch (applies to /new).`);
          return true;
        }
        if (!all.includes(arg)) {
          await send(chatId, `❓ unknown workspace <code>${escapeHtml(arg)}</code>.`);
          return true;
        }
        b.cwd = join(workspaceRoot, arg);
        saveBindings();
        await send(chatId, `📁 workspace → <code>${escapeHtml(b.cwd)}</code> (applies to /new).`);
        return true;
      }
      case "/model": {
        if (!arg) {
          const def = currentDefault();
          await send(chatId, `🎛 model: <code>${escapeHtml(b.model ?? def ?? "?")}</code>${b.model ? " (override)" : " (gateway default)"}\nSet: <code>/model provider/id</code> · clear: <code>/model clear</code>`);
          return true;
        }
        if (busy.has(chatId)) {
          await send(chatId, "⏳ finish the running turn first (/stop to cancel).");
          return true;
        }
        if (arg === "clear") {
          b.model = null;
        } else {
          if (!parseModel(arg)) {
            await send(chatId, `❓ want <code>provider/id</code>, got <code>${escapeHtml(arg)}</code>.`);
            return true;
          }
          b.model = arg;
        }
        await dropHandle(b.sessionId);
        saveBindings();
        await send(chatId, `🎛 model → <code>${escapeHtml(b.model ?? currentDefault() ?? "?")}</code> (agent recreated on next message).`);
        return true;
      }
      case "/new": {
        if (busy.has(chatId)) {
          await send(chatId, "⏳ finish the running turn first (/stop to cancel).");
          return true;
        }
        const suffix = (randomUUID?.() ?? `${Date.now()}`).slice(0, 8);
        const sid = `telegram-${chatId}-${suffix}`;
        b.sessionId = sid;
        b.sessions = [...(b.sessions ?? []), sid];
        if (arg) b.cwd = b.cwd ?? workspaceRoot;
        saveBindings();
        await send(chatId, `🆕 session <code>${escapeHtml(sid)}</code> @ <code>${escapeHtml(b.cwd ?? workspaceRoot)}</code> — send your task.`);
        return true;
      }
      case "/compact":
        await send(chatId, "🗜 compaction isn't exposed to plugins in v1 — start a /new session for a fresh context.");
        return true;
      case "/stop": {
        const turn = busy.get(chatId);
        stopping.add(b.sessionId);
        if (turn) {
          const h = handles.get(b.sessionId);
          if (h) cancelAgent(h.handle, "telegram stop");
          await send(chatId, "⏹ stopping…");
        } else {
          await send(chatId, "⏹ nothing running.");
        }
        return true;
      }
      case "/unbind": {
        await dropHandle(b.sessionId);
        delete bindings.chats[String(chatId)];
        saveBindings();
        await send(chatId, "🔌 unbound — session keeps running. Send any message to re-bind.");
        return true;
      }
      default:
        return false;
    }
  }

  function clickerAllowed(chatId, chatType, clickerId) {
    const from = String(clickerId ?? "");
    if (!from) return false;
    if (chatType === "private") return allowed.includes(from);
    return groupAdmission({ fromId: from, chatId, allowedUsers: allowed, groupUsers, groupChats }).ok;
  }

  async function handleUpdate(u) {
    if (u.callback_query) {
      const cq = u.callback_query;
      const data = cq.data ?? "";
      const msgChat = String(cq.message?.chat?.id ?? "");
      const msgType = cq.message?.chat?.type ?? "private";
      const ack = (text) => api("answerCallbackQuery", { callback_query_id: cq.id, ...(text ? { text } : {}) }).catch(() => {});
      if (!clickerAllowed(msgChat, msgType, cq.from?.id)) {
        await ack();
        return;
      }
      if (data.startsWith("stop:")) {
        const target = data.slice(5);
        if (msgChat && target !== msgChat) {
          await ack();
          return;
        }
        const turn = busy.get(target);
        const bb = bindings.chats[target];
        if (bb) stopping.add(bb.sessionId);
        if (turn) {
          const h = bb && handles.get(bb.sessionId);
          if (h) cancelAgent(h.handle, "telegram stop button");
          await ack("stopping…");
        } else {
          await ack("nothing running");
        }
        return;
      }
      const consumed = await decisions.feed(data, { answerCb: () => ack() });
      if (!consumed) await ack();
      return;
    }
    const msg = u.message ?? u.edited_message;
    if (!msg) return;
    const chatId = String(msg.chat?.id ?? "");
    const chatType = msg.chat?.type ?? "private"; // private|group|supergroup|channel
    const fromId = String(msg.from?.id ?? "");
    if (!chatId) return;
    if (chatType === "private") {
      if (!allowed.includes(chatId)) return; // silent drop, zero token burn
    } else {
      // Groups: owner may use any chat; members only listed chats. Silent drop otherwise.
      if (!groupAdmission({ fromId, chatId, allowedUsers: allowed, groupUsers, groupChats }).ok) return;
    }
    // Forum topics: remember the thread so replies stay tidy.
    if (msg.message_thread_id) {
      const b0 = bindingOf(chatId);
      if (b0.thread !== msg.message_thread_id) {
        b0.thread = msg.message_thread_id;
        saveBindings();
      }
    }
    if (msg.photo?.length || msg.document) {
      await send(chatId, "📷 photos/files unsupported in v1 — send as text.");
      return;
    }
    let text = (msg.text ?? "").trim();
    if (!text) return;
    const isBotSender = !!msg.from?.is_bot;
    if (chatType !== "private") {
      // Mention-gated: /commands, @mention, or reply-to-bot only. Group noise ignored.
      const rfu = msg.reply_to_message?.from?.username;
      const isReplyToBot = botUsername ? String(rfu ?? "").toLowerCase() === botUsername : !!msg.reply_to_message?.from?.is_bot;
      const addressed = groupAddressed({ text, username: botUsername, isReplyToBot });
      if (!addressed) return;
      text = addressed.text;
      // Bot-to-bot loop guard (per Telegram docs): cap consecutive
      // bot-triggered turns per chat; any human message resets the streak.
      // Bot senders must already be explicitly allowlisted above.
      if (isBotSender) {
        if (botStreakTrips(botStreak.get(chatId) ?? 0)) {
          await send(chatId, "⚠️ loop guard tripped (5 bot turns in a row) — a human message resets it.");
          return;
        }
        botStreak.set(chatId, (botStreak.get(chatId) ?? 0) + 1);
      } else {
        botStreak.set(chatId, 0);
      }
    }
    bindingOf(chatId); // ensure binding exists
    if (text.startsWith("/")) {
      try {
        if (await handleCommand(chatId, text)) return;
      } catch (e) {
        log.info(`dsh-pai-telegram: command failed: ${e?.message ?? e}`);
        return;
      }
    }
    // Custom free-text answer to a pending question wins over chat input.
    const custom = decisions.feedText(chatId, text);
    if (custom) {
      const r = answerQuestionCustom(custom.key, custom.exp.qi, custom.text);
      if (r === "pending") {
        await send(chatId, "✍️ noted — more questions pending.");
      } else if (r === true) {
        await send(chatId, "✍️ custom answer recorded.");
      }
      return;
    }
    const turn = busy.get(chatId);
    if (turn) {
      if (text.startsWith("!")) {
        const b = bindingOf(chatId);
        const h = handles.get(b.sessionId);
        if (h) {
          try {
            sendFollowup(h.handle, text.slice(1).trim() || text, { chatId });
            await send(chatId, "🧭 steered mid-turn.");
          } catch (e) {
            await send(chatId, errorCard({ message: e?.message ?? String(e), sessionId: b.sessionId }));
          }
        }
      } else {
        await send(chatId, "⏳ still working — <code>!text</code> steers, /stop cancels.");
      }
      return;
    }
    runTurn(chatId, text).catch((e) => log.info(`dsh-pai-telegram: turn failed: ${e?.message ?? e}`));
  }

  async function poll() {
    if (stopped) return;
    if (!token) {
      timer = setTimeout(poll, 30000);
      return;
    }
    try {
      const updates = await api("getUpdates", { timeout: 30, offset: (state.offset ?? 0) + 1, allowed_updates: ["message", "edited_message", "callback_query"] });
      backoffMs = 1000;
      for (const u of updates ?? []) {
        state.offset = Math.max(state.offset ?? 0, u.update_id ?? 0);
        if (seen.has(u.update_id)) continue;
        seen.add(u.update_id);
        if (seen.size > 1000) seen.clear();
        try {
          await handleUpdate(u);
        } catch (e) {
          log.info(`dsh-pai-telegram: update failed: ${e?.message ?? e}`);
        }
      }
      saveState();
      timer = setTimeout(poll, 0);
    } catch (e) {
      log.info(`dsh-pai-telegram: poll failed, retry in ${Math.round(backoffMs / 1000)}s`);
      timer = setTimeout(poll, backoffMs);
      backoffMs = Math.min(backoffMs * 2, 30000);
    }
  }

  if (!token) {
    log.info("dsh-pai-telegram: no token (DSH_TELEGRAM_BOT_TOKEN) — poller idle, plugin inert");
  } else {
    log.info(
      `dsh-pai-telegram: driver live — polling as dedicated bot (${allowed.length} owner(s)` +
        `${groupsOn ? `, groups on (${groupUsers.length} member(s), ${groupChats.length} chat(s), mention-gated)` : ", groups off"})`,
    );
    refreshMe().finally(() => poll());
  }

  const dispose = () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
  if (typeof ctx.effect === "function") {
    ctx.effect(() => dispose, "dsh-pai-telegram: poller");
  }
}

export default { Config, apply, inject, name };
