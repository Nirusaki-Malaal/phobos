/**
 * Single-use decision waiters — routes Telegram callback_data + free text
 * to the approval/question gate awaiting them. Zero dependencies.
 *
 * waiter: {chatId, onData(data, helpers) -> 'pending' | {done, outcome} | false,
 *          onTimeout() -> outcome, timeoutMs, settled}
 * helpers: {say(text)} — lightweight ack edit/notice via injected notify fn.
 */
export function createDecisions({ notify }) {
  const waiters = new Map(); // key -> waiter
  const textExpect = new Map(); // chatId -> {key, qi}

  function clear(key) {
    const w = waiters.get(key);
    if (w?.timer) clearTimeout(w.timer);
    waiters.delete(key);
    for (const [chat, exp] of textExpect) {
      if (exp.key === key) textExpect.delete(chat);
    }
  }

  async function wait(key, waiter) {
    waiters.set(key, { ...waiter, settled: false });
    return new Promise((resolve) => {
      const w = waiters.get(key);
      w.resolve = (outcome, source) => {
        if (w.settled) return;
        w.settled = true;
        clear(key);
        resolve({ source, outcome });
      };
      w.timer = setTimeout(() => {
        if (w.settled) return;
        let outcome;
        try {
          outcome = w.onTimeout?.();
        } catch {
          outcome = undefined;
        }
        w.resolve(outcome, "timeout");
      }, waiter.timeoutMs);
      if (waiter.timer !== undefined) {
        // allow refresh (re-prompt extension)
        w.refresh = (ms) => {
          clearTimeout(w.timer);
          w.timer = setTimeout(() => {
            if (!w.settled) w.resolve(w.onTimeout?.(), "timeout");
          }, ms);
        };
      }
    });
  }

  /** Route a callback_data tap. Returns true when consumed. */
  async function feed(data, { answerCb } = {}) {
    const key = String(data ?? "").split(":")[1];
    const w = key && waiters.get(key);
    if (!w || w.settled) return false;
    const helpers = {
      say: (text) => notify?.(w.chatId, text),
      expectText: (exp) => textExpect.set(w.chatId, { key, ...exp }),
    };
    let r;
    try {
      r = await w.onData?.(data, helpers);
    } catch {
      return true;
    }
    if (r === "pending" || r === undefined) {
      try {
        await answerCb?.();
      } catch {}
      return true;
    }
    if (r === false) return false;
    try {
      await answerCb?.();
    } catch {}
    w.resolve(r.outcome, "phone");
    return true;
  }

  /** Route free text when a custom answer is expected. Returns true when consumed. */
  function feedText(chatId, text) {
    const exp = textExpect.get(String(chatId));
    if (!exp) return null;
    const w = waiters.get(exp.key);
    if (!w || w.settled) {
      textExpect.delete(String(chatId));
      return null;
    }
    textExpect.delete(String(chatId));
    return { key: exp.key, waiter: w, exp, text };
  }

  function settle(key) {
    const w = waiters.get(key);
    if (!w || w.settled) return;
    w.settled = true;
    clear(key);
  }

  /** Externally resolve a waiter (e.g. free-text custom answer). */
  function complete(key, outcome) {
    const w = waiters.get(key);
    if (!w || w.settled || typeof w.resolve !== "function") return false;
    w.resolve(outcome, "phone");
    return true;
  }

  return { wait, feed, feedText, settle, complete, has: (k) => waiters.has(k) };
}
