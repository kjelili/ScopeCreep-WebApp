/* Scope Creep Detector — app logic.
   Stateless API + client-side privacy layer: email CSVs are parsed and
   anonymised in the browser (scrubber.js); only scrubbed text is uploaded.
   The pseudonym mapping stays on this device, so results re-identify
   locally for display. */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = {
  scope: null,            // {filename, chunks:[...]}
  rawRows: null,          // parsed locally, never uploaded
  pendingRows: null,      // scrubbed (or raw if scrub off), awaiting approval
  emails: null,           // approved rows -> analysis input
  emailsName: "",
  session: null,          // scrubber session (token<->original map, local)
  scrubEnabled: true,
  dict: [],               // [{term,type}]
  scrubApproved: false,
  scopeEmbeddings: null,
  mode: "demo", model: "openai", threshold: "high",
  running: false, cancelled: false,
  runId: null,
  results: [], summary: null, smsOutcomes: [],
  threads: [], drift: {},   // drift: threadKey -> aggregate judgement
  filter: "all", search: "", showReal: true,
  reviews: {},          // index -> {verdict, risk, evidence, note, at} (local only)
  twilioConfigured: false,
  serverKey: false,
};

const BATCH = { demo: 25, openai: 4 };
const LADDER = {
  extreme: ["extreme"],
  high: ["high", "extreme"],
  moderate: ["moderate", "high", "extreme"],
};
const TOKEN_RE = /\[(?:PERSON|ORG|PLACE|EMAIL|PHONE|URL|POSTCODE)-\d+\]/g;

/* ------------------------------------------------------------- helpers */

function toast(msg, isErr = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail
      : detail ? JSON.stringify(detail) : `Request failed (${res.status})`);
  }
  return data;
}

function setStep(n, doneUpTo = n - 1) {
  $$(".stepper .st").forEach((el, i) => {
    el.classList.toggle("active", i === n - 1);
    el.classList.toggle("done", i < doneUpTo);
  });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* Render text for display: optionally re-identify locally, and highlight
   any remaining pseudonym tokens. */
function displayText(s) {
  let t = String(s ?? "");
  if (state.session && state.showReal) t = state.session.reidentify(t);
  t = esc(t);
  if (!(state.session && state.showReal)) {
    t = t.replace(TOKEN_RE, (tok) => `<mark class="pii">${tok}</mark>`);
  }
  return t;
}

async function refHash(text) {
  const buf = await crypto.subtle.digest("SHA-256",
    new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].slice(0, 8)
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

/* ------------------------------------------------------- scope upload */

function wireScopeDrop() {
  const drop = $("#drop-scope"), input = $("#file-scope");
  const send = async (file) => {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    drop.classList.remove("loaded");
    $("#meta-scope").textContent = "Uploading…";
    try {
      onScope(await api("/api/scope", { method: "POST", body: fd }));
      drop.classList.add("loaded");
    } catch (err) {
      $("#meta-scope").textContent = "";
      toast(err.message, true);
    }
    checkReady();
  };
  input.addEventListener("change", () => send(input.files[0]));
  ["dragover", "dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.toggle("dragover", ev === "dragover");
      if (ev === "drop") send(e.dataTransfer.files[0]);
    }));
}

function onScope(d) {
  state.scope = d;
  state.scopeEmbeddings = null;
  $("#meta-scope").textContent =
    `✓ ${d.filename} — ${d.count} scope sections (${d.characters.toLocaleString()} chars)`;
}

/* ------------------------------------------- emails: local parse + scrub */

function wireEmailsDrop() {
  const drop = $("#drop-emails"), input = $("#file-emails");
  const load = async (file) => {
    if (!file) return;
    try {
      const text = await file.text();
      acceptEmailCSV(text, file.name);
    } catch (err) { toast(err.message, true); }
  };
  input.addEventListener("change", () => load(input.files[0]));
  ["dragover", "dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.toggle("dragover", ev === "dragover");
      if (ev === "drop") load(e.dataTransfer.files[0]);
    }));
}

function acceptEmailCSV(text, name) {
  try {
    state.rawRows = Scrubber.parseCSV(text);
  } catch (err) { return toast(err.message, true); }
  state.emailsName = name;
  state.emails = null;
  state.scrubApproved = false;
  $("#drop-emails").classList.add("loaded");
  $("#meta-emails").textContent =
    `✓ ${name} — ${state.rawRows.length} emails parsed locally`;
  runScrub();
  checkReady();
}

