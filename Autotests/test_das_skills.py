import importlib
import json
from email.message import Message
from pathlib import Path
import socket
import sys
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import das_skills  # noqa: E402
from src import helper  # noqa: E402


class _Response:
    status = 200

    def __init__(self, payload):
        self.body = json.dumps(payload).encode()
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"

    def read(self, size=-1):
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Opener:
    def __init__(self, response, seen):
        self.response = response
        self.seen = seen

    def open(self, request, timeout):
        self.seen.append((request, timeout))
        return self.response


def test_disabled_by_default_never_contacts_network(monkeypatch):
    monkeypatch.delenv("OMEGA_DAS_ENABLED", raising=False)
    monkeypatch.setattr(das_skills, "_endpoint", lambda: pytest.fail("endpoint inspected"))
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: pytest.fail("network called"))
    assert das_skills.das_retrieve('{"tokens":["VARIABLE","X"],"max_answers":2}') == (
        "DAS_RETRIEVE_FAILED: adapter is disabled"
    )


def test_posts_normalized_schema_to_configuration_endpoint(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    monkeypatch.setenv("OMEGA_DAS_PROXY_ORIGIN", "http://read-proxy:8080")
    seen = []
    response = _Response({"answers": [{"assignment": {"X": "abc"}}], "count": 1, "truncated": False})
    monkeypatch.setattr(
        das_skills.urllib.request,
        "build_opener",
        lambda handler: (_assert_no_redirect_handler(handler) or _Opener(response, seen)),
    )
    result = das_skills.das_retrieve('{ "max_answers": 2, "tokens": ["VARIABLE", "X"] }')
    assert result == (
        'DAS_RETRIEVE_OK untrusted=true count=1 truncated=false '
        'results=[{"assignment":{"X":"abc"}}]'
    )
    request, timeout = seen[0]
    assert request.full_url == "http://read-proxy:8080/v1/query"
    assert request.method == "POST"
    assert json.loads(request.data) == {"tokens": ["VARIABLE", "X"], "max_answers": 2}
    assert timeout == das_skills.REQUEST_TIMEOUT_SECONDS
    assert request.get_header("Authorization") is None


def _assert_no_redirect_handler(handler):
    assert isinstance(handler, das_skills._NoRedirects)


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        "{}",
        '{"tokens":[],"max_answers":1}',
        '{"tokens":[1],"max_answers":1}',
        '{"tokens":["X"],"max_answers":0}',
        '{"tokens":["X"],"max_answers":true}',
        '{"tokens":["X"],"max_answers":1,"url":"http://evil.test"}',
        '{"tokens":["X"],"tokens":["Y"],"max_answers":1}',
    ],
)
def test_invalid_input_is_rejected_before_network(monkeypatch, body):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: pytest.fail("network called"))
    assert das_skills.das_retrieve(body).startswith("DAS_RETRIEVE_FAILED:")


def test_oversized_input_is_rejected_before_network(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: pytest.fail("network called"))
    body = " " * das_skills.MAX_REQUEST_BYTES + '{"tokens":["X"],"max_answers":1}'
    assert str(das_skills.MAX_REQUEST_BYTES) in das_skills.das_retrieve(body)


@pytest.mark.parametrize(
    "origin",
    [
        "https://read-proxy:8080",
        "http://user@read-proxy:8080",
        "http://read-proxy:8080/path",
        "http://read-proxy:8080?x=1",
        "http://read-proxy",
        " http://read-proxy:8080",
        "http://read\t-proxy:8080",
    ],
)
def test_noncanonical_configured_origin_is_rejected(monkeypatch, origin):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    monkeypatch.setenv("OMEGA_DAS_PROXY_ORIGIN", origin)
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: pytest.fail("network called"))
    assert "origin" in das_skills.das_retrieve('{"tokens":["X"],"max_answers":1}')


@pytest.mark.parametrize(
    "payload",
    [
        {"answers": [], "count": 1, "truncated": False},
        {"answers": [1, 2], "count": 2, "truncated": False},
        {"answers": [], "count": 0, "truncated": 0},
        {"results": [], "count": 0, "truncated": False},
    ],
)
def test_invalid_response_schema_is_rejected(monkeypatch, payload):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    monkeypatch.setattr(
        das_skills.urllib.request,
        "build_opener",
        lambda *_: _Opener(_Response(payload), []),
    )
    assert das_skills.das_retrieve('{"tokens":["X"],"max_answers":1}').startswith(
        "DAS_RETRIEVE_FAILED:"
    )


def test_malformed_response_is_rejected(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    response = _Response({})
    response.body = b'{"answers":'
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: _Opener(response, []))
    assert das_skills.das_retrieve('{"tokens":["X"],"max_answers":1}') == (
        "DAS_RETRIEVE_FAILED: proxy response is not valid UTF-8 JSON"
    )


def test_response_answer_count_is_bounded(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    response = _Response({"answers": [{}, {}], "count": 2, "truncated": True})
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: _Opener(response, []))
    assert das_skills.das_retrieve('{"tokens":["X"],"max_answers":1}') == (
        "DAS_RETRIEVE_FAILED: proxy returned too many answers"
    )


def test_response_size_is_bounded(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    response = _Response({"answers": [], "count": 0, "truncated": False})
    response.body = b"x" * (das_skills.MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: _Opener(response, []))
    assert "response exceeds" in das_skills.das_retrieve('{"tokens":["X"],"max_answers":1}')


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (socket.timeout("late"), "DAS_RETRIEVE_FAILED: proxy request timed out"),
        (
            urllib.error.URLError(socket.timeout("late")),
            "DAS_RETRIEVE_FAILED: proxy request timed out",
        ),
        (
            urllib.error.URLError("private DNS failed"),
            "DAS_RETRIEVE_FAILED: proxy request failed",
        ),
    ],
)
def test_timeout_and_transport_errors_are_stable(monkeypatch, error, expected):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")

    class _FailingOpener:
        def open(self, _request, timeout):
            assert timeout == das_skills.REQUEST_TIMEOUT_SECONDS
            raise error

    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: _FailingOpener())
    assert das_skills.das_retrieve('{"tokens":["X"],"max_answers":1}') == expected


def test_registration_and_helper_gating(monkeypatch):
    imports = (ROOT / "lib_omegaclaw.metta").read_text()
    skills = (ROOT / "src" / "skills.metta").read_text()
    entrypoint = (ROOT / "entrypoint.sh").read_text()
    launcher = (ROOT / "scripts" / "omegaclaw").read_text()
    assert "./src/das_skills.py" in imports
    assert "(das_skills.is_enabled)" in skills
    assert "(= (das-retrieve $body)" in skills
    assert "OMEGA_DAS_ENABLED OMEGA_DAS_PROXY_ORIGIN" in entrypoint
    assert '-e OMEGA_DAS_ENABLED="${OMEGA_DAS_ENABLED:-0}"' in launcher
    assert '-e OMEGA_DAS_PROXY_ORIGIN="${OMEGA_DAS_PROXY_ORIGIN:-http://read-proxy:8080}"' in launcher

    monkeypatch.delenv("OMEGA_DAS_ENABLED", raising=False)
    assert "das-retrieve" not in importlib.reload(helper).LLM_COMMANDS
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    assert "das-retrieve" in importlib.reload(helper).LLM_COMMANDS
    assert helper.balance_parentheses('das-retrieve {"tokens":["X"],"max_answers":1}') == (
        '((das-retrieve "{\\"tokens\\":[\\"X\\"],\\"max_answers\\":1}"))'
    )
    monkeypatch.delenv("OMEGA_DAS_ENABLED")
    importlib.reload(helper)
