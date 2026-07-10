# Build Log

Staged rebuild of the Streamlit prototype as a web application. Each step
was verified stable before the next began.

## Step 1 — Core engine + unit tests

Built `engine.py` (chunking, retrieval, judgement, grounding verification,
risk grading), `demo.py` (deterministic offline provider) and
`extractors.py` (PDF/DOCX/TXT + CSV with encoding fallbacks). All review
corrections applied at this layer: four risk levels, grounding score,
JSON mode, temperature 0 + seed, retries, similarity floor.

*Verification:* 31 unit tests written first against the engine and
extractors; **31/31 passed**, including regression tests for the
Extreme-collapse bug and fabricated-evidence detection. One test was
corrected during the run (it assumed 3 retrieved chunks where the short
test fixture yields 1).

## Step 2 — FastAPI backend

Built `main.py` (uploads, analysis, polling, cancel, CSV export, sample
data, static hosting), `jobs.py` (threaded job manager with progress) and
`sms.py` (Twilio with persistent dedup, content-free alert bodies).

*Verification:* 12 API integration tests added (**43/43 total passing**).
The invalid-phone test caught a real bug — unparseable numbers were
silently dropped instead of rejected with 400 — fixed and re-tested.
Live server boot verified; real sample PDF extracted to 28 chunks; full
demo analysis of the 25 sample emails completed with 16 flagged, 0 errors,
and a well-formed CSV export.

## Step 3 — Frontend

Landing page (`index.html`) and application (`app.html`, `js/app.js`)
on a shared design system (`css/styles.css`): Inter as the single
typeface, 4-px spacing scale, high-contrast palette, scroll-reveal and
drawer transitions that respect `prefers-reduced-motion`, and responsive
layouts — the results table collapses to cards under 760 px, touch targets
are ≥44 px, and the drawer is full-width on phones.

*Verification:* JS syntax-checked with `node --check`; both pages verified
to reference their assets; served pages checked over HTTP.

## Step 4 — End-to-end verification

One scripted pass against a live server: landing page, app page, CSS, JS
and Swagger UI all served (200 + content checks); sample files listed and
fetched through the same endpoints the UI uses; full demo analysis run;
every result row checked against the schema the detail drawer renders;
CSV export row-count verified. **All checks passed.** Full pytest suite
re-run: **43/43.**

## Step 5 — Documentation

README, ARCHITECTURE, API, USER_GUIDE, TESTING, TRACEABILITY (requirements
FR1–FR7 / NFR1–NFR6 mapped to code and tests) and this log.

## Step 6 — Serverless adaptation (Vercel)

Refactored the API to be fully stateless so the app can run on Vercel's
Python runtime: upload endpoints return parsed data to the client; a new
`/api/analyze-batch` endpoint analyses up to 10 emails per request against
client-supplied scope chunks (with an embeddings round-trip in OpenAI mode
so the baseline embeds once); `/api/notify` sends deduplicated,
content-free SMS after a run. The browser orchestrates batches, computes
the summary and alert eligibility, and generates the CSV export locally.
`jobs.py` (threaded job manager) was retired. Added `vercel.json`
(maxDuration 60), a `pyproject.toml` entrypoint declaration, and
docs/DEPLOYMENT.md. Upload cap reduced to 4 MB (platform body limit).

*Verification:* 46/46 tests passing (15 new API tests); live E2E of the
batched flow on the sample project reproduced the pre-refactor results
exactly (25 analysed, 16 flagged, 16 grounded, 3 high risk); notify
correctly rejects when Twilio is unconfigured; all pages and /docs served.

## Step 7 — Client-side privacy scrub (PII anonymisation)

Added a browser-side privacy layer so raw email text never leaves the
tester's device: local CSV parsing, pattern + dictionary PII detection
(emails, phones, URLs, postcodes, greeting/sign-off/honorific names,
user-added project terms), consistent pseudonyms per session, a mandatory
review step with highlighted redactions, and local re-identification in
the results view (with a toggle showing exactly what the server saw).
Exports keep pseudonyms. The /api/emails endpoint is retained for
programmatic use but the UI no longer sends raw email text to it.

