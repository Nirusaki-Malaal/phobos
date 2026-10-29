import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createDecisions } from "../lib/decision.js";
import { createApprovalHandler, createQuestionHandler, answerQuestionCustom } from "../lib/gate.js";

const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));

describe("decisions", () => {
  it("routes callback data to the waiter and resolves", async () => {
    const d = createDecisions({});
    const p = d.wait("k1", { chatId: "1", timeoutMs: 500, onData: (data) => (data === "ap:k1:allow" ? { outcome: "allowed-once" } : false) });
    const consumed = await d.feed("ap:k1:allow", {});
    assert.equal(consumed, true);
    assert.deepEqual(await p, { source: "phone", outcome: "allowed-once" });
  });

  it("ignores unknown keys and double-settles", async () => {
    const d = createDecisions({});
    assert.equal(await d.feed("ap:nope:allow", {}), false);
    const p = d.wait("k2", { chatId: "1", timeoutMs: 30, onData: () => ({ outcome: "x" }) });
    await d.feed("ap:k2:allow", {});
    assert.equal(await d.feed("ap:k2:allow", {}), false);
    assert.deepEqual(await p, { source: "phone", outcome: "x" });
  });

  it("times out fail-closed", async () => {
    const d = createDecisions({});
    const r = await d.wait("k3", { chatId: "1", timeoutMs: 20, onTimeout: () => "rejected" });
    assert.deepEqual(r, { source: "timeout", outcome: "rejected" });
  });

  it("routes free text to custom expectation", async () => {
    const d = createDecisions({ notify: () => Promise.resolve() });
    const p = d.wait("k4", {
      chatId: "7",
      timeoutMs: 500,
      onData: (data, h) => {
        if (data === "q:k4:0:custom") {
          h.expectText({ qi: 0 });
          return "pending";
        }
        return false;
      },
    });
    await d.feed("q:k4:0:custom", {});
    const routed = d.feedText("7", "hello");
    assert.ok(routed && routed.exp.qi === 0);
    d.complete("k4", "done-custom");
    assert.deepEqual(await p, { source: "phone", outcome: "done-custom" });
  });
});

describe("approval gate", () => {
  it("vetoes downstream on phone approve, falls through when unbound", async () => {
    const cards = [];
    const decisions = createDecisions({});
    const tg = {
      sendCard: async (c, t, b) => {
        cards.push({ c, t, b });
        return 1;
      },
      editCard: async () => {},
    };
    const h = createApprovalHandler({ sessionChat: () => "1", decisions, tg, log: { info: () => {} }, timeoutMs: 500 });
    const req = { agent: { session: { id: "s" } }, toolName: "bash", reason: "run tests" };
    const p = h(req, () => new Promise(() => {})); // downstream never settles: phone must win
    await tick();
    await decisions.feed("ap:xxx:allow", { answerCb: async () => {} }); // wrong key ignored
    const key = cards[0].b[0][0].data.split(":")[1];
    await decisions.feed(`ap:${key}:allow`, { answerCb: async () => {} });
    assert.equal(await p, "allowed-once");

    let nextCalled = false;
    const h2 = createApprovalHandler({ sessionChat: () => null, decisions, tg, log: { info: () => {} }, timeoutMs: 50 });
    const r2 = await h2(req, () => {
      nextCalled = true;
      return Promise.resolve("rejected");
    });
    assert.equal(nextCalled, true);
    assert.equal(r2, "rejected");
  });
});

describe("question gate custom", () => {
  it("completes via answerQuestionCustom when all answered", async () => {
    const decisions = createDecisions({ notify: async () => {} });
    let sentButtons = null;
    const tg = {
      sendCard: async (c, t, b) => {
        sentButtons = b;
        return 1;
      },
      editCard: async () => {},
    };
    const h = createQuestionHandler({ sessionChat: () => "1", decisions, tg, log: { info: () => {} }, timeoutMs: 1000 });
    const req = { agent: { session: {} }, questions: [{ id: "q1", question: "pick?", options: [{ label: "a" }] }] };
    const p = h(req, () => new Promise(() => {})); // downstream never settles: phone must win
    await tick(10);
    const customBtn = sentButtons.flat().find((btn) => btn.data.includes(":custom"));
    assert.ok(customBtn);
    const key = customBtn.data.split(":")[1];
    await decisions.feed(customBtn.data, { answerCb: async () => {} });
    assert.equal(answerQuestionCustom(key, 0, "my free text"), true);
    const out = await p;
    assert.deepEqual(out, { answers: [{ id: "q1", selected: [], custom: "my free text" }] });
  });

  it("fills skips on timeout", async () => {
    const decisions = createDecisions({});
    const tg = { sendCard: async () => 1, editCard: async () => {} };
    const h = createQuestionHandler({ sessionChat: () => "1", decisions, tg, log: { info: () => {} }, timeoutMs: 20 });
    const req = { agent: { session: {} }, questions: [{ id: "q1", question: "pick?", options: [{ label: "a" }] }] };
    const out = await h(req, () => new Promise(() => {})); // downstream never settles
    assert.deepEqual(out, { answers: [{ id: "q1", selected: [] }] });
  });
});
