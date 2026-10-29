/** Pure helpers (no DSH imports — unit-testable on the host). */

/** Split text into ≤4096-char chunks without breaking inside ``` fences. */
export function splitMessage(text, limit = 4096) {
  if ([...text].length <= limit) return [text];
  const chars = [...text];
  const chunks = [];
  let cur = [];
  let curLen = 0;
  let inFence = false;
  const push = () => {
    if (cur.length) chunks.push(cur.join(""));
    cur = [];
    curLen = 0;
  };
  for (let i = 0; i < chars.length; i++) {
    if (text.slice(i, i + 3) === "```" && chars[i] === "`") {
      inFence = !inFence;
    }
    cur.push(chars[i]);
    curLen++;
    if (curLen >= limit && !inFence) push();
  }
  push();
  return chunks.map((c, i, a) => (a.length > 1 ? `${c}\n(${i + 1}/${a.length})` : c));
}

export function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function parseAllowedUsers(raw) {
  return String(raw ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter((s) => /^-?\d+$/.test(s));
}

/** Short random id for single-use callback payloads (64B Telegram limit). */
export function shortId(n = 8) {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  const buf = new Uint32Array(n);
  crypto.getRandomValues(buf);
  for (let i = 0; i < n; i++) out += chars[buf[i] % chars.length];
  return out;
}

function fmtDuration(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${s % 60 ? `${s % 60}s` : ""}`;
  return `${Math.floor(m / 60)}h${m % 60}m`;
}

export function codeBlock(text) {
  // Fence unless already fenced; escape handled by caller via escapeHtml.
  if (/```/.test(text)) return text;
  return `\`\`\`\n${text}\n\`\`\``;
}

export function resultCard({ text, ms, sessionId, cwd, model }) {
  const head = `✅ <b>Done</b> · ⏱ ${fmtDuration(ms)}`;
  const foot = `${sessionId} · ${cwd}${model ? ` · ${model}` : ""}`;
  return `${head}\n${text}\n<i>${escapeHtml(foot)}</i>`;
}

export function errorCard({ message, sessionId }) {
  return `❌ <b>Error</b>\n${escapeHtml(message)}${sessionId ? `\n<i>${escapeHtml(sessionId)}</i>` : ""}`;
}

export function stoppedCard({ sessionId }) {
  return `⏹ <b>Stopped</b>${sessionId ? `\n<i>${escapeHtml(sessionId)}</i>` : ""}`;
}

export function approvalCardText({ toolName, reason }) {
  return `🔐 <b>Approval needed</b>\ntool: <code>${escapeHtml(toolName ?? "?")}</code>${reason ? `\n${escapeHtml(reason)}` : ""}`;
}

export function questionCardText({ questions }) {
  const lines = [`❓ <b>Input needed</b>`];
  for (const q of questions.slice(0, 4)) {
    lines.push(`\n<b>${escapeHtml(q.title ?? q.id ?? "question")}</b>`);
    if (q.detail) lines.push(escapeHtml(String(q.detail).slice(0, 300)));
    for (const o of (q.options ?? []).slice(0, 5)) {
      lines.push(`• ${escapeHtml(o.label ?? o.id ?? "?")}`);
    }
  }
  return lines.join("\n");
}

/**
 * Bot-sender loop guard — Telegram's bot-to-bot docs require predictable
 * termination. Consecutive bot-triggered turns per chat are capped; any
 * human message resets the streak. Returns true when the guard trips.
 */
export function botStreakTrips(streak, limit = 5) {
  return streak >= limit;
}

/**
 * Group admission — mirrors Hermes semantics:
 * - owners (allowedUsers) may use DMs and ANY group;
 * - groupUsers (humans AND explicitly listed bots) may use only listed groupChats;
 * - anyone else is silently dropped. Bot senders are never owners-by-default:
 *   they must be explicitly listed (loop-guard assumption).
 * Returns {ok, role: 'owner'|'member'|null}.
 */
export function groupAdmission({ fromId, chatId, allowedUsers, groupUsers, groupChats }) {
  const from = String(fromId ?? "");
  const chat = String(chatId ?? "");
  if (allowedUsers.includes(from)) return { ok: true, role: "owner" };
  if (from && groupUsers.includes(from) && groupChats.includes(chat)) return { ok: true, role: "member" };
  return { ok: false, role: null };
}

/**
 * Group addressing: in groups the bot only reacts when addressed —
 * a /command, an @username mention, or a reply to one of its messages.
 * Returns {text, addressed} with the mention stripped, or null to ignore.
 */
export function groupAddressed({ text, username, isReplyToBot }) {
  const t = String(text ?? "").trim();
  if (!t) return null;
  if (t.startsWith("/")) return { text: t, addressed: true };
  if (username) {
    const re = new RegExp(`@${username}\\b`, "gi");
    if (re.test(t)) {
      const stripped = t.replace(re, "").trim().replace(/\s{2,}/g, " ");
      if (!stripped) return null;
      return { text: stripped, addressed: true };
    }
  }
  if (isReplyToBot) return { text: t, addressed: true };
  return null;
}
