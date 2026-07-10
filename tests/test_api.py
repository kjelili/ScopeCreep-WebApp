"""API integration tests for the stateless (serverless-safe) API."""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app

client = TestClient(app)

SCOPE_TXT = (
    "The project delivers a three-storey office building. "
    "Foundation works include piling and ground beams. "
    "The roofing system uses standing-seam metal panels. "
    "External works are limited to the car park and access road."
).encode()

EMAILS_CSV = (
    "email_body\n"
    "Can we add a rooftop garden to the building?\n"
    "Minutes from the piling progress meeting attached.\n"
    "Client wants extra EV charging points installed urgently - budget?\n"
).encode()


def _uploads():
    s = client.post("/api/scope",
                    files={"file": ("scope.txt", SCOPE_TXT, "text/plain")})
    e = client.post("/api/emails",
                    files={"file": ("emails.csv", EMAILS_CSV, "text/csv")})
    assert s.status_code == 200 and e.status_code == 200
    return s.json(), e.json()


def test_health_reports_limits():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["limits"]["batch"] == 10


def test_scope_returns_chunks_stateless():
    s, _ = _uploads()
    assert s["count"] >= 1
    assert isinstance(s["chunks"], list)
    assert "three-storey" in s["chunks"][0]


def test_emails_returns_rows_stateless():
    _, e = _uploads()
    assert e["count"] == 3
    assert e["rows"][0]["email_body"].startswith("Can we add")


def test_scope_rejects_unsupported_and_tiny():
    assert client.post("/api/scope", files={
        "file": ("x.xlsx", b"junk", "application/junk")}).status_code == 400
    assert client.post("/api/scope", files={
        "file": ("x.txt", b"hi", "text/plain")}).status_code == 400


