"""U1 tests: BedrockSession.invoke() seam + pg_config read-only variant.

These avoid the real OIDC setup() (no network) by constructing BedrockSession via
object.__new__ and injecting a fake boto3 session.
"""
import io
import json
import time

import pytest

import bedrock_session as bs
from embedding_service import pg_config


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class _FakeBody:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def read(self):
        return self._buf.read()


class _FakeRuntime:
    """Captures the request body and returns a canned response (or raises)."""

    def __init__(self, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.last_body = None

    def invoke_model(self, *, modelId, contentType, accept, body):
        self.last_body = json.loads(body)
        if self.raise_exc is not None:
            raise self.raise_exc
        return {"body": _FakeBody(self.response)}


class _FakeSession:
    def __init__(self, runtime):
        self._runtime = runtime

    def client(self, name, region_name=None):
        assert name == "bedrock-runtime"
        return self._runtime


def _make_session(runtime):
    sess = object.__new__(bs.BedrockSession)
    sess.session = _FakeSession(runtime)
    sess.region = "ap-southeast-3"
    sess.model_id = "openai.gpt-oss-120b-1:0"
    sess.target_creds = {"AccessKeyId": "x", "SecretAccessKey": "y", "SessionToken": "z"}
    sess._created_at = time.time()  # fresh → refresh_if_needed() no-ops
    return sess


# --------------------------------------------------------------------------
# invoke()
# --------------------------------------------------------------------------

def test_invoke_returns_full_message_with_tool_calls():
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "search_era_knowledge", "arguments": '{"q":"x"}'},
        }],
    }
    runtime = _FakeRuntime(response={"choices": [{"message": msg}]})
    sess = _make_session(runtime)

    out = sess.invoke(messages=[{"role": "user", "content": "hi"}],
                      tools=[{"type": "function", "function": {"name": "t"}}],
                      max_tokens=2048, temperature=0.0)

    # full message, including tool_calls (not just content)
    assert out["tool_calls"][0]["function"]["name"] == "search_era_knowledge"
    # caller-supplied limits reach the body (not the legacy 512/0.7)
    assert runtime.last_body["max_tokens"] == 2048
    assert runtime.last_body["temperature"] == 0.0
    assert "tools" in runtime.last_body


def test_invoke_raises_on_empty_choices():
    runtime = _FakeRuntime(response={"choices": []})
    sess = _make_session(runtime)
    with pytest.raises(RuntimeError):
        sess.invoke(messages=[{"role": "user", "content": "hi"}])


def test_invoke_raises_on_error_no_silent_none_no_mantle():
    boom = RuntimeError("AccessDeniedException: nope")
    runtime = _FakeRuntime(raise_exc=boom)
    sess = _make_session(runtime)
    with pytest.raises(RuntimeError):
        sess.invoke(messages=[{"role": "user", "content": "hi"}])  # allow_mantle=False


def test_invoke_omits_tools_when_none():
    runtime = _FakeRuntime(response={"choices": [{"message": {"content": "ok"}}]})
    sess = _make_session(runtime)
    sess.invoke(messages=[{"role": "user", "content": "hi"}])
    assert "tools" not in runtime.last_body


# --------------------------------------------------------------------------
# pg_config(readonly=...)
# --------------------------------------------------------------------------

def test_pg_config_readonly_uses_ro_creds(monkeypatch):
    monkeypatch.setenv("PG_RO_USER", "t2s_ro")
    monkeypatch.setenv("PG_RO_PASSWORD", "secret")
    cfg = pg_config(readonly=True)
    assert cfg["user"] == "t2s_ro"
    assert cfg["password"] == "secret"


def test_pg_config_readonly_raises_when_missing(monkeypatch):
    monkeypatch.delenv("PG_RO_USER", raising=False)
    with pytest.raises(RuntimeError):
        pg_config(readonly=True)


def test_pg_config_default_is_superuser(monkeypatch):
    monkeypatch.delenv("PG_USER", raising=False)
    cfg = pg_config()
    assert cfg["user"] == "postgres"
