from factor_backend.graph.build import build_factor_graph


def test_langgraph_compiles():
    g = build_factor_graph()
    assert g is not None


def test_health_reports_langgraph():
    from fastapi.testclient import TestClient
    from factor_backend.main import app

    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["engine"] == "langgraph"