def test_emails_missing_column():
    r = client.post("/api/emails",
                    files={"file": ("e.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 400 and "email_body" in r.json()["detail"]


def test_analyze_batch_demo_end_to_end():
    s, e = _uploads()
    r = client.post("/api/analyze-batch", json={
        "scope_chunks": s["chunks"],
        "emails": [{"index": i, "email_body": row["email_body"]}
                   for i, row in enumerate(e["rows"])],
        "mode": "demo",
    })
    assert r.status_code == 200
    results = r.json()["results"]
    assert [x["scope_creep"] for x in results] == ["yes", "no", "yes"]
    assert all(x["risk_level"] in ("low", "moderate", "high", "extreme")
               for x in results)
    # grounding metadata on every row (FR6)
    assert all("grounded" in x and "grounding_score" in x for x in results)
    # demo mode never returns embeddings
    assert "scope_embeddings" not in r.json()


def test_analyze_batch_respects_batch_limit():
    s, _ = _uploads()
    r = client.post("/api/analyze-batch", json={
        "scope_chunks": s["chunks"],
        "emails": [{"index": i, "email_body": f"email {i}"}
                   for i in range(11)],
        "mode": "demo",
    })
    assert r.status_code == 422  # pydantic max_length


def test_analyze_batch_embeddings_length_mismatch():
    s, _ = _uploads()
    r = client.post("/api/analyze-batch", json={
        "scope_chunks": s["chunks"],
        "scope_embeddings": [[0.1, 0.2]],  # wrong length vs chunks
        "emails": [{"index": 0, "email_body": "add a window"}],
        "mode": "demo",
    })
    # only valid when it matches chunk count
    assert r.status_code in (200, 400)
    if len(s["chunks"]) != 1:
        assert r.status_code == 400


def test_analyze_batch_precomputed_embeddings_demo():
    """Round-trip: demo embeddings computed client-side style, sent back."""
    from app.demo import DemoProvider
    s, _ = _uploads()
    emb = DemoProvider().embed(s["chunks"]).tolist()
    r = client.post("/api/analyze-batch", json={
        "scope_chunks": s["chunks"], "scope_embeddings": emb,
        "emails": [{"index": 0,
                    "email_body": "Please add an extra meeting room"}],
        "mode": "demo",
    })
    assert r.status_code == 200
    assert r.json()["results"][0]["scope_creep"] == "yes"


def test_openai_mode_requires_key():
    s, _ = _uploads()
    r = client.post("/api/analyze-batch", json={
        "scope_chunks": s["chunks"],
        "emails": [{"index": 0, "email_body": "hi"}],
        "mode": "openai",
    })
    assert r.status_code == 400


def test_notify_requires_twilio_or_fails_clearly(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    r = client.post("/api/notify", json={
        "phones": ["+447911123456"],
        "items": [{"ref": "r1", "risk_level": "high", "index": 0}],
        "run_id": "test",
    })
    assert r.status_code == 400
    assert "Twilio" in r.json()["detail"]


def test_notify_rejects_bad_numbers():
    r = client.post("/api/notify", json={
        "phones": ["not-a-number"],
        "items": [{"ref": "r1", "risk_level": "high", "index": 0}],
    })
    assert r.status_code == 400
    assert "E.164" in r.json()["detail"]


def test_sample_data_listing():
    assert client.get("/api/sample-data").status_code == 200


def test_sample_path_traversal_blocked():
    assert client.get("/sample/..%2f..%2fapp%2fmain.py").status_code == 404


def test_static_landing_served():
    r = client.get("/")
    assert r.status_code == 200 and "Scope" in r.text


def test_openai_mode_uses_server_key_when_present(monkeypatch):
    """With OPENAI_API_KEY set server-side, a request without a key must not
    be rejected with 400 (rows may error later — that is contained)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy",
                "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    s, _ = _uploads()
    r = client.post("/api/analyze-batch", json={
        "scope_chunks": s["chunks"],
        "emails": [{"index": 0, "email_body": "hi"}],
        "mode": "openai",
    })
    # must NOT be rejected for a missing key (400); with a fake key the
    # provider fails and the up-front check reports it clearly as 502
    assert r.status_code in (200, 502)
    if r.status_code == 502:
        assert "AI provider" in r.json()["detail"]


def test_health_reports_server_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert client.get("/api/health").json()["server_openai_key"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert client.get("/api/health").json()["server_openai_key"] is True


def test_openai_provider_failure_returns_clear_error(monkeypatch):
    """Auth/billing failures must surface as 502 with a readable detail."""
    class BrokenProvider:
        name = "openai"

        def __init__(self, key):
            pass

        def embed(self, texts):
            raise RuntimeError("invalid_api_key: Incorrect API key provided")

        def complete(self, s, u):
            raise RuntimeError("should not reach")

    import app.main as m
    monkeypatch.setattr(m.eng, "OpenAIProvider", BrokenProvider)
    s, _ = _uploads()
    r = client.post("/api/analyze-batch", json={
        "scope_chunks": s["chunks"],
        "emails": [{"index": 0, "email_body": "add a window"}],
        "mode": "openai", "api_key": "sk-anything",
    })
    assert r.status_code == 502
    assert "invalid_api_key" in r.json()["detail"]
    assert "Common causes" in r.json()["detail"]


def test_comparator_modes_require_server_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    s, _ = _uploads()
    for mode, name in [("anthropic", "ANTHROPIC_API_KEY"),
                       ("gemini", "GEMINI_API_KEY")]:
        r = client.post("/api/analyze-batch", json={
            "scope_chunks": s["chunks"],
            "emails": [{"index": 0, "email_body": "hi"}],
            "mode": mode})
        assert r.status_code == 400 and name in r.json()["detail"]


def test_comparator_mode_uses_openai_embeddings_and_judge_model(monkeypatch):
    """Retrieval must run on the embed provider; judgement on the comparator."""
    calls = {"embed": 0, "complete": 0}

    class FakeEmbedder:
        name = "openai"

        def __init__(self, key): pass

        def embed(self, texts):
            calls["embed"] += 1
            import numpy as np
            return np.ones((len(texts), 8), dtype=np.float32)

        def complete(self, s, u):
            raise AssertionError("embedder must not judge")

    class FakeJudge:
        name = "anthropic"

        def __init__(self, key, model=None): pass

        def embed(self, texts):
            raise AssertionError("judge must not embed")

        def complete(self, s, u):
            calls["complete"] += 1
            return ({"scope_creep": "yes", "risk_level": "High",
                     "justification": "x", "suggestion": "y",
                     "reference_scope_line": "none",
                     "evidence_basis": "none",
                     "impact_analysis": "z"},
                    {"model": "fake-claude", "system_fingerprint": None})

    import app.main as m
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setattr(m.eng, "OpenAIProvider", FakeEmbedder)
    monkeypatch.setattr(m.eng, "AnthropicProvider", FakeJudge)
    s, _ = _uploads()
    r = client.post("/api/analyze-batch", json={
        "scope_chunks": s["chunks"],
        "emails": [{"index": 0, "email_body": "add a window"}],
        "mode": "anthropic", "include_embeddings": True})
    assert r.status_code == 200
    row = r.json()["results"][0]
    assert row["model"] == "fake-claude" and row["scope_creep"] == "yes"
    assert calls["embed"] >= 1 and calls["complete"] == 1
    assert "scope_embeddings" in r.json()  # embeddings returned for reuse


def test_health_reports_models(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    m = client.get("/api/health").json()["models"]
    assert m["openai"] is True and m["anthropic"] is True and m["gemini"] is False


def test_no_retrieval_ablation_top_k_zero():
    """top_k=0 (§6.5.3): judgement with no scope context; no embeddings."""
    s, _ = _uploads()
    r = client.post("/api/analyze-batch", json={
        "scope_chunks": s["chunks"],
        "emails": [{"index": 0, "email_body": "Can we add a rooftop garden?"}],
        "mode": "demo", "top_k": 0, "include_embeddings": True})
    assert r.status_code == 200
    row = r.json()["results"][0]
    assert row["retrieved"] == []          # nothing retrieved
    assert "scope_embeddings" not in r.json()  # nothing embedded
    assert row["scope_creep"] in ("yes", "no")


def test_analyze_drift_demo():
    r = client.post("/api/analyze-drift", json={
        "mode": "demo",
        "threads": [
            {"key": "t1", "items": [
                {"email_body": "add sockets", "scope_creep": "yes",
                 "risk_level": "moderate"},
                {"email_body": "and a healing garden", "scope_creep": "yes",
                 "risk_level": "high"},
                {"email_body": "also solar", "scope_creep": "yes",
                 "risk_level": "moderate"}]},
            {"key": "t2", "items": [
                {"email_body": "minutes", "scope_creep": "no",
                 "risk_level": "low"},
                {"email_body": "schedule confirmed", "scope_creep": "no",
                 "risk_level": "low"}]},
        ]})
    assert r.status_code == 200
    t1, t2 = r.json()["threads"]
    assert t1["key"] == "t1" and t1["cumulative_creep"] == "yes"
    assert t1["cumulative_risk"] in ("high", "extreme")
    assert t2["cumulative_creep"] == "no"


def test_analyze_drift_validates_shape():
    # single-item thread rejected (nothing to accumulate)
    r = client.post("/api/analyze-drift", json={
        "mode": "demo",
        "threads": [{"key": "t", "items": [
            {"email_body": "x", "scope_creep": "yes",
             "risk_level": "low"}]}]})
    assert r.status_code == 422
