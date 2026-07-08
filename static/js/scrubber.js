/* ==========================================================================
   Client-side PII scrubber.

   Runs entirely in the browser: raw email text is parsed, anonymised and
   reviewed locally, and only the scrubbed text is ever uploaded. Detected
   entities are replaced with CONSISTENT pseudonyms ([PERSON-1], [ORG-1] …)
   so the analysis stays coherent across emails, and the token↔original
   mapping never leaves the machine — the results view can re-identify
   locally for display.

   Detection = patterns (emails, phones, URLs, postcodes, greeting and
   sign-off names, honorifics) + a user-maintained dictionary of project
   names, companies and places. Pattern matching is deliberately
   conservative; the mandatory review step exists because no automatic
   scrubber is complete.

   Exposed as window.Scrubber (browser) and module.exports (Node tests).
   ========================================================================== */
"use strict";

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Scrubber = factory();
}(typeof self !== "undefined" ? self : this, function () {

  /* ------------------------------------------------------------ CSV parse
     RFC 4180: quoted fields, escaped quotes, commas/newlines in quotes. */

  function parseCSV(text) {
    const rows = [];
    let field = "", row = [], inQ = false;
    const s = String(text || "").replace(/^﻿/, "");
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (inQ) {
        if (c === '"') {
          if (s[i + 1] === '"') { field += '"'; i++; }
          else inQ = false;
        } else field += c;
      } else if (c === '"') inQ = true;
      else if (c === ",") { row.push(field); field = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && s[i + 1] === "\n") i++;
        row.push(field); field = "";
        if (row.length > 1 || row[0] !== "") rows.push(row);
        row = [];
      } else field += c;
    }
    row.push(field);
    if (row.length > 1 || row[0] !== "") rows.push(row);
    if (!rows.length) throw new Error("CSV appears to be empty.");

    const header = rows[0].map((h) => h.trim());
    const lower = header.map((h) => h.toLowerCase());
    if (!lower.includes("email_body")) {
      throw new Error("CSV must contain an 'email_body' column. Found: "
        + header.join(", "));
    }
    const bodyIdx = lower.indexOf("email_body");
    const out = [];
    for (const r of rows.slice(1)) {
      const body = (r[bodyIdx] || "").trim();
      if (!body) continue;
      const obj = { email_body: body };
      header.forEach((h, i) => { if (i !== bodyIdx && h) obj[h] = r[i] || ""; });
      out.push(obj);
    }
    if (!out.length) throw new Error("No non-empty email bodies found in the CSV.");
    return out;
  }

  /* -------------------------------------------------------------- patterns
     Order matters: URLs and emails first (they contain digits and dots that
     other patterns could partially match). */

  const GREET = "(?:hi|hello|dear|hey|good\\s+morning|good\\s+afternoon)";
  const SIGN = "(?:kind\\s+regards|best\\s+regards|warm\\s+regards|regards|"
    + "many\\s+thanks|thanks|thank\\s+you|cheers|best\\s+wishes|sincerely|best)";
  const NAME = "([A-Z][a-z]{1,20}(?:\\s+[A-Z][a-z]{1,20})?)";

  const PATTERNS = [
    { type: "URL", re: /\bhttps?:\/\/[^\s<>"')\]]+|(?<![\w@.])www\.[^\s<>"')\]]+/gi },
    { type: "EMAIL", re: /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g },
    { type: "PHONE",
      re: /(?<![\d£$€.,])(?:\+?\d[\d\s().-]{7,15}\d)(?![\d%])/g,
      validate: (m) => {
        const digits = m.replace(/\D/g, "");
        return digits.length >= 9 && digits.length <= 15
          && !/^(19|20)\d{6}$/.test(digits);   // not a date-like string
      } },
    { type: "POSTCODE", re: /\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b/g },
    { type: "PERSON",
      re: new RegExp("\\b(?:Mr|Mrs|Ms|Dr|Prof|Miss)\\.?\\s+" + NAME, "g"),
      group: 1 },
    { type: "PERSON",
      re: new RegExp("\\b" + GREET + "[\\s,]+" + NAME + "\\b", "gi"),
      group: 1,
      validate: (m) => /^[A-Z]/.test(m) },   // title-case names only
    { type: "PERSON",
      re: new RegExp("\\b" + SIGN + "\\s*[,;.!-]+\\s*" + NAME + "\\b", "gi"),
      group: 1,
      validate: (m) => /^[A-Z][a-z]/.test(m) },   // title-case names only
  ];

  /* --------------------------------------------------------- Scrub session
     One session per run keeps pseudonyms consistent across all emails. */

  function createSession(dictionary) {
    // dictionary: [{term:"Acme Ltd", type:"ORG"}, {term:"John", type:"PERSON"}]
    const forward = new Map();   // normalised original -> token
    const reverse = new Map();   // token -> original (first casing seen)
    const counters = {};

    function tokenFor(original, type) {
      const key = type + ":" + original.toLowerCase().replace(/\s+/g, " ").trim();
      if (forward.has(key)) return forward.get(key);
      counters[type] = (counters[type] || 0) + 1;
      const token = `[${type}-${counters[type]}]`;
      forward.set(key, token);
      reverse.set(token, original);
      return token;
    }

    const dict = (dictionary || [])
      .filter((d) => d.term && d.term.trim().length >= 2)
      .map((d) => ({
        type: ["PERSON", "ORG", "PLACE"].includes(d.type) ? d.type : "ORG",
        re: new RegExp("\\b" + d.term.trim()
          .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
          .replace(/\s+/g, "\\s+") + "\\b", "gi"),
      }));

    function scrub(text) {
      let out = String(text || "");
      const hits = [];

      const apply = (re, type, group, validate) => {
        out = out.replace(re, (...args) => {
          const full = args[0];
          const captured = group ? args[group] : full;
          if (!captured || (validate && !validate(captured))) return full;
          const token = tokenFor(captured, type);
          hits.push({ type, original: captured, token });
          return group ? full.replace(captured, token) : token;
        });
      };

      for (const d of dict) apply(d.re, d.type, 0, null);
      for (const p of PATTERNS) apply(p.re, p.type, p.group, p.validate);
      return { text: out, hits };
    }

    function scrubRows(rows) {
      const scrubbed = [];
      const allHits = [];
      for (const row of rows) {
        const { text, hits } = scrub(row.email_body);
        scrubbed.push({ ...row, email_body: text });
        allHits.push(hits);
      }
      return { rows: scrubbed, hits: allHits };
    }

    function reidentify(text) {
      return String(text || "").replace(
        /\[(?:PERSON|ORG|PLACE|EMAIL|PHONE|URL|POSTCODE)-\d+\]/g,
        (tok) => reverse.get(tok) || tok);
    }

    function summary(allHits) {
      const byType = {};
      let total = 0;
      for (const hits of allHits) {
        for (const h of hits) { byType[h.type] = (byType[h.type] || 0) + 1; total++; }
      }
      return { total, byType, distinct: reverse.size };
    }

    return { scrub, scrubRows, reidentify, summary,
             get mapSize() { return reverse.size; } };
  }

  return { parseCSV, createSession };
}));
