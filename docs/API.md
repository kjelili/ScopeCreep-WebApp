# API Reference

Interactive documentation is auto-generated at **`/docs`** (Swagger UI) and
**`/redoc`**. This file is the stable summary.

The API is **stateless**: nothing is stored server-side. Upload endpoints
return the parsed data to the client, and the client sends the relevant
parts back with each analysis batch. This is what makes the app
serverless-safe (see docs/DEPLOYMENT.md).

## Health

`GET /api/health` →
`{"status":"ok","version":"2.1.0","twilio_configured":false,
  "limits":{"upload_bytes":4194304,"emails":500,"batch":10}}`

## Parse scope baseline

`POST /api/scope` — multipart form, field `file` (PDF, DOCX or TXT, ≤4 MB)

```json
{"filename":"Scope Document.pdf","characters":10715,"count":28,
 "chunks":["…","…"],"preview":"…"}
```

Errors: `400` unsupported type / no extractable text / >400 sections,
`413` too large.

## Parse emails

`POST /api/emails` — multipart form, field `file`
(CSV with an `email_body` column; ≤500 emails, ≤4 MB)

```json
{"filename":"emails.csv","count":25,"rows":[{"email_body":"…","sender":"…"}]}
```

## Analyse a batch

`POST /api/analyze-batch` — JSON body:

```json
{
  "scope_chunks":     ["…"],
  "scope_embeddings": null,
  "emails":           [{"index":0,"email_body":"…"}],
  "mode":             "demo",   // demo | openai | anthropic | gemini
  "api_key":          "sk-…",
  "top_k":            3,
  "include_embeddings": false
}
```

Rules: ≤10 emails per batch; `api_key` required when `mode` is `"openai"`.
In OpenAI mode, set `include_embeddings: true` on the first batch — the
response includes `scope_embeddings`, which the client sends back on later
batches so the baseline is embedded only once.

Response:

```json
{"results":[
  {"index":0,"email_body":"…","scope_creep":"yes","risk_level":"high",
   "justification":"…","suggestion":"…","reference_scope_line":"…",
   "impact_analysis":"…","grounded":true,"grounding_score":1.0,
   "low_relevance":false,
   "retrieved":[{"text":"…","similarity":0.42}],
   "model":"gpt-4o-mini-2024-07-18","system_fingerprint":"fp_…"}],
 "scope_embeddings":[[…]]}
```

Alert eligibility and the results CSV are computed client-side
(`static/js/app.js`), keeping the endpoint stateless.

## Send SMS alerts

`POST /api/notify` — JSON body:

```json
{"phones":["+447911123456"],
 "items":[{"ref":"a1b2c3","risk_level":"high","grounded":true,"index":4}],
 "run_id":"…"}
```

Requires Twilio env vars on the server. Alert bodies are content-free
(risk + reference only). De-duplicated per (number, run\_id:ref),
best-effort persistent. Response: `{"outcomes":[{"index":4,"to":"+44…",
"status":"sent"}]}` — statuses: `sent`, `failed`, `skipped-duplicate`.

## Sample data

- `GET /api/sample-data` → names and URLs of the bundled sample files
- `GET /sample/{name}` → the file itself (path-traversal protected)