function runScrub() {
  const panel = $("#panel-scrub");
  panel.style.display = "";
  state.scrubApproved = false;
  state.emails = null;

  if (!state.scrubEnabled) {
    state.session = null;
    state.pendingRows = state.rawRows;
    $("#scrub-summary").innerHTML =
      "⚠ Anonymisation is <b>off</b> — email text will be uploaded as-is.";
    $("#scrub-preview").innerHTML = "";
  } else {
    state.session = Scrubber.createSession(state.dict);
    const { rows, hits } = state.session.scrubRows(state.rawRows);
    state.pendingRows = rows;
    const sum = state.session.summary(hits);
    const parts = Object.entries(sum.byType)
      .map(([t, n]) => `${n} ${t.toLowerCase()}`).join(", ");
    $("#scrub-summary").innerHTML = sum.total
      ? `🔒 ${sum.total} redaction${sum.total === 1 ? "" : "s"} across ${state.rawRows.length} emails (${parts}) — ${sum.distinct} distinct pseudonyms.`
      : `No personal information detected by the patterns. Add project names below if any should be redacted.`;
    renderScrubPreview(rows);
  }
  $("#scrub-meta").textContent = "Awaiting your approval…";
  checkReady();
  setStep(2, 1);
}

function renderScrubPreview(rows) {
  const N = 8;
  const items = rows.slice(0, N).map((r, i) => `
    <div class="evidence-chunk" style="border-left-color:var(--accent)">
      <span class="sim">#${i + 1}</span><br>
      ${esc(r.email_body).replace(TOKEN_RE, (t) => `<mark class="pii">${t}</mark>`)}
    </div>`).join("");
  $("#scrub-preview").innerHTML = items +
    (rows.length > N
      ? `<p class="hint">…and ${rows.length - N} more (all scrubbed with the same rules).</p>`
      : "");
}

function renderDictChips() {
  $("#dict-chips").innerHTML = state.dict.map((d, i) =>
    `<button class="chip" data-di="${i}" title="Remove">
       ${esc(d.term)} · ${d.type.toLowerCase()} ✕</button>`).join("");
  $$("#dict-chips .chip").forEach((c) =>
    c.addEventListener("click", () => {
      state.dict.splice(+c.dataset.di, 1);
      renderDictChips();
      if (state.rawRows) runScrub();
    }));
}

$("#dict-add").addEventListener("click", () => {
  const term = $("#dict-term").value.trim();
  if (term.length < 2) return toast("Enter a name of at least 2 characters.", true);
  state.dict.push({ term, type: $("#dict-type").value });
  $("#dict-term").value = "";
  renderDictChips();
  if (state.rawRows) runScrub();
});
$("#dict-term").addEventListener("keydown",
  (e) => e.key === "Enter" && (e.preventDefault(), $("#dict-add").click()));

$("#scrub-on").addEventListener("click", () => setScrub(true));
$("#scrub-off").addEventListener("click", () => {
  if (confirm("Upload email text without anonymisation?")) setScrub(false);
});
function setScrub(on) {
  state.scrubEnabled = on;
  $("#scrub-on").classList.toggle("on", on);
  $("#scrub-off").classList.toggle("on", !on);
  if (state.rawRows) runScrub();
}

$("#btn-approve").addEventListener("click", () => {
  if (!state.pendingRows) return;
  state.scrubApproved = true;
  state.emails = { rows: state.pendingRows, count: state.pendingRows.length };
  $("#scrub-meta").textContent = state.scrubEnabled
    ? "Approved — only anonymised text will be uploaded."
    : "Approved — text will be uploaded without anonymisation.";
  setStep(3, 2);
  checkReady();
  toast("Emails approved. Choose settings and analyse.");
});

function checkReady() {
  const ready = state.scope && state.emails && !state.running;
  $("#btn-run").disabled = !ready;
  if (!state.running) {
    $("#run-meta").textContent = ready ? "Ready."
      : state.rawRows && !state.scrubApproved
        ? "Approve the privacy scrub first…"
        : "Waiting for documents…";
  }
}

/* --------------------------------------------------------- sample loader */

$("#btn-sample").addEventListener("click", async () => {
  try {
    toast("Loading the sample hospital project…");
    const listing = await api("/api/sample-data");
    const names = Object.keys(listing.files);
    const scopeName = names.find((n) => n.toLowerCase().includes("scope") && n.endsWith(".pdf"))
      || names.find((n) => n.endsWith(".docx"));
    const csvName = names.find((n) => n.endsWith(".csv"));
    if (!scopeName || !csvName) throw new Error("Sample files not found on the server.");

    const blob = await fetch(listing.files[scopeName]).then((r) => r.blob());
    const fd = new FormData();
    fd.append("file", new File([blob], scopeName));
    onScope(await api("/api/scope", { method: "POST", body: fd }));
    $("#drop-scope").classList.add("loaded");

    const csvText = await fetch(listing.files[csvName]).then((r) => r.text());
    acceptEmailCSV(csvText, csvName);
    toast("Sample loaded — review the privacy scrub, then analyse.");
  } catch (err) { toast(err.message, true); }
});

