import pytest

from qfactor.agent.llm import LLMClient


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.is_success:
            raise RuntimeError(f"http {self.status_code}")


class _Client:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, *_args, **_kwargs):
        return self.response


def test_chat_json_extracts_standard_completion(monkeypatch):
    import qfactor.agent.llm as llm

    response = _Response({"choices": [{"message": {"content": '{"ok": true}'}}]})
    monkeypatch.setattr(llm.httpx, "Client", lambda **_kwargs: _Client(response))

    out = LLMClient(api_key="x", base_url="https://example.test", model="gpt-5-mini").chat_json(
        "Return JSON only.", "Return json."
    )

    assert out == {"ok": True}


def test_chat_json_surfaces_error_payload_when_choices_missing(monkeypatch):
    import qfactor.agent.llm as llm

    response = _Response({"error": {"message": "model_not_found"}})
    monkeypatch.setattr(llm.httpx, "Client", lambda **_kwargs: _Client(response))

    client = LLMClient(api_key="x", base_url="https://example.test", model="gpt-5-mini")
    with pytest.raises(RuntimeError, match="LLM completion missing choices.*model_not_found"):
        client.chat_json("Return JSON only.", "Return json.")
