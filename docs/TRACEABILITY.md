# Requirements Traceability

Mapping from the thesis design requirements (Chapter 4, §4.3) to their
implementation and tests. FR = functional, NFR = non-functional.

| Req | Requirement (abridged) | Implementation | Verified by |
|---|---|---|---|
| FR1 | Ingest email streams with content and metadata | `extractors.parse_email_csv` (extra columns pass through); `/api/emails` | `test_csv_extra_columns_pass_through`, `test_real_email_csv` |
| FR2 | Machine-readable scope baseline from contract artefacts | `extractors.extract_scope_text`; `engine.ScopeIndex` (chunk + embed once) | `test_real_pdf_extracts_text`, `test_scope_index_*` |
| FR3 | Semantic comparison, not keyword matching | embeddings + cosine retrieval (`ScopeIndex.retrieve`), LLM judgement | `test_scope_index_retrieval_ranks_relevant_chunk_first`, E2E demo run |
| FR4 | Flag deviations early, pre-change-request | whole pipeline operates on raw email, independent of change-control state | E2E demo run (informal “can we add…” emails flagged) |
| FR5 | Risk grading + configurable alert threshold | 4 levels in `normalize_risk`; ladders in `alert_eligible`; threshold in UI/API | `test_normalize_risk_keeps_four_levels`, `test_alert_threshold_ladders` |
| FR6 | Every judgement carries its baseline evidence | `reference_scope_line` + independent `verify_grounding` (score + flag per row) | `test_grounding_*` (4 tests), API schema check in E2E |
| FR7 | Human review, never autonomous action | app surfaces flags for review; no automated downstream action exists | design property; UI review drawer |
| FR8 | Detect cumulative scope drift across message sequences (Q2, Q6, Q12, Q13) | thread grouping (`threads.js`), drift index, `/api/analyze-drift` aggregate judgement, drift timeline UI | `tests/threads.test.mjs` (6), `test_analyse_thread_*`, `test_analyze_drift_*` |
| NFR1 | Explainability & traceability | justification, evidence clause, retrieved chunks + similarities, grounding score exposed per row | drawer render + `test_full_demo_run_and_export` |
| NFR2 | Reliability & consistency | temperature 0, seed, JSON mode, retries; model version logged per row | `test_parse_json_*`, `test_demo_provider_is_deterministic`; Chapter 6 measures live variance |
| NFR3 | Privacy & data protection | client-side PII scrub with review before upload (scrubber.js); key per-run in memory; SMS bodies content-free; no persistence of uploads | 17 node tests in `tests/scrubber.test.mjs`; `sms.alert_message` |
| NFR4 | Integration/complementarity | standalone web app + documented REST API for PMIS integration | `docs/API.md`; `/docs` OpenAPI schema |
| NFR5 | Practicality at volume | batched embedding, background jobs, progress, cancellation, 500-email guard | job E2E, `test_full_demo_run_and_export` |
| NFR6 | Coverage across organisational boundaries | operates on email exports — any sender, internal or external | FR1 pass-through of sender column |

## Corrections traced to the code review

| Issue found in prototype | Fix | Where |
|---|---|---|
| Real phone numbers committed to public repo | history file moved to user data dir; `.gitignore` covers it | `sms.py`, `.gitignore` |
| Extreme risk collapsed into High | four-level normalisation with alias map | `engine.normalize_risk` |
| Evidence never verified (FR6 gap) | independent grounding verification with score | `engine.verify_grounding` |
| temperature 0.2, no seed, no JSON mode, model unlogged | 0.0 + seed 42 + `response_format=json_object` + per-row model/fingerprint | `engine.OpenAIProvider` |
| SMS dedup dead code; repeat alerts on re-run | persistent `SmsHistory` ledger, wired into the job loop | `sms.py`, `jobs.py` |
| Email snippets sent over SMS | content-free alert bodies | `sms.alert_message` |
| Forced latin-1 CSV decoding | utf-8 → utf-8-sig → latin-1 fallback | `extractors._decode` |
| No similarity floor on retrieval | `low_relevance` flag surfaced in results | `engine.analyse_email` |
| No tests | 63-test suite (46 pytest + 17 node) | `tests/` |
| Raw email content uploaded as-is | browser-side anonymisation with consistent pseudonyms, mandatory review, local re-identification | `static/js/scrubber.js`, `static/js/app.js` |