/* -------------------------------------------------------------- settings */

$("#mode-demo").addEventListener("click", () => setMode("demo"));
$("#mode-openai").addEventListener("click", () => setMode("openai"));
function setMode(mode) {
  state.mode = mode;
  $("#mode-demo").classList.toggle("on", mode === "demo");
  $("#mode-openai").classList.toggle("on", mode === "openai");
  $("#key-field").style.display = mode === "openai" ? "" : "none";
  $("#model-field").style.display = mode === "openai" ? "" : "none";
}

$("#model-select").addEventListener("change", (e) => {
  state.model = e.target.value;
});

["moderate", "high", "extreme"].forEach((t) =>
  $("#th-" + t).addEventListener("click", () => {
    state.threshold = t;
    ["moderate", "high", "extreme"].forEach((x) =>
      $("#th-" + x).classList.toggle("on", x === t));
  }));

/* ------------------------------------------------------------------ run */

$("#btn-run").addEventListener("click", runAnalysis);
$("#btn-cancel").addEventListener("click", () => {
  state.cancelled = true;
  $("#btn-cancel").disabled = true;
});

async function runAnalysis() {
  const apiKey = $("#api-key").value.trim();
  if (state.mode === "openai" && !apiKey && !state.serverKey) {
    return toast("Enter your OpenAI API key or switch to demo mode.", true);
  }
  const rows = state.emails.rows;
  const chunks = state.scope.chunks;
  const batchSize = BATCH[state.mode];

  state.running = true;
  state.cancelled = false;
  state.results = [];
  state.reviews = {};
  state.threads = []; state.drift = {};
  state.runId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  $("#btn-run").disabled = true;
  $("#btn-cancel").style.display = "";
  $("#btn-cancel").disabled = false;
  $("#bar").style.width = "0%";
  setStep(3, 2);

  try {
    for (let i = 0; i < rows.length && !state.cancelled; i += batchSize) {
      const slice = rows.slice(i, i + batchSize);
      const liveMode = state.mode === "demo" ? "demo" : state.model;
      const body = {
        scope_chunks: chunks,
        emails: slice.map((r, k) => ({ index: i + k, email_body: r.email_body })),
        mode: liveMode,
        top_k: 3,
      };
      if (state.mode === "openai") {
        if (apiKey) body.api_key = apiKey;
        if (state.scopeEmbeddings) body.scope_embeddings = state.scopeEmbeddings;
        else body.include_embeddings = true;
      }
      const data = await api("/api/analyze-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (data.scope_embeddings) state.scopeEmbeddings = data.scope_embeddings;

      for (const r of data.results) {
        const src = rows[r.index] || {};
        r.alert = r.scope_creep === "yes"
          && LADDER[state.threshold].includes(r.risk_level);
        for (const k of Object.keys(src)) {
          if (!(k in r)) r[k] = src[k];
        }
        state.results.push(r);
      }
      const done = Math.min(i + batchSize, rows.length);
      $("#bar").style.width = `${(100 * done) / rows.length}%`;
      $("#run-meta").textContent = `${done} / ${rows.length}`;
    }

    state.summary = summarise(state.results);
    await analyseDrift(apiKey);
    renderResults();
    setStep(4, 3);
    toast(state.cancelled
      ? `Cancelled — showing ${state.results.length} analysed emails.`
      : "Analysis complete.");
    await maybeNotify();
  } catch (err) {
    toast(err.message, true);
  } finally {
    state.running = false;
    $("#btn-cancel").style.display = "none";
    checkReady();
  }
}

function summarise(results) {
  const rc = { low: 0, moderate: 0, high: 0, extreme: 0 };
  let flagged = 0, grounded = 0, errors = 0, lowRel = 0;
  for (const r of results) {
    if (r.scope_creep === "yes") { flagged++; if (r.risk_level in rc) rc[r.risk_level]++; }
    if (r.scope_creep === "error") errors++;
    if (r.grounded) grounded++;
    if (r.low_relevance) lowRel++;
  }
  return { total: results.length, flagged, risk_counts: rc,
           grounded, errors, low_relevance: lowRel };
}

/* ------------------------------------------- cumulative drift (FR8) */

async function analyseDrift(apiKey) {
  state.threads = Threads.group(state.results);
  state.drift = {};
  const candidates = Threads.driftCandidates(state.threads);
  if (!candidates.length) return;
  const liveMode = state.mode === "demo" ? "demo" : state.model;
  try {
    for (let i = 0; i < candidates.length; i += 5) {
      const body = {
        mode: liveMode,
        threads: candidates.slice(i, i + 5).map((t) => ({
          key: t.key,
          items: t.items.slice(0, 20).map((r) => ({
            email_body: String(r.email_body).slice(0, 600),
            scope_creep: r.scope_creep, risk_level: r.risk_level,
            reference_scope_line: String(r.reference_scope_line || "none").slice(0, 200),
          })),
        })),
      };
      if (liveMode !== "demo" && apiKey) body.api_key = apiKey;
      const data = await api("/api/analyze-drift", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      for (const t of data.threads) state.drift[t.key] = t;
    }
  } catch (err) {
    toast("Drift analysis failed: " + err.message, true);
  }
}

const DOT_COLORS = { low: "#d1fae5", moderate: "#fde68a",
                     high: "#fecaca", extreme: "#7f1d1d" };

function renderDrift() {
  const sec = $("#drift-section"), list = $("#drift-list");
  const multi = state.threads.length > 1 ||
    (state.threads[0] && state.threads[0].key !== "__project__");
  const rows = state.threads.map((t) => {
    const idx = Threads.driftIndex(t.items);
    const agg = state.drift[t.key];
    if (idx.flagged === 0 && !agg) return "";
    const dots = t.items.map((r) => {
      const c = r.scope_creep === "yes"
        ? (DOT_COLORS[r.risk_level] || "#e2e8f0") : "#f1f5f9";
      const border = r.scope_creep === "yes" ? "#334155" : "#cbd5e1";
      return `<span class="drift-dot" data-i="${r.index}" title="Email #${r.index + 1}: ${r.scope_creep}${r.scope_creep === "yes" ? " / " + r.risk_level : ""}" style="background:${c};border-color:${border}"></span>`;
    }).join("");
    const aggBadge = agg
      ? (agg.cumulative_creep === "yes"
          ? badge("b-" + agg.cumulative_risk, `cumulative: ${agg.cumulative_risk}`)
          : badge("b-none", "no accumulation"))
      : badge("b-" + (idx.band === "none" ? "none" : idx.band === "low" ? "low" : "building"),
              `drift index ${idx.score}`);
    return `<div class="drift-card">
      <div class="drift-head">
        <span class="drift-title">${esc(t.label)} · ${idx.flagged}/${idx.total} flagged</span>
        <span>${aggBadge} ${badge("b-none", `index ${idx.score}`)}</span>
      </div>
      <div class="drift-strip">${dots}</div>
      ${agg && agg.narrative ? `<div class="drift-narrative">${esc(state.session && state.showReal ? state.session.reidentify(agg.narrative) : agg.narrative)}</div>` : ""}
      ${agg && agg.recommendation ? `<div class="drift-reco">→ ${esc(agg.recommendation)}</div>` : ""}
    </div>`;
  }).filter(Boolean);
  if (!rows.length) { sec.style.display = "none"; return; }
  sec.style.display = "";
  list.innerHTML = (multi ? "" :
    `<p class="hint">All emails treated as one timeline — add a subject or thread column for per-thread analysis.</p>`)
    + rows.join("");
  $$("#drift-list .drift-dot").forEach((d) =>
    d.addEventListener("click", () => openDrawer(+d.dataset.i)));
}

/* --------------------------------- self-calibration (measured precision) */

function calibration() {
  const reviewed = Object.entries(state.reviews);
  if (reviewed.length < 1) return null;
  let confirmedFlags = 0, reviewedFlags = 0, agree = 0;
  for (const [idx, rv] of reviewed) {
    const r = state.results.find((x) => x.index === +idx);
    if (!r) continue;
    if (rv.verdict === r.scope_creep) agree++;
    if (r.scope_creep === "yes") {
      reviewedFlags++;
      if (rv.verdict === "yes") confirmedFlags++;
    }
  }
  return { n: reviewed.length, agree,
    precision: reviewedFlags ? confirmedFlags / reviewedFlags : null,
    reviewedFlags };
}

/* --------------------------------------------- evidence pack (per item) */

function evidencePackHTML(r) {
  const rv = state.reviews[r.index];
  const t = state.threads.find((th) => th.items.some((x) => x.index === r.index));
  const agg = t ? state.drift[t.key] : null;
  const row = (k, v) => `<tr><th>${k}</th><td>${v}</td></tr>`;
  const evState = r.grounded
    ? (r.evidence_basis === "omission" ? "Boundary clause verified (creep by omission)" : "Verified in baseline")
    : "Not verified against baseline";
  return `<!doctype html><html><head><meta charset="utf-8">
<title>Scope evidence pack — email #${r.index + 1}</title>
<style>
 body{font-family:Georgia,serif;max-width:720px;margin:40px auto;color:#111;line-height:1.5}
 h1{font-size:20px;border-bottom:2px solid #1d4ed8;padding-bottom:8px}
 h2{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:#444;margin-top:24px}
 table{border-collapse:collapse;width:100%}
 th{text-align:left;width:200px;vertical-align:top;padding:6px 10px 6px 0;color:#555;font-weight:600}
 td{padding:6px 0}
 blockquote{border-left:3px solid #1d4ed8;margin:8px 0;padding:6px 12px;background:#f6f8fc}
 .foot{margin-top:32px;font-size:11px;color:#777;border-top:1px solid #ddd;padding-top:8px}
 @media print {.noprint{display:none}}
</style></head><body>
<h1>Scope-change evidence pack</h1>
<table>
${row("Generated", new Date().toLocaleString())}
${row("Analysis run", (state.runId || "").slice(0, 8))}
${row("Engine", esc(r.model || "demo"))}
${row("Scope baseline", esc(state.scope ? state.scope.filename : ""))}
</table>
<h2>Communication (anonymised)</h2>
<blockquote>${esc(r.email_body)}</blockquote>
<h2>AI assessment</h2>
<table>
${row("Verdict", r.scope_creep === "yes" ? "Potential scope creep" : "In scope")}
${row("Risk level", esc(r.risk_level))}
${row("Justification", esc(r.justification))}
${row("Suggested action", esc(r.suggestion))}
${row("Impact analysis", esc(r.impact_analysis))}
</table>
<h2>Evidence</h2>
<table>
${row("Cited scope clause", `<blockquote>${esc(r.reference_scope_line)}</blockquote>`)}
${row("Verification", `${evState} (grounding score ${r.grounding_score})`)}
</table>
${agg ? `<h2>Thread context (cumulative)</h2><table>
${row("Thread", esc(t.label))}
${row("Cumulative judgement", `${esc(agg.cumulative_creep)} — ${esc(agg.cumulative_risk)} risk`)}
${row("Pattern", esc(agg.narrative))}
</table>` : ""}
<h2>Project manager review</h2>
<table>
${rv ? row("Reviewer verdict", rv.verdict === "yes" ? "Confirmed as scope creep" : "Assessed as in scope")
     + row("Reviewer risk", esc(rv.risk))
     + row("Evidence assessment", esc(rv.evidence))
     + row("Note", esc(rv.note || "—"))
     + row("Reviewed at", new Date(rv.at).toLocaleString())
  : row("Status", "Not yet reviewed — this pack records the AI assessment only")}
</table>
<p class="foot">Generated by Scope Creep Detector (research artefact). Email content shown is the anonymised text as analysed; the AI assessment is advisory and the reviewer's judgement governs. Intended for submission into the project's existing change-control process.</p>
<p class="noprint"><button onclick="print()">Print / save as PDF</button></p>
</body></html>`;
}

function downloadEvidencePack(r) {
  const html = evidencePackHTML(r);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([html], { type: "text/html" }));
  a.download = `evidence_pack_email${r.index + 1}_${(state.runId || "run").slice(0, 8)}.html`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("Evidence pack downloaded — open it and print to PDF for your change-control file.");
}

/* ------------------------------------------------------------- SMS alerts */

async function maybeNotify() {
  const phones = $("#phones").value.split(/[,;]/).map((s) => s.trim()).filter(Boolean);
  const eligible = state.results.filter((r) => r.alert);
  state.smsOutcomes = [];
  if (!phones.length || !eligible.length) return renderSummaryCards();
  if (!state.twilioConfigured) {
    toast("SMS skipped: Twilio is not configured on the server.", true);
    return renderSummaryCards();
  }
  try {
    const items = [];
    for (const r of eligible) {
      items.push({ ref: await refHash(r.email_body), index: r.index,
                   risk_level: r.risk_level, grounded: !!r.grounded });
    }
    const data = await api("/api/notify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phones, items, run_id: state.runId }),
    });
    state.smsOutcomes = data.outcomes || [];
    const sent = state.smsOutcomes.filter((o) => o.status === "sent").length;
    const failed = state.smsOutcomes.filter((o) => o.status === "failed").length;
    toast(`SMS alerts: ${sent} sent${failed ? `, ${failed} failed` : ""}.`, failed > 0);
  } catch (err) {
    toast("SMS alerts failed: " + err.message, true);
  }
  renderSummaryCards();
}

