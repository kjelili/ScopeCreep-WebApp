import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const Threads = require("../static/js/threads.js");

test("subject normalisation strips reply/forward prefixes recursively", () => {
  assert.equal(Threads.normaliseSubject("RE: Fwd: RE: Pump room layout "),
    "pump room layout");
  assert.equal(Threads.normaliseSubject("FW[2]: costs"), "costs");
});

test("explicit thread column wins over subject", () => {
  assert.equal(Threads.threadKeyFor({ thread: "T-9", subject: "Re: x" }), "T-9");
});

test("grouping by normalised subject; fallback single timeline", () => {
  const rows = [
    { index: 0, subject: "Re: Roof", scope_creep: "yes", risk_level: "high" },
    { index: 1, subject: "Roof", scope_creep: "yes", risk_level: "moderate" },
    { index: 2, subject: "Invoices", scope_creep: "no", risk_level: "low" },
  ];
  const g = Threads.group(rows);
  assert.equal(g.length, 2);
  assert.equal(g[0].items.length, 2);           // Roof thread, largest first
  const bare = Threads.group([{ index: 0 }, { index: 1 }]);
  assert.equal(bare.length, 1);
  assert.match(bare[0].label, /Project timeline/);
});

test("date ordering when all rows dated; upload order otherwise", () => {
  const g = Threads.group([
    { index: 5, subject: "a", date: "2026-03-02" },
    { index: 1, subject: "a", date: "2026-03-01" },
  ]);
  assert.equal(g[0].items[0].index, 1);
  const g2 = Threads.group([
    { index: 5, subject: "b", date: "not a date" },
    { index: 1, subject: "b", date: "2026-03-01" },
  ]);
  assert.equal(g2[0].items[0].index, 1); // falls back to index order
});

test("drift index weights severity and bands sensibly", () => {
  const items = [
    { scope_creep: "yes", risk_level: "moderate" },
    { scope_creep: "yes", risk_level: "high" },
    { scope_creep: "no", risk_level: "low" },
  ];
  const d = Threads.driftIndex(items);
  assert.deepEqual([d.flagged, d.total, d.score, d.band], [2, 3, 5, "building"]);
  assert.equal(Threads.driftIndex([{ scope_creep: "no" }]).band, "none");
});

test("driftCandidates needs 2+ flagged", () => {
  const t = Threads.group([
    { index: 0, subject: "a", scope_creep: "yes", risk_level: "low" },
    { index: 1, subject: "a", scope_creep: "no", risk_level: "low" },
    { index: 2, subject: "b", scope_creep: "yes", risk_level: "low" },
    { index: 3, subject: "b", scope_creep: "yes", risk_level: "low" },
  ]);
  const c = Threads.driftCandidates(t);
  assert.equal(c.length, 1);
  assert.equal(c[0].items[0].subject, "b");
});
