import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { escapeHtml, parseAllowedUsers, splitMessage } from "../lib/util.js";

describe("allowlist", () => {
  it("parses numeric chat ids, drops usernames/empties", () => {
    assert.deepEqual(parseAllowedUsers("654191377, 987654321"), ["654191377", "987654321"]);
    assert.deepEqual(parseAllowedUsers("@user, ,abc"), []);
    assert.deepEqual(parseAllowedUsers(""), []);
  });
});

describe("renderer", () => {
  it("escapes html", () => {
    assert.equal(escapeHtml("<b>&"), "&lt;b&gt;&amp;");
  });
  it("splits long messages with suffix", () => {
    const chunks = splitMessage("x".repeat(5000));
    assert.equal(chunks.length, 2);
    assert.match(chunks[0], /\(1\/2\)$/);
  });
  it("never splits inside fences at the boundary", () => {
    const fence = "```\n" + "y".repeat(4090) + "\n```\ntail";
    const chunks = splitMessage(fence);
    assert.ok(chunks.length >= 1);
    assert.ok(!chunks[0].endsWith("(1/2)") || chunks[0].includes("```"));
  });
});
