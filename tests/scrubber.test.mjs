/* Node tests for the client-side PII scrubber.
   Run: node --test tests/scrubber.test.mjs */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Scrubber = require("../static/js/scrubber.js");

/* ------------------------------------------------------------- CSV parse */

test("csv: basic parse with extra columns", () => {
  const rows = Scrubber.parseCSV(
    "sender,email_body\nalice@x.com,Add a window please\n");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].email_body, "Add a window please");
  assert.equal(rows[0].sender, "alice@x.com");
});

test("csv: quoted fields with commas and escaped quotes", () => {
  const rows = Scrubber.parseCSV(
    'email_body\n"He said ""add it"", then left, quickly"\n');
  assert.equal(rows[0].email_body, 'He said "add it", then left, quickly');
});

test("csv: newline inside quoted field", () => {
  const rows = Scrubber.parseCSV('email_body\n"line one\nline two"\n');
  assert.equal(rows[0].email_body, "line one\nline two");
});

test("csv: missing email_body column throws", () => {
  assert.throws(() => Scrubber.parseCSV("a,b\n1,2\n"), /email_body/);
});

test("csv: skips empty bodies, CRLF endings", () => {
  const rows = Scrubber.parseCSV("email_body\r\n\r\nreal email\r\n");
  assert.equal(rows.length, 1);
});

/* -------------------------------------------------------------- patterns */

function scrubOne(text, dict) {
  const s = Scrubber.createSession(dict || []);
  return { s, ...s.scrub(text) };
}

test("scrub: email addresses", () => {
  const { text, hits } = scrubOne("Contact sarah.jones@acme.co.uk today");
  assert.match(text, /\[EMAIL-1\]/);
  assert.ok(!text.includes("sarah.jones"));
  assert.equal(hits[0].type, "EMAIL");
});

test("scrub: phone numbers, not money or years", () => {
  const { text } = scrubOne("Call +44 7911 123456 re the £150,000 budget for 2026");
  assert.match(text, /\[PHONE-1\]/);
  assert.ok(text.includes("£150,000"), "money must survive");
  assert.ok(text.includes("2026"), "years must survive");
});

test("scrub: URLs and postcodes", () => {
  const { text } = scrubOne("See https://portal.acme.com/rfi/42 near NE1 4ST");
  assert.match(text, /\[URL-1\]/);
  assert.match(text, /\[POSTCODE-1\]/);
});

test("scrub: greeting and sign-off names", () => {
  const { text } = scrubOne("Hi John, can we add sockets? Regards, Sarah Smith");
  assert.ok(!/John/.test(text), "greeting name removed");
  assert.ok(!/Sarah/.test(text), "sign-off name removed");
  assert.match(text, /\[PERSON-1\]/);
  assert.match(text, /\[PERSON-2\]/);
});

test("scrub: honorific names", () => {
  const { text } = scrubOne("Mr Adebayo confirmed the change with Dr Chen.");
  assert.ok(!text.includes("Adebayo") && !text.includes("Chen"));
});

test("scrub: greeting followed by lowercase word is not a name", () => {
  const { text } = scrubOne("hi there, quick question about the roof");
  assert.ok(!text.includes("[PERSON"), text);
});

test("scrub: dictionary terms, case-insensitive, multi-word", () => {
  const { text } = scrubOne(
    "Acme Construction agreed; ACME construction will price it for Riverside House.",
    [{ term: "Acme Construction", type: "ORG" },
     { term: "Riverside House", type: "PLACE" }]);
  assert.ok(!/acme/i.test(text));
  assert.match(text, /\[ORG-1\].*\[ORG-1\]/s, "same org -> same token");
  assert.match(text, /\[PLACE-1\]/);
});

test("scrub: pseudonyms are consistent across emails in a session", () => {
  const s = Scrubber.createSession([{ term: "John", type: "PERSON" }]);
  const a = s.scrub("John wants an extra door.");
  const b = s.scrub("Ask John about the door.");
  assert.match(a.text, /\[PERSON-1\]/);
  assert.match(b.text, /\[PERSON-1\]/, "same person -> same token");
});

test("scrub: scope-relevant content is untouched", () => {
  const { text } = scrubOne(
    "Please add two extra power outlets to level 3 and price the variation.");
  assert.equal(text,
    "Please add two extra power outlets to level 3 and price the variation.");
});

/* --------------------------------------------------- session round-trips */

test("reidentify: restores originals locally", () => {
  const s = Scrubber.createSession([{ term: "Acme Ltd", type: "ORG" }]);
  const { text } = s.scrub("Hi John, Acme Ltd wants sockets. Email j@a.com");
  const back = s.reidentify(text);
  assert.ok(back.includes("John") && back.includes("Acme Ltd")
    && back.includes("j@a.com"));
});

test("scrubRows + summary", () => {
  const s = Scrubber.createSession([]);
  const { rows, hits } = s.scrubRows([
    { email_body: "Hi Sarah, call 07911123456", sender: "x" },
    { email_body: "Nothing personal here." },
  ]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].sender, "x", "pass-through columns preserved");
  const sum = s.summary(hits);
  assert.ok(sum.total >= 2);
  assert.ok(sum.byType.PERSON >= 1 && sum.byType.PHONE >= 1);
  assert.equal(hits[1].length, 0);
});

test("reidentify: unknown tokens pass through unchanged", () => {
  const s = Scrubber.createSession([]);
  assert.equal(s.reidentify("[PERSON-99] said so"), "[PERSON-99] said so");
});
