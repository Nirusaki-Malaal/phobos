/**
 * Approval + question gates — outermost waterfall race: phone vs downstream.
 * Container-only orchestration over injected deps (no DSH imports).
 *
 * deps: {sessionChat(session)->chatId|null, decisions, tg, log, timeoutMs}
 * tg: {sendCard(chatId, text, buttons)->msgId, editCard(chatId, msgId, text, buttons)}
 */
import { shortId, approvalCardText, questionCardText } from "./util.js";

function tag(promise, source) {
  return promise.then(
    (value) => ({ source, value }),
    () => ({ source, value: undefined }),
  );
}

async function raceGate({ phone, downstream, log, label }) {
  const winner = await Promise.race([tag(phone, "phone"), tag(downstream, "web")]);
  // phone resolves via decisions.wait -> {source, outcome}; unwrap one level.
  const v = winner.value;
  const outcome = v && typeof v === "object" && "outcome" in v ? v.outcome : v;
  log?.info?.(`dsh-pai-telegram: ${label} decided on ${winner.source}`);
  return { source: winner.source, outcome };
}

export function createApprovalHandler({ sessionChat, decisions, tg, log, timeoutMs }) {
  return async function approvalGate(req, next) {
    const session = req?.agent?.session;
    const chatId = session ? sessionChat(session) : null;
    if (!chatId) return next();
    const key = shortId();
    const toolName = req?.toolName ?? "?";
    const reason = req?.reason ?? "";
    let msgId = null;
    try {
      msgId = await tg.sendCard(chatId, approvalCardText({ toolName, reason }), [
        [
          { text: "✅ Approve", data: `ap:${key}:allow` },
          { text: "⛔ Reject", data: `ap:${key}:deny` },
        ],
      ]);
    } catch {}
    const phone = decisions.wait(key, {
      chatId,
      timeoutMs,
      onData: async (data) => {
        const parts = String(data ?? "").split(":");
        if (parts[0] !== "ap" || parts[1] !== key) return false;
        const allow = parts[2] === "allow";
        try {
          await tg.editCard?.(chatId, msgId, allow ? "✅ approved on phone" : "⛔ rejected on phone", []);
        } catch {}
        return { outcome: allow ? "allowed-once" : "rejected" };
      },
      onTimeout: () => {
        tg.editCard?.(chatId, msgId, "⛔ approval timed out — rejected (fail-closed)", []).catch?.(() => {});
        return "rejected";
      },
    });
    const downstream = Promise.resolve().then(
      () => next(),
      () => "unavailable",
    );
    const winner = await raceGate({ phone, downstream, log, label: `approval ${toolName}` });
    if (winner.source === "web") {
      try {
        await tg.editCard?.(chatId, msgId, "answered on web", []);
      } catch {}
      decisions.settle(key);
      return winner.outcome ?? "unavailable";
    }
    return winner.outcome ?? "rejected";
  };
}

export function createQuestionHandler({ sessionChat, decisions, tg, log, timeoutMs }) {
  return async function questionGate(req, next) {
    const session = req?.agent?.session;
    const chatId = session ? sessionChat(session) : null;
    const questions = Array.isArray(req?.questions) ? req.questions.slice(0, 4) : [];
    if (!chatId || !questions.length) return next();
    const key = shortId();
    const answers = new Map(); // qi -> {id, selected:[], custom?}
    const buttons = [];
    questions.forEach((q, qi) => {
      const row = [];
      for (const o of (q.options ?? []).slice(0, 5)) {
        row.push({ text: String(o.label ?? "?").slice(0, 30), data: `q:${key}:${qi}:opt:${encodeURIComponent(String(o.label))}` });
      }
      if (row.length) buttons.push(row);
      buttons.push([
        { text: "✍️ custom", data: `q:${key}:${qi}:custom` },
        { text: "⏭ skip", data: `q:${key}:${qi}:skip` },
      ]);
    });
    const total = questions.length;
    const doneShape = () => ({ answers: questions.map((q, qi) => answers.get(qi) ?? { id: q.id, selected: [] }) });
    let msgId = null;
    try {
      msgId = await tg.sendCard(chatId, questionCardText({ questions: req.questions }), buttons);
    } catch {}
    const phone = decisions.wait(key, {
      chatId,
      timeoutMs,
      onData: async (data, helpers) => {
        const parts = String(data ?? "").split(":");
        if (parts[0] !== "q" || parts[1] !== key) return false;
        const qi = Number(parts[2]);
        const q = questions[qi];
        if (!q) return false;
        const kind = parts[3];
        if (kind === "skip") {
          answers.set(qi, { id: q.id, selected: [] });
          await helpers.say?.(`⏭ Q${qi + 1} skipped`);
        } else if (kind === "custom") {
          helpers.expectText?.({ qi });
          await helpers.say?.(`✍️ send free text for Q${qi + 1}`);
          return "pending";
        } else if (kind === "opt") {
          const label = decodeURIComponent(parts.slice(4).join(":"));
          const prev = answers.get(qi);
          if (q.multiSelect && prev) {
            if (!prev.selected.includes(label)) prev.selected.push(label);
          } else {
            answers.set(qi, { id: q.id, selected: [label] });
          }
          await helpers.say?.(`✔ Q${qi + 1}: ${label.slice(0, 40)}`);
        } else {
          return false;
        }
        if (answers.size >= total) return { outcome: doneShape() };
        return "pending";
      },
      onTimeout: () => {
        tg.editCard?.(chatId, msgId, "⏭ questions timed out — skipped unanswered", []).catch?.(() => {});
        return doneShape();
      },
    });
    // Custom free-text arrives via decisions.feedText in index.js and lands
    // here through answerQuestionCustom() (resolves the waiter when all done).
    customCompleters.set(key, (qi, text) => {
      const q = questions[qi];
      if (!q) return false;
      answers.set(qi, { id: q.id, selected: [], custom: String(text).slice(0, 500) });
      if (answers.size >= total) {
        decisions.complete(key, doneShape());
        return true;
      }
      return "pending";
    });
    const downstream = Promise.resolve().then(
      () => next(),
      () => undefined,
    );
    try {
    const winner = await raceGate({ phone, downstream, log, label: `questions (${total})` });
    if (winner.source === "web") {
      try {
        await tg.editCard?.(chatId, msgId, "answered on web", []);
      } catch {}
      decisions.settle(key);
      return winner.outcome;
    }
    return winner.outcome;
    } finally {
      customCompleters.delete(key);
    }
  };
}

/** Feed free text into a pending question (index.js custom-text path). */
const customCompleters = new Map();
export function answerQuestionCustom(key, qi, text) {
  const fn = customCompleters.get(key);
  return fn ? fn(qi, text) : false;
}
