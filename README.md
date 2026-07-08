# Scope Creep Detector — Web Application

RAG-grounded detection of scope creep in project email, checked against the
contracted scope baseline. This is the research artefact accompanying the PhD
thesis *Exploring ways to mitigate scope creep through the applications of
Artificial Intelligence*, rebuilt as a proper web application (FastAPI + a
responsive vanilla-JS frontend) to support practitioner testing and
validation. It replaces the earlier Streamlit prototype.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> — the landing page links to the app. Click
**Load sample project** and **Analyse emails** to see the complete workflow
in demo mode with the bundled hospital-project sample. No API key required.

To analyse with the real model, switch the engine to **OpenAI — live AI** in
the app and paste an API key (used per-run, never stored or logged).

## Optional: SMS alerts (Twilio)

```bash
cp .env.example .env      # then fill in your Twilio credentials
```

Alerts are de-duplicated per (recipient, email) pair and deliberately carry
no email content — only the risk level and a result reference (GDPR-aware by
design). Without Twilio configured, the app still shows alerts in the UI.

## Testing

```bash
python -m pytest tests/ -v            # backend: 46 tests, no network
node --test tests/scrubber.test.mjs   # privacy scrub: 17 tests
```

## Project layout

```
app/            FastAPI backend
  engine.py     RAG pipeline: chunking, retrieval, judgement, grounding check
  demo.py       offline deterministic provider (demo mode)
  extractors.py PDF/DOCX/TXT + CSV parsing with encoding fallbacks
  sms.py        Twilio alerts with de-duplication
  main.py       stateless HTTP API + static hosting (local + Vercel)
static/         landing page, app UI, client-side PII scrubber (no build step)
tests/          pytest suite (engine, extractors, API)
docs/           architecture, API, user guide, testing, build log, traceability
test_data/      sample scope document + 25 test emails
```

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | pipeline design, decisions, limitations |
| [docs/API.md](docs/API.md) | REST endpoints (also live at `/docs`) |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | step-by-step guide for testers |
| [docs/TESTING.md](docs/TESTING.md) | test strategy and how to extend it |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Vercel deployment, limits, custom domain |
| [docs/TRACEABILITY.md](docs/TRACEABILITY.md) | mapping to thesis requirements FR1–FR7 / NFR1–NFR6 |
| [docs/BUILD_LOG.md](docs/BUILD_LOG.md) | staged build record with verification evidence |

## Key changes from the Streamlit prototype

1. All four risk levels (Low/Moderate/High/Extreme) preserved end-to-end —
   the prototype collapsed Extreme into High.
2. Evidence grounding is verified, not assumed: every judgement carries a
   grounding score checked against the retrieved scope sections (FR6).
3. Reproducibility support: temperature 0, fixed seed, JSON response mode,
   and the responding model version logged per row (thesis §6.6.3).
4. SMS de-duplication actually wired in; alert bodies contain no email
   content; history lives outside the repo.
5. CSV encoding detection (UTF-8 → latin-1 fallback) instead of forced latin-1.
6. Demo mode for zero-cost practitioner evaluation.
7. A privacy scrub that runs in the browser: emails are parsed and
   anonymised locally with consistent pseudonyms ([PERSON-1], [ORG-1]),
   reviewed by the tester, and only then uploaded. The pseudonym mapping
   never leaves the device; results re-identify locally for display.
8. A 60+-test suite (pytest + node) and staged, documented build.

## Deployment

The API is stateless (the browser holds the session and requests analysis
in batches), so the app runs unchanged on a local server or on Vercel.
See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the step-by-step Vercel
guide, environment variables, platform limits and custom-domain setup.

## References

- FastAPI: <https://fastapi.tiangolo.com/>
- OpenAI API: <https://platform.openai.com/docs/api-reference>
- Structured outputs: <https://platform.openai.com/docs/guides/structured-outputs>
- Embeddings: <https://platform.openai.com/docs/guides/embeddings>
- Twilio Messaging: <https://www.twilio.com/docs/messaging/api>
- pypdf: <https://pypdf.readthedocs.io/> · python-docx: <https://python-docx.readthedocs.io/>