/* -------------------------------------------------------------- results */

const RISK_ORDER = { extreme: 0, high: 1, moderate: 2, low: 3, unknown: 4 };

function renderResults() {
  $("#panel-results").style.display = "";
  $("#demo-flag").style.display = state.mode === "demo" ? "" : "none";
  const namesBtn = $("#btn-names");
  if (state.session && state.session.mapSize > 0) {
    namesBtn.style.display = "";
    updateNamesBtn();
  } else namesBtn.style.display = "none";
  renderSummaryCards();

  const rc = state.summary.risk_counts;
  const flagged = Math.max(state.summary.flagged, 1);
  const colors = { low: "var(--risk-low)", moderate: "#f59e0b",
                   high: "var(--risk-high)", extreme: "var(--risk-extreme)" };
  $("#riskbar").innerHTML = ["low", "moderate", "high", "extreme"]
    .map((r) => `<div style="width:${(100 * (rc[r] ?? 0)) / flagged}%;background:${colors[r]}" title="${r}: ${rc[r] ?? 0}"></div>`)
    .join("");

  renderRows();
  renderDrift();
}

function updateNamesBtn() {
  $("#btn-names").textContent = state.showReal
    ? "👁 Showing real names (local)"
    : "🔒 Showing pseudonyms";
  $("#btn-names").classList.toggle("on", !state.showReal);
}

