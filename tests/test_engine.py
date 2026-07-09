"""Unit tests for the RAG engine. No network access required."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import engine
from app.demo import DemoProvider


# ---------------------------------------------------------------- chunking

def test_chunk_text_empty():
    assert engine.chunk_text("") == []
    assert engine.chunk_text(None) == []
    assert engine.chunk_text("   \n\t ") == []


def test_chunk_text_respects_size():
    text = " ".join(f"Sentence number {i} is here." for i in range(100))
    chunks = engine.chunk_text(text)
    assert len(chunks) > 1
    # sentence-aware chunks stay near the target size
    assert all(len(c) <= engine.CHUNK_SIZE + engine.CHUNK_OVERLAP + 1
               for c in chunks)


def test_chunk_text_overlap_carries_context():
    text = " ".join(f"Sentence number {i} is here." for i in range(100))
    chunks = engine.chunk_text(text)
    # tail of chunk n appears at the head of chunk n+1
    tail = chunks[0][-20:]
    assert tail in chunks[1][: engine.CHUNK_OVERLAP + 25]


def test_chunk_text_handles_giant_sentence():
    text = "x" * 1700  # no sentence boundaries at all
    chunks = engine.chunk_text(text)
    assert sum(len(c) for c in chunks) >= 1700
    assert all(len(c) <= engine.CHUNK_SIZE for c in chunks)


def test_chunk_text_preserves_content():
    text = "The roof is included. The garden is excluded. Budget is fixed."
    chunks = engine.chunk_text(text)
    assert len(chunks) == 1 and "garden" in chunks[0]


# ------------------------------------------------------------- normalisers

def test_normalize_risk_keeps_four_levels():
    # regression test for the original prototype's collapse of extreme->high
    assert engine.normalize_risk("Extreme") == "extreme"
    assert engine.normalize_risk("HIGH") == "high"
    assert engine.normalize_risk("critical") == "extreme"
    assert engine.normalize_risk("medium") == "moderate"
    assert engine.normalize_risk("minor") == "low"
    assert engine.normalize_risk("banana") == "unknown"
    assert engine.normalize_risk(None) == "unknown"


def test_normalize_verdict():
    assert engine.normalize_verdict("Yes") == "yes"
    assert engine.normalize_verdict(True) == "yes"
    assert engine.normalize_verdict("0") == "no"
    assert engine.normalize_verdict("maybe") == "unknown"


# ------------------------------------------------------------ JSON parsing

def test_parse_json_plain():
    assert engine.parse_json_safely('{"a": 1}') == {"a": 1}


def test_parse_json_with_wrapping_text():
    raw = 'Sure! Here you go:\n{"scope_creep": "yes"}\nHope that helps.'
    assert engine.parse_json_safely(raw) == {"scope_creep": "yes"}


def test_parse_json_failure_raises():
    with pytest.raises(ValueError):
        engine.parse_json_safely("not json at all")


# ---------------------------------------------------------------- grounding

def test_grounding_verbatim_quote_passes():
    chunks = ["The contractor shall install a green roof system. "
              "All landscaping is limited to the immediate surroundings."]
    ok, score = engine.verify_grounding(
        "The contractor shall install a green roof system.", chunks)
    assert ok and score == 1.0


def test_grounding_fabricated_quote_fails():
    chunks = ["Foundation works include piling and ground beams."]
    ok, score = engine.verify_grounding(
        "The project includes a rooftop swimming pool and helipad.", chunks)
    assert not ok
    assert score < engine.GROUNDING_THRESHOLD


def test_grounding_none_is_ungrounded():
    ok, score = engine.verify_grounding("none", ["anything"])
    assert not ok and score == 0.0


def test_grounding_near_match_passes():
    chunks = ["The works comprise the design and construction of a "
              "three-storey office building at 12 High Street."]
    ok, _ = engine.verify_grounding(
        "the works comprise the design and construction of a three storey "
        "office building", chunks)
    assert ok


# ---------------------------------------------------------------- retrieval

SCOPE = (
    "The project delivers a three-storey office building. "
    "Foundation works include piling and ground beams. "
    "The roofing system uses standing-seam metal panels. "
    "Interior fit-out covers open-plan offices and two meeting rooms. "
    "External works are limited to the car park and access road. "
    "Mechanical and electrical services include HVAC and standard lighting."
)


def test_scope_index_retrieval_ranks_relevant_chunk_first():
    provider = DemoProvider()
    index = engine.ScopeIndex(SCOPE, provider)
    results = index.retrieve("Can we change the roofing panels?", top_k=3)
    assert results, "retrieval returned nothing"
    assert all(-1.001 <= s <= 1.001 for _, s in results)
    sims = [s for _, s in results]
    assert sims == sorted(sims, reverse=True)


def test_scope_index_empty_document():
    index = engine.ScopeIndex("", DemoProvider())
    assert index.retrieve("anything") == []


# ------------------------------------------------------------ end-to-end

def test_analyse_email_demo_creep():
    provider = DemoProvider()
    index = engine.ScopeIndex(SCOPE, provider)
    j = engine.analyse_email(
        "Client asked: can we add a rooftop garden and extra solar panels? "
        "Needs pricing urgently, budget is tight.", index, provider)
    assert j.scope_creep == "yes"
    assert j.risk_level in engine.RISK_LEVELS
    assert j.error is None
    # top_k=3 but the short test scope fits in one chunk
    assert 1 <= len(j.retrieved) <= 3
    # demo provider quotes verbatim, so grounding must verify
    assert j.grounded


def test_analyse_email_demo_no_creep():
    provider = DemoProvider()
    index = engine.ScopeIndex(SCOPE, provider)
    j = engine.analyse_email(
        "Minutes attached from the HVAC services meeting.", index, provider)
    assert j.scope_creep == "no"
    assert j.risk_level == "low"


def test_analyse_email_error_is_contained():
    class Broken:
        name = "broken"

        def embed(self, texts):
            raise RuntimeError("boom")

        def complete(self, s, u):
            raise RuntimeError("boom")

    index = engine.ScopeIndex(SCOPE, DemoProvider())
    j = engine.analyse_email("hello", index, Broken())
    assert j.scope_creep == "error"
    assert "boom" in (j.error or "")


# ----------------------------------------------------------- alert gating

def make_j(verdict, risk):
    j = engine.Judgement()
    j.scope_creep, j.risk_level = verdict, risk
    return j


def test_alert_threshold_ladders():
    assert engine.alert_eligible(make_j("yes", "extreme"), "extreme")
    assert not engine.alert_eligible(make_j("yes", "high"), "extreme")
    assert engine.alert_eligible(make_j("yes", "high"), "high")
    assert engine.alert_eligible(make_j("yes", "extreme"), "high")
    assert engine.alert_eligible(make_j("yes", "moderate"), "moderate")
    assert not engine.alert_eligible(make_j("no", "extreme"), "moderate")
    assert not engine.alert_eligible(make_j("yes", "low"), "moderate")


def test_demo_provider_is_deterministic():
    p = DemoProvider()
    e = p.embed(["scope creep detection", "scope creep detection"])
    assert np.allclose(e[0], e[1])
    prompt = ("PROJECT SCOPE SECTIONS:\nScope Section 1:\nA.\n\n"
              "EMAIL CONTENT:\nplease add an extra window")
    r1, _ = p.complete("s", prompt)
    r2, _ = p.complete("s", prompt)
    assert r1 == r2


def test_normalize_basis():
    assert engine.normalize_basis("omission", "Some clause.") == "omission"
    assert engine.normalize_basis("conflict", "Some clause.") == "conflict"
    assert engine.normalize_basis("", "Some clause.") == "conflict"
    assert engine.normalize_basis("banana", "Some clause.") == "conflict"
    assert engine.normalize_basis("omission", "none") == "none"
    assert engine.normalize_basis(None, "") == "none"


def test_analyse_email_carries_evidence_basis():
    provider = DemoProvider()
    index = engine.ScopeIndex(SCOPE, provider)
    j = engine.analyse_email(
        "Please add an extra rooftop garden urgently.", index, provider)
    assert j.evidence_basis in ("omission", "conflict")
    assert "evidence_basis" in j.to_dict()
