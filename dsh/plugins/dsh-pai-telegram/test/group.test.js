import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { groupAdmission, groupAddressed, botStreakTrips } from "../lib/util.js";

const LISTS = { allowedUsers: ["1"], groupUsers: ["2"], groupChats: ["-100"] };

describe("groupAdmission", () => {
  it("owner may use any chat", () => {
    assert.deepEqual(groupAdmission({ fromId: "1", chatId: "-999", ...LISTS }), { ok: true, role: "owner" });
  });
  it("member may use only listed chats", () => {
    assert.deepEqual(groupAdmission({ fromId: "2", chatId: "-100", ...LISTS }), { ok: true, role: "member" });
    assert.deepEqual(groupAdmission({ fromId: "2", chatId: "-999", ...LISTS }), { ok: false, role: null });
  });
  it("strangers are dropped", () => {
    assert.deepEqual(groupAdmission({ fromId: "3", chatId: "-100", ...LISTS }), { ok: false, role: null });
    assert.deepEqual(groupAdmission({ fromId: "", chatId: "-100", ...LISTS }), { ok: false, role: null });
  });
});

describe("groupAddressed", () => {
  it("commands always address", () => {
    assert.deepEqual(groupAddressed({ text: "/status", username: "b" }), { text: "/status", addressed: true });
  });
  it("mentions address and strip", () => {
    assert.deepEqual(groupAddressed({ text: "hey @b what is this?", username: "b" }), { text: "hey what is this?", addressed: true });
  });
  it("plain chatter ignored", () => {
    assert.equal(groupAddressed({ text: "hello all", username: "b" }), null);
    assert.equal(groupAddressed({ text: "", username: "b" }), null);
  });
  it("reply-to-bot addresses", () => {
    assert.deepEqual(groupAddressed({ text: "and then?", username: "b", isReplyToBot: true }), { text: "and then?", addressed: true });
  });
  it("mention-only message ignored", () => {
    assert.equal(groupAddressed({ text: "@b", username: "b" }), null);
  });
});

describe("botStreakTrips", () => {
  it("trips at the limit, resets below", () => {
    assert.equal(botStreakTrips(4), false);
    assert.equal(botStreakTrips(5), true);
    assert.equal(botStreakTrips(6), true);
  });
});