*Verification:* 17 node tests for the scrubber (CSV edge cases, each
detector, money/year false-positive guards, cross-email consistency,
re-identification round-trip; one sign-off case-sensitivity bug caught and
fixed); full pytest suite still 46/46; scrub round-trip smoke test passed;
end-to-end boot with the new UI verified.

## Step 8 — PM review layer + boundary-clause evidence (v2.2.0)

Driven by the first live production run (25 emails, gpt-4o-mini): 22 of 24
creep verdicts cited "none" as evidence because most scope creep is defined
by absence from the baseline, which the quotation-based grounding check
scored as unverified. Two changes: (1) the prompt now asks the model to
quote the BOUNDARY clause for out-of-scope requests and declare an
`evidence_basis` (conflict / omission / none), giving three evidence states
in the UI — verified, boundary ✓ (omission), unverified/no citation;
(2) a project-manager review layer (FR7 made concrete): per-row confirm or
overturn of the verdict, risk override, evidence confirmation and a note,
all held locally in the browser, with the CSV export carrying the AI
columns untouched plus pm_verdict / pm_risk / pm_evidence / pm_note /
pm_reviewed_at — a paired AI-vs-practitioner judgement dataset for the
thesis evaluation.

*Verification:* 51 pytest + 17 node tests passing; JS syntax-checked;
live click-through pending post-deploy.

## Step 9 — Multi-model comparison (v2.3.0, RQ1)

Added Claude Haiku (Anthropic SDK) and Gemini Flash (REST, no extra SDK)
as comparator judgement models, selectable from a dropdown that appears
only for models whose server keys are configured. Methodological control:
retrieval is identical in every live mode — OpenAI embeddings, same
chunks, same top-3 — so verdict differences are attributable to the
judgement model alone (embed/judge provider split in the API). Model
names are env-overridable; temperature 0 everywhere; OpenAI alone
supports a seed (footnoted for the reproducibility analysis). Justified
against RQ1 ("are AI models effective…"), recorded here as a
requirements change driven by the research questions rather than
feature drift.

*Verification:* 54 pytest + 17 node tests, including a provider-split
test proving the embedder never judges and the judge never embeds, and
clear 400s when comparator keys are absent.

## Step 10 — Ablation kit (v2.4.0, §6.5)

Added the no-retrieval ablation condition (top_k=0 judges with no scope
context; skips embedding work), scripts/ablation.py (runs demo/noret/k1/
full/k5 per model against the deployed API and writes precision, recall,
F1, accuracy, flag rate, grounding rate and evidence-basis counts to
summary.csv plus per-condition row files), a 30-email labelled benchmark
(test_data/labelled_test_emails.csv — 25 sample emails + 5 in-scope
additions; labels to be reviewed by the researcher per §6.3), and
docs/ABLATION.md. The demo heuristic doubles as the §6.5.1 keyword
baseline.

*Verification:* 55 pytest + 17 node tests, including the top_k=0 path
(empty retrieval, no embeddings returned).

## Step 11 — Repository split: practitioner app vs evaluation instrument

The evaluation tooling (scripts/ablation.py, the labelled benchmark and
docs/ABLATION.md) moved to a separate, version-pinned repository
(ScopeCreep-Evaluation, frozen at v2.4.0) used to reproduce the Chapter 6
results for supervisors and examiners. This repository remains the
practitioner-facing proof-of-concept and continues to evolve with pilot
feedback. The API's top_k>=0 capability is retained here (four lines,
tested, unreachable from the UI) so the two codebases stay behaviourally
identical at the split point.

## Known constraints carried forward

In-memory state (single worker, export before restart); no authentication
(pilot tool — put access control in front of any shared deployment); demo
verdicts are heuristic and watermarked as such.
