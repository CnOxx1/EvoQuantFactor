from fastapi.testclient import TestClient


def test_api_loop_rejects_non_research_gate_before_running_agent():
    from qfactor.api.app import create_app

    client = TestClient(create_app())
    response = client.post(
        "/api/agent/loop",
        json={"rounds": 1, "batch_size": 1, "gate_name": "production"},
    )
    assert response.status_code == 422


def test_cli_production_eval_is_diagnostic_only(monkeypatch):
    import qfactor.cli as cli

    captured = {}

    class _Eval:
        def evaluate_and_save(self, name, gate_name, promote):
            captured.update(name=name, gate_name=gate_name, promote=promote)
            return {"summary": {}, "gate": {"status": "candidate", "checks": {}}}

    monkeypatch.setattr(cli, "EvalService", lambda: _Eval())
    cli.eval_factor("quality_factor", gate="production")

    assert captured == {
        "name": "quality_factor",
        "gate_name": "production",
        "promote": False,
    }


def test_cli_direct_status_promotion_is_disabled():
    import typer

    import qfactor.cli as cli

    try:
        cli.promote("quality_factor")
    except typer.BadParameter as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("direct status promotion must be disabled")


def test_public_read_only_mode_exposes_monitor_and_blocks_mutations(monkeypatch):
    from qfactor.api.app import create_app

    monkeypatch.setenv("QFACTOR_READ_ONLY_WEB", "1")
    client = TestClient(create_app())

    home = client.get("/", follow_redirects=False)
    assert home.status_code in {302, 307}
    assert home.headers["location"] == "/ui/monitor"
    assert client.get("/api/factory/status").status_code == 200
    assert client.post("/api/library/archive").status_code == 403
    assert client.post("/api/library/demote-corr").status_code == 403
    assert client.post("/api/agent/loop", json={"rounds": 1, "batch_size": 1}).status_code == 403
