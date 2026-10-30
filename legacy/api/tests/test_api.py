import time

from fastapi.testclient import TestClient

from tynode_api.main import app


def _span(i, parent=None, kind="chain", err=None):
    now = time.time()
    return {
        "id": i, "trace_id": "t1", "parent_id": parent, "name": f"step-{i}", "kind": kind,
        "start": now, "end": now + 0.5, "model": "gpt-4o-mini" if kind == "llm" else None,
        "tokens_in": 100, "tokens_out": 50, "cost": 0.001, "error": err,
    }


def test_ingest_and_query():
    with TestClient(app) as c:
        r = c.post("/v1/spans", json=[_span("a"), _span("b", "a", "llm")])
        assert r.status_code == 200 and r.json()["ingested"] == 2
        traces = c.get("/api/v1/traces").json()
        assert len(traces) == 1 and traces[0]["spans"] == 2
        detail = c.get("/api/v1/traces/t1").json()
        assert {s["id"] for s in detail} == {"a", "b"}
        ov = c.get("/api/v1/overview").json()
        assert ov["traces"] == 1 and ov["llm_calls"] == 1
        assert c.get("/api/v1/traces/nope").status_code == 404


def test_health():
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "ok"