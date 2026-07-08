# Testing

## Running the suite

```bash
python -m pytest tests/ -v
```

43 tests, ~2 seconds, no network access and no API keys required. The
OpenAI SDK is never invoked in tests; everything runs against the
deterministic `DemoProvider` or pure functions.

## What is covered

**`tests/test_engine.py` (21)** — chunking (empty input, size ceiling,
overlap carry-over, pathological unbroken text, content preservation);
risk/verdict normalisation including the Extreme-collapse regression test;
tolerant JSON parsing; grounding verification (verbatim quote, fabricated
quote, "none", near-match); retrieval ordering and empty-document
behaviour; end-to-end analysis in demo mode (creep, no-creep, contained
errors); alert-threshold ladders; demo-provider determinism.

**`tests/test_extractors.py` (10)** — CSV encoding fallbacks (UTF-8,
latin-1), missing `email_body` column, empty-row skipping, extra-column
pass-through; unsupported file types; real sample PDF/DOCX/CSV extraction.

**`tests/test_api.py` (12)** — health; upload validation (type, size,
tiny text, missing column); unknown ids; OpenAI mode without key; invalid
phone rejection; a full demo run asserting verdicts per email, the
grounding fields on every row, the summary block and the CSV export;
job-not-found; sample listing; path-traversal protection on `/sample/*`;
static landing page.

## What is deliberately not covered

Live OpenAI calls (cost, non-determinism — evaluated separately in thesis
Chapter 6 against labelled datasets) and live Twilio delivery (needs real
credentials; `send_sms` degrades to a clear error tuple without them).

## Extending

Add engine behaviours as pure-function tests where possible. For new
endpoints, use `fastapi.testclient.TestClient` — see `test_api.py` for the
upload/poll/export pattern, including `_wait()` for job completion.