$("#btn-names").addEventListener("click", () => {
  state.showReal = !state.showReal;
  updateNamesBtn();
  renderRows();
  closeDrawer();
  toast(state.showReal
    ? "Real names shown — re-identification happens only on this device."
    : "Pseudonyms shown — this is exactly what the server and AI saw.");
});

function renderSummaryCards() {
  const s = state.summary || {};
  const rc = s.risk_counts || {};
  const smsSent = state.smsOutcomes.filter((o) => o.status === "sent").length;
  const cal = calibration();
  $("#sumgrid").innerHTML = `
    <div class="sum"><b>${s.total ?? 0}</b><span>emails analysed</span></div>
    <div class="sum"><b>${s.flagged ?? 0}</b><span>flagged as scope creep</span></div>
    <div class="sum"><b style="color:var(--risk-high)">${(rc.high ?? 0) + (rc.extreme ?? 0)}</b><span>high / extreme risk</span></div>
    <div class="sum"><b style="color:var(--brand-ink)">${s.grounded ?? 0}</b><span>with verified evidence</span></div>
    <div class="sum"><b>${smsSent}</b><span>SMS alerts sent</span></div>
    <div class="sum"><b style="color:var(--ok)">${Object.keys(state.reviews).length}</b><span>reviewed by you</span></div>
    ${cal && cal.reviewedFlags >= 5 ? `<div class="sum"><b style="color:var(--brand-ink)">${Math.round(cal.precision * 100)}%</b><span>measured precision — ${cal.reviewedFlags} flags you checked</span></div>` : ""}`;
}

