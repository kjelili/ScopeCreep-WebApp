"""
Scope Creep Detector — FastAPI application (stateless / serverless-safe).

Runs identically under a local server and on Vercel:

  local:   uvicorn app.main:app --reload
  vercel:  the FastAPI `app` below is the function entrypoint
           (declared in pyproject.toml [project.scripts])

Serverless constraints shape the API design:
  * No server-side session state. The browser holds the scope chunks,
    embeddings and email rows, and requests analysis in small batches.
  * Request bodies stay under Vercel's ~4.5 MB limit (uploads capped at 4 MB,
    batches capped at 10 emails).
  * No background threads; every request completes its own work.

Security notes (deliberate for a research prototype):
  * The OpenAI key is accepted per-request, used for that request only,
    and never logged or persisted (NFR3).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import engine as eng
from . import extractors
from . import sms as sms_mod
from .demo import DemoProvider

app = FastAPI(
    title="Scope Creep Detector API",
    version="2.3.0",
    description=(
        "Stateless RAG-based detection of scope creep in project email, "
        "grounded in the contractual scope baseline. Research artefact "
        "accompanying the thesis 'Exploring ways to mitigate scope creep "
        "through the applications of Artificial Intelligence'."),
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"],
)

MAX_UPLOAD = 4 * 1024 * 1024   # 4 MB — inside Vercel's 4.5 MB body limit
MAX_EMAILS = 500               # per uploaded CSV
MAX_BATCH = 10                 # emails per analyze-batch request
MAX_CHUNKS = 400               # scope sections per request


# ----------------------------------------------------------------- models

class EmailItem(BaseModel):
    index: int
    email_body: str


class AnalyzeBatchRequest(BaseModel):
    scope_chunks: list[str] = Field(..., min_length=1, max_length=MAX_CHUNKS)
    scope_embeddings: Optional[list[list[float]]] = None
    emails: list[EmailItem] = Field(..., min_length=1, max_length=MAX_BATCH)
    mode: str = Field("demo", pattern="^(demo|openai|anthropic|gemini)$")
    api_key: Optional[str] = None
    top_k: int = Field(3, ge=1, le=10)
    include_embeddings: bool = False


class NotifyItem(BaseModel):
    ref: str            # client-side stable reference (dedup key)
    risk_level: str
    grounded: bool = False
    index: int = 0


class NotifyRequest(BaseModel):
    phones: list[str] = Field(..., min_length=1, max_length=10)
    items: list[NotifyItem] = Field(..., min_length=1, max_length=100)
    run_id: str = ""


# ---------------------------------------------------------------- helpers

async def _read_limited(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(
            413, "File exceeds the 4 MB limit (a hosting constraint). "
                 "Trim the document or split the CSV.")
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    return data


def _providers(mode: str, api_key: Optional[str]):
    """Returns (embed_provider, judge_provider). Retrieval embeddings are
    ALWAYS OpenAI in live modes, so cross-model comparisons (RQ1) isolate
    the judgement model. Comparator keys are server-held only."""
    if mode == "demo":
        d = DemoProvider()
        return d, d
    okey = (api_key or os.getenv("OPENAI_API_KEY") or "").strip()
    if not okey:
        raise HTTPException(
            400, "Live modes need an OpenAI key for retrieval embeddings — "
                 "paste one in the app, or configure OPENAI_API_KEY.")
    # validate all required keys BEFORE constructing any client, so the
    # tester gets the precise missing-key message
    ckey = None
    if mode == "anthropic":
        ckey = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not ckey:
            raise HTTPException(
                400, "Claude mode is not available: ANTHROPIC_API_KEY is "
                     "not configured on the server.")
    elif mode == "gemini":
        ckey = (os.getenv("GEMINI_API_KEY") or "").strip()
        if not ckey:
            raise HTTPException(
                400, "Gemini mode is not available: GEMINI_API_KEY is not "
                     "configured on the server.")
    oai = eng.OpenAIProvider(okey)
    if mode == "openai":
        return oai, oai
    if mode == "anthropic":
        return oai, eng.AnthropicProvider(
            ckey, os.getenv("SCOPEAPP_ANTHROPIC_MODEL", "claude-haiku-4-5"))
    return oai, eng.GeminiProvider(
        ckey, os.getenv("SCOPEAPP_GEMINI_MODEL", "gemini-2.5-flash"))


# --------------------------------------------------------------- endpoints

@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version,
            "twilio_configured": sms_mod.twilio_configured(),
            "server_openai_key": bool(os.getenv("OPENAI_API_KEY")),
            "models": {
                "openai": True,   # via pasted or server key
                "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
                "gemini": bool(os.getenv("GEMINI_API_KEY")),
            },
            "limits": {"upload_bytes": MAX_UPLOAD, "emails": MAX_EMAILS,
                       "batch": MAX_BATCH}}


@app.post("/api/scope")
async def upload_scope(file: UploadFile = File(...)):
    """Extract and chunk the scope baseline (PDF, DOCX or TXT). FR2.
    Stateless: the chunks are returned to the client, which sends them
    back with each analysis batch."""
    data = await _read_limited(file)
    try:
        text = extractors.extract_scope_text(file.filename, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    text = text.strip()
    if len(text) < 50:
        raise HTTPException(
            400, "Could not extract meaningful text from the document. "
                 "If it is a scanned PDF, run OCR first.")
    chunks = eng.chunk_text(text)
    if len(chunks) > MAX_CHUNKS:
        raise HTTPException(
            400, f"Scope document produced {len(chunks)} sections; the "
                 f"limit is {MAX_CHUNKS}. Upload the relevant schedule "
                 "rather than the full contract.")
    return {"filename": file.filename, "characters": len(text),
            "count": len(chunks), "chunks": chunks,
            "preview": text[:400]}


@app.post("/api/emails")
async def upload_emails(file: UploadFile = File(...)):
    """Parse the email CSV (requires an 'email_body' column). FR1.
    Stateless: rows are returned to the client."""
    data = await _read_limited(file)
    try:
        rows = extractors.parse_email_csv(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if len(rows) > MAX_EMAILS:
        raise HTTPException(
            400, f"CSV has {len(rows)} emails; the limit is {MAX_EMAILS} "
                 "per analysis run.")
    return {"filename": file.filename, "count": len(rows), "rows": rows}


@app.post("/api/analyze-batch")
def analyze_batch(req: AnalyzeBatchRequest):
    """Analyse a batch of emails against the provided scope chunks.
    FR3–FR6. The client orchestrates batches and aggregates results.

    In OpenAI mode, pass include_embeddings=true on the first batch; the
    response returns the scope embeddings so later batches can send them
    back and avoid re-embedding the baseline every request."""
    embedder, judge = _providers(req.mode, req.api_key)

    if req.scope_embeddings is not None:
        if len(req.scope_embeddings) != len(req.scope_chunks):
            raise HTTPException(
                400, "scope_embeddings length must match scope_chunks.")
        index = eng.index_from_precomputed(
            req.scope_chunks, req.scope_embeddings, embedder)
    else:
        index = eng.ScopeIndex.from_chunks(req.scope_chunks, embedder)

    # Embed the baseline up front so provider problems (invalid key, no
    # billing, quota) surface as one clear error instead of an opaque 500.
    if req.mode != "demo" and req.scope_embeddings is None:
        try:
            _ = index.matrix
        except Exception as exc:
            raise HTTPException(
                502, "The AI provider rejected the request: "
                     f"{type(exc).__name__}: {str(exc)[:300]}. "
                     "Common causes: invalid API key, no billing/credit on "
                     "the OpenAI account, or a project-restricted key.")

    results = []
    for item in req.emails:
        j = eng.analyse_email(item.email_body, index, judge,
                              top_k=req.top_k)
        results.append({"index": item.index,
                        "email_body": item.email_body,
                        **j.to_dict()})

    out = {"results": results}
    if req.include_embeddings and req.mode != "demo":
        try:
            out["scope_embeddings"] = index.matrix.tolist()
        except Exception:
            pass  # embeddings cache is an optimisation, never a failure
    return out


@app.post("/api/notify")
def notify(req: NotifyRequest):
    """Send content-free SMS alerts for flagged items (FR5, NFR3).
    De-duplicated per (number, ref) via a best-effort persistent ledger."""
    bad = [p for p in req.phones if not sms_mod.valid_number(p)]
    if bad:
        raise HTTPException(
            400, f"Invalid phone number(s): {', '.join(bad)}. "
                 "Use E.164 format, e.g. +447911123456.")
    if not sms_mod.twilio_configured():
        raise HTTPException(
            400, "Twilio is not configured on the server. Set "
                 "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and "
                 "TWILIO_PHONE_NUMBER.")
    history = sms_mod.SmsHistory()
    outcomes = []
    for item in req.items:
        for phone in req.phones:
            number = sms_mod.clean_phone_number(phone)
            ref = f"{req.run_id}:{item.ref}"
            if history.already_sent(number, ref):
                outcomes.append({"index": item.index, "to": number,
                                 "status": "skipped-duplicate"})
                continue
            ok, err = sms_mod.send_sms(
                number, sms_mod.alert_message(
                    item.risk_level, item.index,
                    req.run_id or uuid.uuid4().hex, item.grounded))
            if ok:
                history.record(number, ref)
            outcomes.append({"index": item.index, "to": number,
                             "status": "sent" if ok else "failed",
                             "error": err})
    return {"outcomes": outcomes}


@app.get("/api/sample-data")
def sample_data():
    """Locations of bundled sample files so testers can try the app fast."""
    base = Path(__file__).resolve().parents[1] / "test_data"
    files = {p.name: f"/sample/{p.name}"
             for p in sorted(base.glob("*")) if p.suffix in
             (".csv", ".pdf", ".docx")}
    return {"files": files}


@app.get("/sample/{name}")
def sample_file(name: str):
    base = (Path(__file__).resolve().parents[1] / "test_data").resolve()
    target = (base / name).resolve()
    if not str(target).startswith(str(base)) or not target.exists():
        raise HTTPException(404, "No such sample file.")
    return FileResponse(target)


# Static frontend — mounted last so /api keeps precedence. Served by the
# function everywhere (works on Vercel too; moving files to public/ would
# hand them to the CDN instead — optional optimisation, see DEPLOYMENT.md).
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True),
              name="static")
