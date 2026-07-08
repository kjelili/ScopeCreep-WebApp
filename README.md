# Scope Creep Detector

**Catch scope creep where it starts: in the email.**

A RAG-grounded web application that reads project emails against the
contracted scope baseline and flags potential scope creep early — with the
exact scope clause cited as evidence for every judgement.

🔗 **Live app:** <https://scope-creep-web-app.vercel.app>

> Research artefact accompanying the PhD thesis *Exploring ways to mitigate
> scope creep through the applications of Artificial Intelligence*.
> Built with FastAPI and a responsive vanilla-JS frontend; replaces the
> earlier Streamlit prototype.

---

## ✨ Features

- 📨 **Email-native detection** — analyses the channel where informal scope
  change actually happens, including messages from external stakeholders
- 🔍 **Semantic matching, not keywords** — embeddings + retrieval catch
  "we might be able to squeeze that in", not just the word "change"
- 📎 **Verified evidence** — every flag cites the scope clause it rests on,
  and an independent grounding check confirms the clause really exists
- 🚦 **Four-level risk grading** — Low / Moderate / High / Extreme, with a
  configurable alert threshold
- 🔒 **Privacy scrub in the browser** — emails are anonymised with
  consistent pseudonyms *before* upload; the name mapping never leaves
  the tester's device
- 📱 **SMS alerts (optional)** — de-duplicated, content-free Twilio
  notifications for high-risk flags
- 🧪 **Demo mode** — evaluate the complete workflow with bundled sample
  data; no API key, no cost
- 📥 **Audit-ready CSV export** — verdicts, justifications, evidence,
  grounding scores and model version

## 🚀 Quick start (local)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>, click **Load sample project**, then
**Analyse emails**. To use the real model, switch the engine to
**OpenAI — live AI** and paste an API key (used per-run, never stored).

## 🧪 Testing

```bash
python -m pytest tests/ -v            # backend: 48 tests, no network
node --test tests/scrubber.test.mjs   # privacy scrub: 17 tests
```

## 📁 Project layout

```
app/                    FastAPI backend
├── engine.py           RAG pipeline: chunking, retrieval, judgement, grounding
├── demo.py             offline deterministic provider (demo mode)
├── extractors.py       PDF / DOCX / TXT + CSV parsing
├── sms.py              Twilio alerts with de-duplication
└── main.py             stateless HTTP API + static hosting
static/                 landing page, app UI, client-side PII scrubber
tests/                  pytest + node test suites (65 tests)
docs/                   full documentation set
test_data/              sample scope document + 25 test emails
```

## 📚 Documentation

| Document | Contents |
|----------|----------|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | pipeline design, decisions, limitations |
| [API](docs/API.md) | REST endpoints (interactive version at `/docs`) |
| [USER_GUIDE](docs/USER_GUIDE.md) | step-by-step guide for testers |
| [DEPLOYMENT](docs/DEPLOYMENT.md) | Vercel deployment, limits, custom domain |
| [TESTING](docs/TESTING.md) | test strategy and how to extend it |
| [TRACEABILITY](docs/TRACEABILITY.md) | mapping to thesis requirements FR1–FR7 / NFR1–NFR6 |
| [BUILD_LOG](docs/BUILD_LOG.md) | staged build record with verification evidence |

## 🔒 Privacy by design

1. Email CSVs are **parsed in the browser** — raw text is never uploaded
2. Names, contacts, URLs and postcodes become consistent pseudonyms
   (`[PERSON-1]`, `[ORG-1]`) so the analysis stays coherent
3. A **mandatory review step** shows every redaction before anything leaves
   the device
4. Results **re-identify locally**; exports keep the pseudonyms
5. SMS alerts carry no email content; the OpenAI key is used per-run and
   never stored or logged

## ⚙️ How it works

1. **Upload the scope baseline** (PDF/DOCX/TXT) → sentence-aware chunks,
   embedded once (`text-embedding-3-small`)
2. **Upload project emails** (CSV) → parsed and anonymised in the browser
3. **Each email is compared** against its top-3 most relevant scope
   sections (cosine retrieval) and judged by GPT-4o-mini
   (temperature 0, seed, JSON mode)
4. **Every verdict is graded** for risk and its cited clause is
   independently verified against the baseline
5. **A human reviews** each flag — the tool recommends, it never decides

## ☁️ Deployment

The API is stateless (the browser holds the session and requests analysis
in batches), so the same code runs locally and on Vercel unchanged.
See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the full guide.

## 🔗 References

- [FastAPI](https://fastapi.tiangolo.com/) · [OpenAI API](https://platform.openai.com/docs/api-reference) · [Twilio Messaging](https://www.twilio.com/docs/messaging/api)
- [pypdf](https://pypdf.readthedocs.io/) · [python-docx](https://python-docx.readthedocs.io/)
