# Deployment — Vercel

The app is stateless by design: the browser holds the working session
(scope chunks, embeddings, email rows) and requests analysis in small
batches, so it runs correctly on serverless infrastructure. The same code
runs locally with `uvicorn app.main:app --reload`.

## 1. Push to a fresh GitHub repository

```bash
cd ScopeCreep-WebApp
git init && git add . && git commit -m "Scope Creep Detector v2.1 (web app)"
git remote add origin https://github.com/<you>/scopecreep-detector.git
git push -u origin main
```

Use a **new** repository. Do not push into the old `ScopeCreep-Detector`
repo without purging its history first — it contains real phone numbers in
`sms_history.json` and its commit history.

## 2. Import into Vercel

1. <https://vercel.com/new> → import the repository.
2. Framework preset: Vercel detects FastAPI automatically — it looks for
   the `app` instance declared in `pyproject.toml` (`app = "app.main:app"`).
   No build command needed; dependencies install from `requirements.txt`.
3. Deploy. You get `https://<project>.vercel.app`.

Or from the CLI: `npm i -g vercel && vercel deploy` (CLI ≥ 48.1.8;
`vercel dev` runs the same setup locally).

## 3. Environment variables (optional — SMS alerts)

Project → Settings → Environment Variables:

| Name | Value |
|---|---|
| `OPENAI_API_KEY` | server-held OpenAI key — used when a tester leaves the key field blank |
| `TWILIO_ACCOUNT_SID` | from the Twilio console |
| `TWILIO_AUTH_TOKEN` | from the Twilio console |
| `TWILIO_PHONE_NUMBER` | your Twilio number, E.164 |
| `SCOPEAPP_DATA_DIR` | `/tmp/scopecreep` |

Note on `SCOPEAPP_DATA_DIR`: serverless filesystems are ephemeral, so the
SMS de-duplication ledger only persists per warm instance. The client also
de-duplicates per run, so in practice repeats are rare; a shared store
(e.g. Vercel KV/Redis) would make it strict if ever needed.

## 4. Platform limits that shaped the design

| Limit | Consequence in the app |
|---|---|
| ~4.5 MB request body | uploads capped at 4 MB; batches capped at 10 emails |
| function `maxDuration` | set to 60 s in `vercel.json`; OpenAI batches of 4 finish well inside it |
| no shared memory between invocations | no server session: chunks/embeddings live in the browser and travel with each batch |
| no background threads | the browser is the orchestrator; progress is per-batch |

If a live OpenAI batch ever times out, lower the frontend batch size
(`BATCH.openai` in `static/js/app.js`).

## 5. Test the deployment

1. `https://<project>.vercel.app/api/health` → `{"status":"ok", …}`
2. Landing page loads; **Launch the app**.
3. **Load sample project** → **Analyse emails** (demo mode) → 25 results,
   16 flagged, export CSV works.
4. OpenAI mode with a key: watch the first batch return embeddings
   (slightly slower), later batches faster.
5. `/docs` serves the interactive API reference.

## 6. Custom domain (when ready to go live)

1. Buy the domain (Vercel Domains, or any registrar).
2. Project → Settings → Domains → Add → follow the DNS instructions
   (CNAME `cname.vercel-dns.com` or Vercel nameservers).
3. TLS certificates are provisioned automatically.

## 7. Before opening it to project professionals

- **Access control.** There is no authentication. For a pilot, enable
  Vercel's Password Protection / Deployment Protection, or front it with
  an identity layer. Do not run it fully public with SMS configured.
- **Terms + privacy note.** Testers will upload real project text; a short
  participant-information page (you already have ethics wording in the
  thesis) linked from the landing page is appropriate.
- **Server-held OpenAI key (optional).** Currently testers paste their own
  key. For a smoother pilot you could set `OPENAI_API_KEY` in Vercel and
  fall back to it server-side — one small change in `app/main.py`
  (`_provider`) — but then add access control first, or strangers spend
  your credits.
- **Static via CDN (optional).** Moving `static/**` to `public/**` lets
  Vercel's CDN serve the frontend without invoking the function. Purely an
  optimisation; the app works as-is.
