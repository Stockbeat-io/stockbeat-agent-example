import pytest

import config
from analysis.llm import (
    AnthropicClient,
    OllamaClient,
    OpenAICompatClient,
    build_client,
)


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeSession:
    """Records the last request. Note `headers` — the client always sends it now."""

    def __init__(self, resp):
        self.resp = resp
        self.last_url = None
        self.last_json = None
        self.last_headers = None

    def post(self, url, json=None, headers=None, timeout=None):
        self.last_url = url
        self.last_json = json
        self.last_headers = headers
        return self.resp


class BoomSession:
    def post(self, url, json=None, headers=None, timeout=None):
        raise RuntimeError("backend down")


# --- Ollama ---

def test_ollama_returns_content():
    sess = FakeSession(FakeResp({"message": {"content": "hello"}}))
    client = OllamaClient("http://x", "mistral:7b", session=sess)
    assert client.generate("hi", system="be terse") == "hello"
    assert sess.last_url == "http://x/api/chat"
    assert sess.last_json["model"] == "mistral:7b"
    assert sess.last_json["stream"] is False
    assert [m["role"] for m in sess.last_json["messages"]] == ["system", "user"]


def test_ollama_without_system_omits_system_message():
    sess = FakeSession(FakeResp({"message": {"content": "ok"}}))
    OllamaClient("http://x", "mistral:7b", session=sess).generate("hi")
    assert [m["role"] for m in sess.last_json["messages"]] == ["user"]


def test_ollama_empty_on_error():
    assert OllamaClient("http://x", "m", session=BoomSession()).generate("hi") == ""


# --- OpenAI-compatible ---

def test_openai_compat_returns_content():
    payload = {"choices": [{"message": {"content": "hi there"}}]}
    sess = FakeSession(FakeResp(payload))
    client = OpenAICompatClient("http://y", "gpt-4o-mini", api_key="k1", session=sess)
    assert client.generate("hi", system="sys") == "hi there"
    assert sess.last_url == "http://y/v1/chat/completions"
    assert sess.last_headers["Authorization"] == "Bearer k1"
    assert [m["role"] for m in sess.last_json["messages"]] == ["system", "user"]


def test_openai_compat_strips_trailing_slash_on_base_url():
    sess = FakeSession(FakeResp({"choices": [{"message": {"content": "x"}}]}))
    OpenAICompatClient("http://y/", "m", api_key="k", session=sess).generate("hi")
    assert sess.last_url == "http://y/v1/chat/completions"


def test_openai_compat_empty_on_error():
    client = OpenAICompatClient("http://y", "m", api_key="k", session=BoomSession())
    assert client.generate("hi") == ""


# --- Anthropic ---

def test_anthropic_returns_content():
    sess = FakeSession(FakeResp({"content": [{"text": "claude says"}]}))
    client = AnthropicClient("http://z", "claude-sonnet-4-6", api_key="k2", session=sess)
    assert client.generate("hi", system="sys") == "claude says"
    assert sess.last_url == "http://z/v1/messages"
    assert sess.last_headers["x-api-key"] == "k2"
    assert sess.last_headers["anthropic-version"] == "2023-06-01"
    assert sess.last_json["max_tokens"] > 0


def test_anthropic_system_is_top_level_not_a_message():
    """The Messages API takes `system` as a top-level field, not a role."""
    sess = FakeSession(FakeResp({"content": [{"text": "x"}]}))
    AnthropicClient("http://z", "m", api_key="k", session=sess).generate("hi", system="be terse")
    assert sess.last_json["system"] == "be terse"
    assert [m["role"] for m in sess.last_json["messages"]] == ["user"]


def test_anthropic_omits_system_when_absent():
    sess = FakeSession(FakeResp({"content": [{"text": "x"}]}))
    AnthropicClient("http://z", "m", api_key="k", session=sess).generate("hi")
    assert "system" not in sess.last_json


def test_anthropic_empty_on_error():
    client = AnthropicClient("http://z", "m", api_key="k", session=BoomSession())
    assert client.generate("hi") == ""


def test_malformed_response_returns_empty():
    """A 200 with an unexpected shape must not raise into the pipeline."""
    sess = FakeSession(FakeResp({"unexpected": "shape"}))
    assert OllamaClient("http://x", "m", session=sess).generate("hi") == ""


# --- Factory ---

def test_build_client_defaults_to_ollama(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(config, "LLM_MODEL", "mistral:7b")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    client = build_client()
    assert isinstance(client, OllamaClient)
    assert client.model == "mistral:7b"


def test_build_client_ollama_needs_no_api_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(config, "LLM_MODEL", "mistral:7b")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    assert build_client() is not None


def test_build_client_anthropic(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setattr(config, "LLM_MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-ant-test")
    assert isinstance(build_client(), AnthropicClient)


def test_build_client_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_MODEL", "m")
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        build_client()


def test_build_client_missing_model_raises(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai-compatible")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    with pytest.raises(ValueError, match="LLM_MODEL is required"):
        build_client()


def test_build_client_missing_api_key_raises(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setattr(config, "LLM_MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    with pytest.raises(ValueError, match="LLM_API_KEY is required"):
        build_client()
