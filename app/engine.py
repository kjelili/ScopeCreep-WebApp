"""
Core RAG engine for scope-creep detection.

Implements the pipeline specified in Chapter 5 of the thesis:
  scope artefact -> sentence-aware chunking (~500 chars, 55 overlap)
  -> embeddings (text-embedding-3-small) -> cosine top-k retrieval
  -> grounded LLM judgement (gpt-4o-mini, temperature 0, JSON mode)
  -> risk grading (Low / Moderate / High / Extreme)
  -> evidence-grounding verification (FR6)

Corrections relative to the original Streamlit prototype:
  * Four risk levels preserved end-to-end (previously "Extreme" was
    collapsed into "High" by normalisation, contradicting §5.6.1).
  * reference_scope_line is now VERIFIED against the retrieved chunks
    (FR6): every judgement carries a grounding score and flag.
  * response_format=json_object removes a class of parse failures (NFR2).
  * temperature=0 and a fixed seed; the responding model version and
    system fingerprint are logged per call (Chapter 6, §6.6.3).
  * Retries with exponential backoff on transient API errors.
  * A minimum-similarity floor flags low-relevance retrievals instead of
    silently judging against unrelated context.

Providers are injectable so the same pipeline runs against OpenAI or the
offline demo provider (see demo.py), and so tests never need the network.

References:
  OpenAI API:        https://platform.openai.com/docs/api-reference
  Embeddings guide:  https://platform.openai.com/docs/guides/embeddings
  Structured output: https://platform.openai.com/docs/guides/structured-outputs
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

import numpy as np

# ----------------------------------------------------------------------------
# Constants (thesis §5.2, §5.4–5.6)
# ----------------------------------------------------------------------------

CHUNK_SIZE = 500          # characters, sentence-aware (§5.2.1)
CHUNK_OVERLAP = 55        # characters carried between chunks (§5.2.1)
TOP_K = 3                 # retrieved fragments per email (§5.4)
MIN_SIMILARITY = 0.10     # below this, retrieval is flagged low-relevance
GROUNDING_THRESHOLD = 0.55  # fuzzy-match ratio for FR6 verification

RISK_LEVELS = ["low", "moderate", "high", "extreme"]  # §5.6.1 — four levels

EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
SEED = 42

SYSTEM_PROMPT = (
    "You are an AI assistant supporting construction project governance. "
    "Compare the EMAIL content against the PROJECT SCOPE SECTIONS provided.\n"
    "Decide whether the email indicates potential scope creep: a request, "
    "commitment or change that falls outside the documented scope baseline.\n"
    "Respond ONLY with a valid JSON object with exactly these keys:\n"
    '  "scope_creep": "yes" or "no"\n'
    '  "justification": brief reasoning grounded in the scope sections\n'
    '  "suggestion": recommended next action for the project manager\n'
    '  "risk_level": one of "Low", "Moderate", "High", "Extreme"\n'
    '  "reference_scope_line": one sentence quoted VERBATIM from the scope '
    "sections. If the email conflicts with or is covered by a clause, quote "
    "that clause. If the request is ABSENT from the scope (creep by "
    "omission), quote the boundary clause - the sentence that best defines "
    "what this part of the works includes - to show the request falls "
    'outside it. Use "none" only when no retrieved section is even loosely '
    "related.\n"
    '  "evidence_basis": "conflict" if the quoted clause covers or conflicts '
    'with the email, "omission" if you quoted a boundary clause and the '
    'request is absent from scope, "none" otherwise\n'
    '  "impact_analysis": likely impact on time, cost or quality\n'
    "Never invent scope text. If the scope sections do not cover the topic, "
    "say so in the justification."
)


# ----------------------------------------------------------------------------
# Provider protocol — OpenAI and the demo provider both satisfy this
# ----------------------------------------------------------------------------

class Provider(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...
    def complete(self, system: str, user: str) -> tuple[dict, dict]:
        """Returns (parsed_json, meta). meta includes model/fingerprint."""


class OpenAIProvider:
    """Thin wrapper over the OpenAI SDK with retries and JSON mode."""

    name = "openai"

    def __init__(self, api_key: str,
                 chat_model: str = CHAT_MODEL,
                 embedding_model: str = EMBEDDING_MODEL,
                 max_retries: int = 3):
        from openai import OpenAI  # local import keeps tests network-free
        self._client = OpenAI(api_key=api_key)
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.max_retries = max_retries

    def _with_retries(self, fn: Callable):
        delay = 1.0
        last_exc: Optional[Exception] = None
        for _ in range(self.max_retries):
            try:
                return fn()
            except Exception as exc:  # RateLimitError, APIError, timeouts
                last_exc = exc
                transient = any(s in type(exc).__name__ for s in
                                ("RateLimit", "APIConnection", "Timeout",
                                 "InternalServer", "APIStatus"))
                if not transient:
                    raise
                time.sleep(delay)
                delay *= 2
        raise last_exc  # type: ignore[misc]

    def embed(self, texts: list[str]) -> np.ndarray:
        def call():
            resp = self._client.embeddings.create(
                model=self.embedding_model, input=texts)
            return np.array([d.embedding for d in resp.data], dtype=np.float32)
        return self._with_retries(call)

    def complete(self, system: str, user: str) -> tuple[dict, dict]:
        def call():
            resp = self._client.chat.completions.create(
                model=self.chat_model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0.0,
                seed=SEED,
                response_format={"type": "json_object"},
            )
            meta = {
                "model": resp.model,
                "system_fingerprint": getattr(resp, "system_fingerprint", None),
                "usage": resp.usage.total_tokens if resp.usage else None,
            }
            return parse_json_safely(resp.choices[0].message.content), meta
        return self._with_retries(call)


class AnthropicProvider:
    """Comparator judge model (RQ1 cross-model evaluation). Judgement only:
    retrieval stays on OpenAI embeddings so verdict differences are
    attributable to the model, not the retriever. temperature 0; Anthropic
    has no seed parameter (noted for the reproducibility analysis)."""

    name = "anthropic"

    def __init__(self, api_key: str, chat_model: str = "claude-haiku-4-5",
                 max_retries: int = 3):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self.chat_model = chat_model
        self.max_retries = max_retries

    def embed(self, texts: list[str]) -> np.ndarray:
        raise RuntimeError(
            "AnthropicProvider is judgement-only; retrieval embeddings "
            "come from the OpenAI embedder (held constant across models).")

    def complete(self, system: str, user: str) -> tuple[dict, dict]:
        delay, last = 1.0, None
        for _ in range(self.max_retries):
            try:
                resp = self._client.messages.create(
                    model=self.chat_model, max_tokens=1024, temperature=0.0,
                    system=system + "\nRespond with the JSON object only.",
                    messages=[{"role": "user", "content": user}])
                meta = {"model": resp.model, "system_fingerprint": None,
                        "usage": (resp.usage.input_tokens
                                  + resp.usage.output_tokens)}
                return parse_json_safely(resp.content[0].text), meta
            except Exception as exc:
                last = exc
                if not any(x in type(exc).__name__ for x in
                           ("RateLimit", "APIConnection", "Timeout",
                            "Overloaded", "InternalServer", "APIStatus")):
                    raise
                time.sleep(delay); delay *= 2
        raise last  # type: ignore[misc]


class GeminiProvider:
    """Comparator judge model via the Gemini REST API (no extra SDK).
    Judgement only; see AnthropicProvider note on constant retrieval."""

    name = "gemini"

    def __init__(self, api_key: str, chat_model: str = "gemini-2.5-flash",
                 max_retries: int = 3):
        self._key = api_key
        self.chat_model = chat_model
        self.max_retries = max_retries

    def embed(self, texts: list[str]) -> np.ndarray:
        raise RuntimeError(
            "GeminiProvider is judgement-only; retrieval embeddings "
            "come from the OpenAI embedder (held constant across models).")

    def complete(self, system: str, user: str) -> tuple[dict, dict]:
        import httpx
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.chat_model}:generateContent")
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.0,
                                 "responseMimeType": "application/json"},
        }
        delay, last = 1.0, None
        for _ in range(self.max_retries):
            try:
                r = httpx.post(url, params={"key": self._key}, json=payload,
                               timeout=45)
                if r.status_code in (429, 500, 502, 503):
                    raise TimeoutError(f"Gemini transient {r.status_code}")
                if r.status_code != 200:
                    raise RuntimeError(
                        f"Gemini error {r.status_code}: {r.text[:200]}")
                data = r.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                meta = {"model": data.get("modelVersion", self.chat_model),
                        "system_fingerprint": None,
                        "usage": data.get("usageMetadata", {})
                                     .get("totalTokenCount")}
                return parse_json_safely(text), meta
            except (TimeoutError, ConnectionError) as exc:
                last = exc
                time.sleep(delay); delay *= 2
        raise last  # type: ignore[misc]


# ----------------------------------------------------------------------------
# Text processing
# ----------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Sentence-aware chunking. Sentences are never split mid-way unless a
    single sentence exceeds chunk_size; consecutive chunks share ~overlap
    characters of trailing context."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""

    for sent in sentences:
        # hard-split pathological sentences longer than the chunk size
        while len(sent) > chunk_size:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.append(sent[:chunk_size].strip())
            sent = sent[chunk_size:]
        if len(current) + len(sent) + 1 <= chunk_size:
            current += sent + " "
        else:
            if current:
                chunks.append(current.strip())
                current = current[-overlap:].lstrip() + " " if overlap else ""
            current += sent + " "

    if current.strip():
        chunks.append(current.strip())
    return chunks


def parse_json_safely(raw: Optional[str]) -> dict:
    """Parse model output as JSON, tolerating stray text around the object."""
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            pass
    raise ValueError(f"Model did not return valid JSON: {raw[:200]}")


def normalize_risk(value) -> str:
    """Standardise risk casing WITHOUT collapsing levels (fixes the
    original prototype, which mapped 'extreme' into 'high')."""
    risk = str(value or "").strip().lower()
    aliases = {"critical": "extreme", "severe": "extreme",
               "medium": "moderate", "mod": "moderate", "minor": "low"}
    risk = aliases.get(risk, risk)
    return risk if risk in RISK_LEVELS else "unknown"


def normalize_verdict(value) -> str:
    s = str(value or "").strip().lower()
    if s in {"true", "yes", "y", "1"}:
        return "yes"
    if s in {"false", "no", "n", "0"}:
        return "no"
    return "unknown"


def normalize_basis(value, reference: str) -> str:
    """Standardise the evidence basis. A quoted clause with no stated basis
    is treated as a conflict citation; 'none' reference forces 'none'."""
    basis = str(value or "").strip().lower()
    ref = str(reference or "").strip().lower()
    if not ref or ref == "none":
        return "none"
    return basis if basis in ("conflict", "omission") else "conflict"


def _normalise_for_match(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", s.lower())).strip()


def verify_grounding(reference: str, chunks: list[str]) -> tuple[bool, float]:
    """FR6: check that the model's quoted scope line actually appears in the
    retrieved context. Returns (grounded, best_ratio). Substring containment
    counts as fully grounded; otherwise the best fuzzy ratio across chunk
    windows is compared to GROUNDING_THRESHOLD."""
    ref = _normalise_for_match(reference or "")
    if not ref or ref == "none":
        return False, 0.0
    best = 0.0
    for chunk in chunks:
        c = _normalise_for_match(chunk)
        if ref in c:
            return True, 1.0
        ratio = difflib.SequenceMatcher(None, ref, c).ratio()
        # also compare against a sliding window the size of the reference,
        # so long chunks do not dilute the score
        if len(c) > len(ref):
            step = max(1, len(ref) // 2)
            for i in range(0, len(c) - len(ref) + 1, step):
                ratio = max(ratio, difflib.SequenceMatcher(
                    None, ref, c[i:i + len(ref)]).ratio())
        best = max(best, ratio)
    return best >= GROUNDING_THRESHOLD, round(best, 3)


# ----------------------------------------------------------------------------
# Retrieval
# ----------------------------------------------------------------------------

class ScopeIndex:
    """Chunked, embedded scope baseline (FR2). Embeddings are computed once
    per document in a single batched call and cached by content hash."""

    def __init__(self, scope_text: str, provider: Provider):
        self.chunks = chunk_text(scope_text)
        self.provider = provider
        self._matrix: Optional[np.ndarray] = None
        self.fingerprint = hashlib.sha256(
            scope_text.encode("utf-8")).hexdigest()[:16]

    @property
    def matrix(self) -> np.ndarray:
        if self._matrix is None:
            if not self.chunks:
                self._matrix = np.zeros((0, 1), dtype=np.float32)
            else:
                self._matrix = self.provider.embed(self.chunks)
        return self._matrix

    def retrieve(self, query: str, top_k: int = TOP_K
                 ) -> list[tuple[str, float]]:
        if not self.chunks:
            return []
        q = self.provider.embed([query])[0]
        m = self.matrix
        sims = (m @ q) / (np.linalg.norm(m, axis=1) * np.linalg.norm(q) + 1e-9)
        idx = np.argsort(sims)[-top_k:][::-1]
        return [(self.chunks[i], float(sims[i])) for i in idx]


# ----------------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------------

@dataclass
class Judgement:
    scope_creep: str = "unknown"
    risk_level: str = "unknown"
    justification: str = ""
    suggestion: str = ""
    reference_scope_line: str = "none"
    evidence_basis: str = "none"      # conflict | omission | none
    impact_analysis: str = ""
    grounded: bool = False
    grounding_score: float = 0.0
    low_relevance: bool = False
    retrieved: list = field(default_factory=list)   # [(chunk, sim), ...]
    model: Optional[str] = None
    system_fingerprint: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "scope_creep": self.scope_creep,
            "risk_level": self.risk_level,
            "justification": self.justification,
            "suggestion": self.suggestion,
            "reference_scope_line": self.reference_scope_line,
            "evidence_basis": self.evidence_basis,
            "impact_analysis": self.impact_analysis,
            "grounded": self.grounded,
            "grounding_score": self.grounding_score,
            "low_relevance": self.low_relevance,
            "retrieved": [{"text": t, "similarity": round(s, 3)}
                          for t, s in self.retrieved],
            "model": self.model,
            "system_fingerprint": self.system_fingerprint,
        }
        if self.error:
            d["error"] = self.error
        return d


def analyse_email(email: str, index: ScopeIndex, provider: Provider,
                  top_k: int = TOP_K) -> Judgement:
    """Run one email through retrieval + grounded judgement (FR3–FR6)."""
    j = Judgement()
    try:
        # top_k == 0 is the no-retrieval ablation condition (§6.5.3): the
        # model judges without any scope context.
        relevant = index.retrieve(email, top_k=top_k) if top_k > 0 else []
        j.retrieved = relevant
        j.low_relevance = bool(relevant) and max(
            s for _, s in relevant) < MIN_SIMILARITY

        context = "\n\n".join(
            f"Scope Section {i + 1}:\n{chunk}"
            for i, (chunk, _s) in enumerate(relevant)
        ) or "(no scope sections available)"

        user = (
            "PROJECT SCOPE SECTIONS:\n----------------------\n"
            f"{context}\n\nEMAIL CONTENT:\n-------------\n{email}"
        )
        result, meta = provider.complete(SYSTEM_PROMPT, user)

        j.scope_creep = normalize_verdict(result.get("scope_creep"))
        j.risk_level = normalize_risk(result.get("risk_level"))
        j.justification = str(result.get("justification", "")).strip()
        j.suggestion = str(result.get("suggestion", "")).strip()
        j.reference_scope_line = str(
            result.get("reference_scope_line", "none")).strip() or "none"
        j.evidence_basis = normalize_basis(
            result.get("evidence_basis"), j.reference_scope_line)
        j.impact_analysis = str(result.get("impact_analysis", "")).strip()
        j.model = meta.get("model")
        j.system_fingerprint = meta.get("system_fingerprint")

        j.grounded, j.grounding_score = verify_grounding(
            j.reference_scope_line, [c for c, _ in relevant])
    except Exception as exc:  # surfaced per-row, never crashes the batch
        j.scope_creep = "error"
        j.risk_level = "unknown"
        j.error = f"{type(exc).__name__}: {exc}"
    return j


def alert_eligible(j: Judgement, threshold: str) -> bool:
    """FR5: configurable alerting threshold over the four risk levels."""
    ladders = {
        "extreme": ["extreme"],
        "high": ["high", "extreme"],
        "moderate": ["moderate", "high", "extreme"],
    }
    return (j.scope_creep == "yes"
            and j.risk_level in ladders.get(threshold, ladders["high"]))


# ----------------------------------------------------------------------------
# Serverless support: build an index from client-held chunks/embeddings
# ----------------------------------------------------------------------------

def _index_with_chunks(chunks: list[str], provider) -> "ScopeIndex":
    idx = ScopeIndex.__new__(ScopeIndex)
    idx.chunks = list(chunks)
    idx.provider = provider
    idx._matrix = None
    idx.fingerprint = hashlib.sha256(
        "\n".join(chunks).encode("utf-8")).hexdigest()[:16]
    return idx


def index_from_precomputed(chunks: list[str], embeddings,
                           provider) -> "ScopeIndex":
    """Rebuild a ScopeIndex from chunks + embeddings computed earlier
    (returned to and held by the client between stateless requests)."""
    idx = _index_with_chunks(chunks, provider)
    idx._matrix = (np.array(embeddings, dtype=np.float32) if chunks
                   else np.zeros((0, 1), dtype=np.float32))
    return idx


ScopeIndex.from_chunks = classmethod(
    lambda cls, chunks, provider: _index_with_chunks(chunks, provider))