function visibleRows() {
  const q = state.search.toLowerCase();
  return state.results.filter((r) => {
    if (state.filter === "flagged" && r.scope_creep !== "yes") return false;
    if (state.filter === "high" && !["high", "extreme"].includes(r.risk_level)) return false;
    if (state.filter === "ungrounded" && (r.grounded || r.scope_creep !== "yes")) return false;
    if (state.filter === "alerts" && !r.alert) return false;
    if (state.filter === "unreviewed" && state.reviews[r.index]) return false;
    if (q) {
      const shown = state.session && state.showReal
        ? state.session.reidentify(r.email_body) : r.email_body;
      if (!shown.toLowerCase().includes(q)) return false;
    }
    return true;
  }).sort((a, b) =>
    (a.scope_creep === "yes" ? 0 : 1) - (b.scope_creep === "yes" ? 0 : 1)
    || RISK_ORDER[a.risk_level] - RISK_ORDER[b.risk_level]
    || a.index - b.index);
}

function badge(cls, text) { return `<span class="badge ${cls}">${text}</span>`; }

/* Evidence state: verified quotation / boundary clause (omission) / none */
function evidenceState(r) {
  if (r.scope_creep !== "yes") return null;
  const ref = (r.reference_scope_line || "").trim().toLowerCase();
  if (!ref || ref === "none") return { cls: "b-ungrounded", label: "no citation" };
  if (r.grounded && r.evidence_basis === "omission")
    return { cls: "b-omission", label: "boundary ✓" };
  if (r.grounded) return { cls: "b-grounded", label: "verified" };
  return { cls: "b-ungrounded", label: "unverified" };
}

