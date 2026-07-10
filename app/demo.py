"""
Offline demo provider.

Lets project professionals test the full workflow — upload, retrieval,
judgement, risk grading, alerts, export — without an OpenAI key. Results
are heuristic and clearly watermarked "demo" in the UI; they exist so the
validation exercise (thesis §3.3.3) can focus on workflow and usability
before any spend on API calls.

Embeddings: deterministic hashed bag-of-words vectors (cosine-comparable).
Judgement: keyword + retrieval-similarity heuristic, fully deterministic.
"""

from __future__ import annotations

import re

import numpy as np

_DIM = 512

_CHANGE_CUES = [
    "add", "extra", "additional", "include", "expand", "extend", "upgrade",
    "change", "instead", "also", "new", "increase", "more", "another",
    "squeeze", "install", "replace", "modify",
]
_URGENCY_CUES = [
    "asap", "urgent", "immediately", "friday", "tomorrow", "today",
    "don't worry", "dont worry", "paperwork later", "no need for",
]
_COST_CUES = ["budget", "cost", "price", "expensive", "fee", "charge"]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text).lower())


class DemoProvider:
    """Satisfies the engine.Provider protocol with no network access."""

    name = "demo"

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), _DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            for tok in _tokens(text):
                out[row, hash(tok) % _DIM] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)

    def complete(self, system: str, user: str) -> tuple[dict, dict]:
        if user.startswith("THREAD SEQUENCE"):
            return self._complete_drift(user)
        email = user.split("EMAIL CONTENT:")[-1]
        scope = user.split("EMAIL CONTENT:")[0]
        toks = set(_tokens(email))

        change_hits = [c for c in _CHANGE_CUES if c in toks]
        urgency = any(c in email.lower() for c in _URGENCY_CUES)
        cost = any(c in email.lower() for c in _COST_CUES)

        # crude coverage check: how much of the email vocabulary appears
        # in the retrieved scope sections
        scope_toks = set(_tokens(scope))
        content = [t for t in toks if len(t) > 3]
        coverage = (sum(1 for t in content if t in scope_toks)
                    / max(len(content), 1))

        creep = bool(change_hits) and coverage < 0.8
        if creep and urgency and cost:
            risk = "Extreme"
        elif creep and (urgency or cost):
            risk = "High"
        elif creep:
            risk = "Moderate"
        else:
            risk = "Low"

        # quote the first scope sentence as reference (verbatim, so the
        # grounding verifier can confirm it — mirroring ideal model behaviour)
        m = re.search(r"Scope Section 1:\n(.+?)(?:\n\n|$)", user, re.S)
        first_chunk = m.group(1).strip() if m else ""
        sentence = re.split(r"(?<=[.!?])\s+", first_chunk)[0] if first_chunk else "none"

        result = {
            "scope_creep": "yes" if creep else "no",
            "justification": (
                f"[Demo heuristic] Change cues detected: {', '.join(change_hits) or 'none'}; "
                f"scope coverage {coverage:.0%}; urgency={'yes' if urgency else 'no'}; "
                f"cost pressure={'yes' if cost else 'no'}."
            ),
            "suggestion": ("Log a change request and confirm against the scope baseline."
                           if creep else "No action needed; monitor the thread."),
            "risk_level": risk,
            "reference_scope_line": sentence if creep else "none",
            "evidence_basis": "omission" if creep else "none",
            "impact_analysis": ("Potential time/cost impact if actioned informally."
                                if creep else "No material impact identified."),
        }
        meta = {"model": "demo-heuristic-v1", "system_fingerprint": "demo",
                "usage": 0}
        return result, meta

    def _complete_drift(self, user: str) -> tuple[dict, dict]:
        """Deterministic aggregate judgement for the drift prompt."""
        yes = user.count("verdict=yes")
        highish = user.count("risk=high") + user.count("risk=extreme")
        creep = yes >= 2
        risk = ("Extreme" if creep and (yes >= 4 or highish >= 2)
                else "High" if creep and (yes >= 3 or highish >= 1)
                else "Moderate" if creep else "Low")
        result = {
            "cumulative_creep": "yes" if creep else "no",
            "cumulative_risk": risk,
            "narrative": (f"[Demo heuristic] {yes} of the emails in this "
                          "sequence were individually flagged; taken "
                          "together they indicate accumulating scope drift."
                          if creep else
                          "[Demo heuristic] No accumulation pattern across "
                          "this sequence."),
            "recommendation": ("Raise a consolidated change request covering "
                               "the accumulated items." if creep else
                               "No action needed; continue monitoring."),
        }
        return result, {"model": "demo-heuristic-v1",
                        "system_fingerprint": "demo", "usage": 0}
