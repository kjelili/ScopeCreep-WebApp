/* ==========================================================================
   Thread grouping for cumulative scope-drift analysis (FR8).

   Survey basis: Q12/Q13 — practitioners report that scope implications are
   hard to track across long email threads and are missed in long chains;
   Q2 — creep originates in small, informal (cumulative) changes. Grouping
   is heuristic and transparent: an explicit `thread` column wins; else a
   normalised `subject` (Re:/Fwd: stripped); else one project-wide timeline.
   Ordering uses a `date` column when parseable, else upload order.

   Exposed as window.Threads (browser) and module.exports (Node tests).
   ========================================================================== */
"use strict";

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Threads = factory();
}(typeof self !== "undefined" ? self : this, function () {

  const RISK_W = { low: 1, moderate: 2, high: 3, extreme: 4 };

  /* Normalise a subject line into a thread key. */
  function normaliseSubject(s) {
    let t = String(s || "").trim();
    let prev;
    do { prev = t;
      t = t.replace(/^\s*(re|fwd?|fw|aw|sv)\s*(\[\d+\])?\s*[:\]]\s*/i, "");
    } while (t !== prev);
    return t.replace(/\s+/g, " ").trim().toLowerCase();
  }

  function threadKeyFor(row) {
    const explicit = row.thread || row.thread_id || row.Thread || row.THREAD;
    if (explicit && String(explicit).trim()) return String(explicit).trim();
    const subj = row.subject || row.Subject || row.SUBJECT;
    if (subj && normaliseSubject(subj)) return "subj:" + normaliseSubject(subj);
    return "__project__";
  }

  function parseDate(v) {
    if (!v) return null;
    const d = new Date(v);
    return isNaN(d.getTime()) ? null : d.getTime();
  }

  /* Group analysed results (each row: original columns + verdict fields)
     into ordered threads. Returns [{key, label, items:[...]}] with items in
     chronological (or upload) order. */
  function group(results) {
    const map = new Map();
    results.forEach((r) => {
      const key = threadKeyFor(r);
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(r);
    });
    const threads = [];
    for (const [key, items] of map) {
      const dated = items.map((r) => ({ r, t: parseDate(r.date || r.Date) }));
      const allDated = dated.every((x) => x.t !== null);
      const ordered = allDated
        ? dated.sort((a, b) => a.t - b.t).map((x) => x.r)
        : items.slice().sort((a, b) => a.index - b.index);
      const subj = items[0].subject || items[0].Subject;
      threads.push({
        key,
        label: key === "__project__" ? "Project timeline (no thread data)"
          : (subj ? String(subj).replace(/^\s*(re|fwd?|fw)\s*:\s*/i, "").trim()
                  : key),
        items: ordered,
      });
    }
    // largest threads first
    threads.sort((a, b) => b.items.length - a.items.length);
    return threads;
  }

  /* Severity-weighted drift index for a set of flagged items, plus a simple
     qualitative band. Deliberately transparent arithmetic — the LLM narrative
     interprets; this number just accumulates. */
  function driftIndex(items) {
    const flagged = items.filter((r) => r.scope_creep === "yes");
    const score = flagged.reduce(
      (s, r) => s + (RISK_W[r.risk_level] || 1), 0);
    const band = score === 0 ? "none"
      : score <= 2 ? "low" : score <= 5 ? "building" : "high";
    return { flagged: flagged.length, total: items.length, score, band };
  }

  /* Threads worth an aggregate LLM judgement: 2+ flagged items. */
  function driftCandidates(threads) {
    return threads.filter(
      (t) => t.items.filter((r) => r.scope_creep === "yes").length >= 2);
  }

  return { normaliseSubject, threadKeyFor, group, driftIndex, driftCandidates };
}));