function renderRows() {
  const rows = visibleRows();
  const head = `<div class="rrow head"><span>#</span><span>Email</span><span>Verdict</span><span>Risk</span><span>Evidence</span></div>`;
  $("#results").innerHTML = head + (rows.length ? rows.map((r) => `
    <div class="rrow" data-i="${r.index}" role="button" tabindex="0" aria-label="Open detail">
      <span class="idx">${r.index + 1}</span>
      <span class="body">${displayText(r.email_body)}</span>
      <span>${badge("b-" + r.scope_creep, r.scope_creep === "yes" ? "Scope creep" : r.scope_creep === "no" ? "In scope" : "Error")}</span>
      <span>${r.scope_creep === "yes" ? badge("b-" + r.risk_level, r.risk_level) : ""}</span>
      <span>${(() => { const e = evidenceState(r); return e ? badge(e.cls, e.label) : ""; })()}${state.reviews[r.index] ? " " + badge("b-reviewed", "PM ✓") : ""}</span>
    </div>`).join("")
    : `<div class="rrow" style="cursor:default"><span></span><span class="body" style="color:var(--muted)">Nothing matches this filter.</span></div>`);

  $$("#results .rrow[data-i]").forEach((el) => {
    const open = () => openDrawer(+el.dataset.i);
    el.addEventListener("click", open);
    el.addEventListener("keydown", (e) => e.key === "Enter" && open());
  });
}

$$(".filters .chip[data-f]").forEach((c) => c.addEventListener("click", () => {
  state.filter = c.dataset.f;
  $$(".filters .chip[data-f]").forEach((x) => x.classList.toggle("on", x === c));
  renderRows();
}));
$("#search").addEventListener("input", (e) => { state.search = e.target.value; renderRows(); });

/* --------------------------------------------------- client-side export */

