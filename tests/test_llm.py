import analysis.llm as llm_mod
from analysis.llm import OllamaClient


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.last_json = None

    def post(self, url, json=None, timeout=None):
        self.last_json = json
        return self.resp


def test_generate_returns_content():
    sess = FakeSession(FakeResp({"message": {"content": "hello"}}))
    client = OllamaClient("http://x", "mistral:7b", session=sess)
    assert client.generate("hi", system="be terse") == "hello"
    assert sess.last_json["model"] == "mistral:7b"
    assert sess.last_json["stream"] is False
    roles = [m["role"] for m in sess.last_json["messages"]]
    assert roles == ["system", "user"]


def test_generate_without_system_omits_system_message():
    sess = FakeSession(FakeResp({"message": {"content": "ok"}}))
    client = OllamaClient("http://x", "mistral:7b", session=sess)
    client.generate("hi")
    roles = [m["role"] for m in sess.last_json["messages"]]
    assert roles == ["user"]


def test_generate_empty_on_error(monkeypatch):
    class BoomSession:
        def post(self, url, json=None, timeout=None):
            raise RuntimeError("ollama down")

    client = OllamaClient("http://x", "mistral:7b", session=BoomSession())
    assert client.generate("hi") == ""
