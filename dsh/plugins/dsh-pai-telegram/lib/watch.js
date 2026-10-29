/**
 * Turn watcher — ctx.on('session/event') fan-out for one bound session.
 * Container-only. Callbacks: onDraft(text), onDone({text, reason, ms}),
 * onApprovalAsked(info). Caller owns Telegram send/edit + throttle.
 */
const INTEREST = new Set(["turn/start", "assistant/message", "turn/end", "agent/error", "approval/asked"]);

export function assistantText(event) {
  const blocks = event?.data?.message?.content ?? [];
  return blocks
    .filter((b) => b?.type === "text")
    .map((b) => b?.text ?? "")
    .join("");
}

export function attachWatcher(ctx, session, hooks) {
  const startedAt = Date.now();
  let draft = "";
  let done = false;
  const firstSeq = typeof session?.seq === "number" ? session.seq : 0;
  const stop =
    typeof ctx.on === "function"
      ? ctx.on("session/event", (target, event) => {
          if (done || target !== session) return;
          if (typeof event?.seq === "number" && event.seq < firstSeq) return;
          if (!event || typeof event.type !== "string" || !INTEREST.has(event.type)) return;
          try {
            if (event.type === "turn/start") {
              draft = "";
              hooks.onStart?.();
            } else if (event.type === "assistant/message") {
              const t = assistantText(event);
              if (t && t !== draft) {
                draft = t;
                hooks.onDraft?.(draft);
              }
            } else if (event.type === "turn/end") {
              done = true;
              hooks.onDone?.({ text: draft, reason: event?.data?.reason, ms: Date.now() - startedAt });
            } else if (event.type === "agent/error") {
              done = true;
              hooks.onError?.(event?.data?.error ?? event?.data ?? "agent error", Date.now() - startedAt);
            } else if (event.type === "approval/asked") {
              hooks.onApprovalAsked?.(event?.data ?? {});
            }
          } catch {}
        })
      : () => {};
  return {
    stop: () => {
      done = true;
      try {
        typeof stop === "function" && stop();
      } catch {}
    },
  };
}
