# Architecture

## Overview

```
 Browser (static/)                    FastAPI (app/main.py)
 ┌──────────────────┐   multipart    ┌──────────────────────────┐
 │ landing + app UI │ ─────────────► │ /api/scope  /api/emails  │
 │ (vanilla JS)     │                │        │                 │
 │ polls job status │ ◄───────────── │ /api/jobs/{id}           │
 └──────────────────┘    JSON        │        │                 │
                                     │  JobManager (thread)     │
                                     │        │                 │
                                     │  engine.py pipeline      │
                                     │  ┌────────────────────┐  │
                                     │  │ chunk → embed →    │  │
                                     │  │ retrieve → judge → │  │
                                     │  │ verify grounding → │  │
                                     │  │ grade risk → alert │  │
                                     │  └────────────────────┘  │
                                     │     │            │       │
                                     │  OpenAI or    sms.py     │
                                     │  DemoProvider (Twilio)   │
                                     └──────────────────────────┘
```

## Pipeline (thesis Chapter 5 mapping)

| Stage | Module | Thesis § |
|---|---|---|
| Scope extraction (PDF/DOCX/TXT) | `extractors.py` | 5.2 |
| Sentence-aware chunking, ~500 chars / 55 overlap | `engine.chunk_text` | 5.2.1 |
| Embedding, `text-embedding-3-small`, batched once per document | `engine.ScopeIndex` | 5.2.2 |
| Cosine retrieval, top-3 | `engine.ScopeIndex.retrieve` | 5.4 |
| Judgement, `gpt-4o-mini`, temperature 0, seed 42, JSON mode | `engine.OpenAIProvider` | 5.5 |
| Structured output schema | `engine.Judgement` | 5.5.2 |
| Grounding verification | `engine.verify_grounding` | 5.5.3 |
| Risk grading, 4 levels + configurable threshold | `engine.alert_eligible` | 5.6 |
| SMS alerting + manual review UI | `sms.py`, frontend | 5.7 |

## Design decisions

**Provider injection.** The engine takes any object satisfying the
`Provider` protocol. `OpenAIProvider` is production; `DemoProvider` is a
deterministic offline heuristic. This keeps the entire test suite off the
network and gives practitioners a zero-cost evaluation path.

**Grounding as verification, not trust.** The LLM is asked to quote its
evidence verbatim; `verify_grounding` then independently confirms the quote
against the retrieved chunks (substring or fuzzy match ≥ 0.55). The result
is exposed per row (`grounded`, `grounding_score`) and aggregated in the
summary. This converts FR6 from an instruction into a measured property.

**Reproducibility.** Temperature 0, fixed seed, JSON response format, and
per-row logging of `model` and `system_fingerprint` give Chapter 6's
run-to-run variance analysis its raw material.

**In-memory state.** Uploads, jobs and results live in process memory.
Deliberate: single-researcher pilot tool, no accounts, no database to
secure, restart wipes everything. Consequences: run one worker; a restart
loses results (export CSV first). A multi-tenant deployment would replace
the dict stores with a database and the thread with a task queue — the
provider/engine layer would not change.

**Privacy (NFR3).** The OpenAI key is accepted per request, kept in memory
for the job, never logged or persisted. SMS bodies contain no email content.
SMS de-dup history lives in `~/.scopecreep/` (configurable via
`SCOPEAPP_DATA_DIR`), never in the repository.

**Client-side privacy layer.** Email CSVs are parsed in the browser
(`static/js/scrubber.js`), and detected personal information — email
addresses, phone numbers, URLs, postcodes, greeting/sign-off and honorific
names, plus a user-maintained project dictionary — is replaced with
consistent pseudonyms before upload. Consistency matters twice over: the
LLM still reads coherent text ("[PERSON-1] asked us to add extra outlets"
flags exactly as well), and the token→original mapping, which never leaves
the device, lets the results view re-identify locally. A mandatory review
step precedes any upload because pattern-based detection is deliberately
conservative rather than complete; the tester remains the last check.
Exports contain pseudonyms — exactly what the server and model saw.

## Limitations (documented for the thesis)

- Demo mode is a heuristic; its verdicts are illustrative, and the UI
  watermarks them as such.
- No authentication — deploy behind a reverse proxy with access control
  for anything beyond localhost testing.
- Retrieval quality depends on scope document text quality; scanned PDFs
  need OCR first (the API returns a clear error in that case).
- Embedding cache is per-process; a restart re-embeds.
- The PII scrubber is pattern+dictionary based, not a full NER model; the
  review step exists precisely because automatic detection is incomplete.
  The scope document is parsed server-side (PDF extraction), so its text
  does reach the server — noted for ethics documentation.