$("#btn-export").addEventListener("click", () => {
  if (!state.results.length) return;
  const cols = ["index", "email_body", "scope_creep", "risk_level",
    "justification", "suggestion", "reference_scope_line", "evidence_basis",
    "grounded", "grounding_score", "low_relevance", "impact_analysis",
    "alert", "model",
    "pm_verdict", "pm_risk", "pm_evidence", "pm_note", "pm_reviewed_at"];
  const cell = (v) => {
    const s = String(v ?? "");
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const rowsOut = state.results.map((r) => {
    const rv = state.reviews[r.index] || {};
    const t = state.threads.find((th) => th.items.some((x) => x.index === r.index));
    const agg = t ? state.drift[t.key] : null;
    return { ...r,
             thread: t ? t.label : "",
             thread_cumulative_creep: agg ? agg.cumulative_creep : "",
             thread_cumulative_risk: agg ? agg.cumulative_risk : "",
             pm_verdict: rv.verdict || "", pm_risk: rv.risk || "",
             pm_evidence: rv.evidence || "", pm_note: rv.note || "",
             pm_reviewed_at: rv.at || "" };
  });
  const csv = [cols.join(",")]
    .concat(rowsOut.map((r) => cols.map((c) => cell(r[c])).join(",")))
    .join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `scope_creep_results_${(state.runId || "run").slice(0, 8)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  if (state.session && state.session.mapSize > 0) {
    toast("Export uses pseudonyms — exactly what was analysed. The name mapping stays on this device.");
  }
});

/* --------------------------------------------------------------- drawer */

function openDrawer(i) {
  const r = state.results.find((x) => x.index === i);
  if (!r) return;
  $("#d-title").textContent = `Email #${i + 1}`;
  const retrieved = (r.retrieved || []).map((c) =>
    `<div class="evidence-chunk"><span class="sim">similarity ${c.similarity}</span><br>${esc(c.text)}</div>`).join("");
  $("#d-body").innerHTML = `
    <div class="kv"><div class="k">Email</div><div class="v">${displayText(r.email_body)}</div></div>
    <div class="kv"><div class="k">Verdict</div><div class="v">
      ${badge("b-" + r.scope_creep, r.scope_creep)} ${r.scope_creep === "yes" ? badge("b-" + r.risk_level, r.risk_level + " risk") : ""}
      ${r.alert ? badge("b-high", "alert raised") : ""}
    </div></div>
    ${r.error ? `<div class="kv"><div class="k">Error</div><div class="v">${esc(r.error)}</div></div>` : ""}
    <div class="kv"><div class="k">Justification</div><div class="v">${displayText(r.justification) || "—"}</div></div>
    <div class="kv"><div class="k">Suggested action</div><div class="v">${displayText(r.suggestion) || "—"}</div></div>
    <div class="kv"><div class="k">Impact analysis</div><div class="v">${displayText(r.impact_analysis) || "—"}</div></div>
    <div class="kv"><div class="k">Cited scope clause ${(() => {
        if (r.grounded && r.evidence_basis === "omission")
          return `<span style="color:var(--brand-ink)">✓ boundary clause verified — creep by omission (score ${r.grounding_score})</span>`;
        if (r.grounded)
          return `<span style="color:var(--ok)">✓ verified in baseline (score ${r.grounding_score})</span>`;
        return `<span style="color:var(--danger)">✗ not verified (score ${r.grounding_score})</span>`;
      })()}</div>
      <div class="v">${esc(r.reference_scope_line)}</div></div>
    <div class="kv"><div class="k">Retrieved scope sections</div>${retrieved || "<div class='v'>—</div>"}</div>
    <div class="kv"><div class="k">Engine</div><div class="v">${esc(r.model || "—")}${r.low_relevance ? " · ⚠ low retrieval relevance" : ""}</div></div>
    ${reviewFormHTML(r)}`;
  wireReviewForm(r);
  $("#drawer").classList.add("open");
  $("#veil").classList.add("open");
}

/* ---------------------------------------------- PM review (FR7, local) */

function reviewFormHTML(r) {
  const rv = state.reviews[r.index] || {};
  const verdict = rv.verdict || r.scope_creep;
  const risk = rv.risk || r.risk_level;
  const seg = (id, opts, cur) => `<div class="seg" id="${id}">` + opts.map(([v, lab]) =>
    `<button type="button" data-v="${v}" class="${v === cur ? "on" : ""}">${lab}</button>`).join("") + "</div>";
  return `
    <div class="kv" style="border-top:2px solid var(--line); padding-top:var(--s4)">
      <div class="k">Project manager review — your judgement, recorded in the export</div>
      <div class="switch-row" style="margin:var(--s3) 0; gap:var(--s3)">
        ${seg("rv-verdict", [["yes", "Scope creep"], ["no", "In scope"]], verdict)}
        ${seg("rv-risk", [["low", "Low"], ["moderate", "Mod"], ["high", "High"], ["extreme", "Extreme"]], risk)}
      </div>
      <div class="switch-row" style="margin-bottom:var(--s3)">
        ${seg("rv-evidence", [["confirmed", "Evidence correct"], ["rejected", "Evidence wrong"], ["unsure", "Unsure"]], rv.evidence || "unsure")}
      </div>
      <input type="text" id="rv-note" placeholder="Reviewer note (optional)" value="${esc(rv.note || "")}">
      <div class="runbar" style="margin-top:var(--s3)">
        <button class="btn btn-primary btn-sm" id="rv-save">Save review</button>
        <button class="btn btn-ghost btn-sm" id="rv-pack" type="button">📄 Evidence pack</button>
        ${rv.at ? `<button class="btn btn-ghost btn-sm" id="rv-clear">Clear</button>` : ""}
        <span class="run-meta" id="rv-status">${rv.at ? "Reviewed " + new Date(rv.at).toLocaleString() : "Not reviewed yet"}</span>
      </div>
    </div>`;
}

function wireReviewForm(r) {
  const pick = (id) => {
    const el = $("#" + id);
    if (!el) return;
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      [...el.children].forEach((x) => x.classList.toggle("on", x === b));
    });
  };
  ["rv-verdict", "rv-risk", "rv-evidence"].forEach(pick);
  const cur = (id) => { const b = $("#" + id + " .on"); return b ? b.dataset.v : ""; };
  const save = $("#rv-save");
  if (save) save.addEventListener("click", () => {
    state.reviews[r.index] = {
      verdict: cur("rv-verdict"), risk: cur("rv-risk"),
      evidence: cur("rv-evidence"),
      note: $("#rv-note").value.trim(),
      at: new Date().toISOString(),
    };
    renderResults();
    closeDrawer();
    toast(`Review saved for email #${r.index + 1} — it stays on this device and goes into your CSV export.`);
  });
  const pack = $("#rv-pack");
  if (pack) pack.addEventListener("click", () => downloadEvidencePack(r));
  const clear = $("#rv-clear");
  if (clear) clear.addEventListener("click", () => {
    delete state.reviews[r.index];
    renderResults();
    closeDrawer();
    toast(`Review cleared for email #${r.index + 1}.`);
  });
}

function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#veil").classList.remove("open");
}
$("#d-close").addEventListener("click", closeDrawer);
$("#veil").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());

/* ----------------------------------------------------------------- init */

wireScopeDrop();
wireEmailsDrop();
renderDictChips();
setStep(1, 0);
api("/api/health")
  .then((h) => {
    state.twilioConfigured = !!h.twilio_configured;
    state.serverKey = !!h.server_openai_key;
    const sel = $("#model-select");
    if (sel && h.models) {
      if (h.models.anthropic) sel.insertAdjacentHTML("beforeend",
        '<option value="anthropic">Claude Haiku (Anthropic)</option>');
      if (h.models.gemini) sel.insertAdjacentHTML("beforeend",
        '<option value="gemini">Gemini Flash (Google)</option>');
    }
    if (state.serverKey) {
      $("#api-key").placeholder = "Optional — this deployment provides a key";
    }
  })
  .catch(() => {});
