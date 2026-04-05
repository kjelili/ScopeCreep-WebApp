# scope_rag_checker.py

from openai import OpenAI
import json
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import re
import hashlib
import traceback

# Cache for embeddings
EMBEDDING_CACHE = {}


def normalize_risk(risk_str):
    """Standardize risk level casing and values"""
    if not risk_str:
        return "unknown"
    risk = str(risk_str).strip().lower()
    if risk in ["high", "extreme", "critical"]:
        return "high"
    if risk in ["medium", "moderate", "mod"]:
        return "moderate"
    if risk in ["low", "minor"]:
        return "low"
    return risk


def normalize_scope_creep(value) -> str:
    """Normalize scope creep field into a stable string: yes/no/error/unknown."""
    if value is None:
        return "unknown"
    s = str(value).strip().lower()
    if s in {"true", "yes", "y", "1"}:
        return "yes"
    if s in {"false", "no", "n", "0"}:
        return "no"
    if s in {"error", "err"}:
        return "error"
    return s or "unknown"


def _client(api_key: str) -> OpenAI:
    """Create an OpenAI client for a given API key (keeps auth local, avoids global state)."""
    return OpenAI(api_key=api_key)


def get_embedding(text, api_key, model="text-embedding-3-small"):
    """Generate or retrieve cached embedding for text."""
    cache_key = hashlib.sha256(f"{model}:{text}".encode("utf-8")).hexdigest()
    if cache_key in EMBEDDING_CACHE:
        return EMBEDDING_CACHE[cache_key]

    client = _client(api_key)
    resp = client.embeddings.create(
        model=model,
        input=[text],
    )
    embedding = resp.data[0].embedding
    EMBEDDING_CACHE[cache_key] = embedding
    return embedding


def chunk_text(text, chunk_size=500, overlap=55):
    """Split text into overlapping chunks with sentence awareness."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    if not text:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = ""

    for sent in sentences:
        if len(current) + len(sent) <= chunk_size:
            current += sent + " "
        else:
            if current:
                chunks.append(current.strip())
            if current:
                current = current[-min(len(current), overlap):] + " "
            else:
                current = ""
            current += sent + " "

    if current:
        chunks.append(current.strip())
    return chunks


def retrieve_relevant_chunks(query, documents, api_key, top_k=3):
    """Retrieve top_k most relevant document chunks using cosine similarity."""
    if not documents:
        return []

    query_emb = np.array(get_embedding(query, api_key)).reshape(1, -1)
    doc_embs = np.array([get_embedding(doc, api_key) for doc in documents])

    sims = cosine_similarity(query_emb, doc_embs)[0]
    idxs = np.argsort(sims)[-top_k:][::-1]

    return [(documents[i], float(sims[i])) for i in idxs]


def _parse_json_safely(raw: str) -> dict:
    """Try to extract JSON from the model output, even if there is extra text."""
    raw = (raw or "").strip()

    # Fast path: entire content is JSON
    try:
        return json.loads(raw)
    except Exception:
        pass

    # Fallback: first {...} block
    try:
        start = raw.index("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])
    except Exception:
        raise ValueError(f"Model did not return valid JSON: {raw}")


def check_scope_creep_with_rag(email, scope_text, api_key):
    """
    Main function used by the Streamlit app.
    Returns a dict with:
      scope_creep, justification, suggestion, risk_level,
      reference_scope_line, impact_analysis
    """
    try:
        client = _client(api_key)

        # Build RAG context
        scope_chunks = chunk_text(scope_text)
        relevant = retrieve_relevant_chunks(email, scope_chunks, api_key)

        context = "\n\n".join(
            f"Scope Section (Relevance: {score:.2f}):\n{chunk}"
            for chunk, score in relevant
        )

        system_prompt = (
            "You are an AI project management assistant analyzing for scope creep.\n"
            "Compare the EMAIL content against the PROJECT SCOPE SECTIONS.\n"
            "Respond ONLY with a valid JSON object (no extra commentary) with keys:\n"
            "  scope_creep, justification, suggestion, risk_level,\n"
            "  reference_scope_line, impact_analysis.\n"
            "risk_level must be one of: Low, Moderate, High, Extreme."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content":
                    "PROJECT SCOPE SECTIONS:\n"
                    "----------------------\n"
                    f"{context}\n\n"
                    "EMAIL CONTENT:\n"
                    "-------------\n"
                    f"{email}",
            },
        ]

        resp = client.chat.completions.create(
            model="gpt-4o-mini",   # or "gpt-4o" if you prefer
            messages=messages,
            temperature=0.2,
        )

        raw_content = resp.choices[0].message.content
        result = _parse_json_safely(raw_content)

        # Normalise & fill missing keys
        result["risk_level"] = normalize_risk(result.get("risk_level", "Unknown"))
        result["scope_creep"] = normalize_scope_creep(result.get("scope_creep", "unknown"))
        result.setdefault("justification", "")
        result.setdefault("suggestion", "")
        result.setdefault("reference_scope_line", "none")
        result.setdefault("impact_analysis", "unknown")

        return result

    except Exception as e:
        return {
            "scope_creep": "Error",
            "justification": f"An error occurred during AI analysis: {e}",
            "suggestion": "Check logs for details. If this persists, reinstall deps from requirements.txt (httpx pin fixes proxies/proxy mismatches).",
            "risk_level": "Unknown",
            "reference_scope_line": "none",
            "impact_analysis": "unknown",
            "_traceback": traceback.format_exc(),
        }
