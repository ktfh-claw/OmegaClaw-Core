import importlib
import io
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
    assert das_skills.das_retrieve('{"metta": "(Inheritance (Concept \\"x\\") $X)"}') == (
        "DAS_RETRIEVE_FAILED: adapter is disabled"
    )


def test_metta_mode_round_trip(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    monkeypatch.setenv("OMEGA_DAS_PROXY_ORIGIN", "http://read-proxy:8080")
    seen = []
    response = _Response(
        {"mode": "metta", "answers": [{"assignments": {"X": "abc"}, "handles": ["h1"]}], "count": 1, "truncated": False}
    )
    monkeypatch.setattr(
        das_skills.urllib.request,
        "build_opener",
        lambda handler: (_assert_no_redirect_handler(handler) or _Opener(response, seen)),
    )
    body = '{ "metta": "(Inheritance (Concept \\"x402\\") $X)", "max_answers": 2 }'
    result = das_skills.das_retrieve(body)
    assert result.startswith("DAS_RETRIEVE_OK untrusted=true count=1 truncated=false results=")
    assert "h1" in result
    request, timeout = seen[0]
    assert request.full_url == "http://read-proxy:8080/v1/query"
    assert request.method == "POST"
    assert json.loads(request.data) == {"metta": "(Inheritance (Concept \"x402\") $X)", "max_answers": 2}
    assert timeout == das_skills.REQUEST_TIMEOUT_SECONDS
    assert request.get_header("Authorization") is None


def test_query_alias_normalizes_to_metta(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    seen = []
    response = _Response({"mode": "metta", "answers": [], "count": 0, "truncated": False})
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: _Opener(response, seen))
    result = das_skills.das_retrieve('{"query": "(Inheritance (Concept \\"Fetch.ai\\") $X)"}')
    assert result.startswith("DAS_RETRIEVE_OK untrusted=true count=0")
    request, _timeout = seen[0]
    assert json.loads(request.data) == {"metta": "(Inheritance (Concept \"Fetch.ai\") $X)", "max_answers": 10}


def test_tokens_mode_round_trip(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    seen = []
    response = _Response(
        {"mode": "structural", "answers": [{"assignments": {"V1": "n1"}, "handles": ["h1"]}], "count": 1, "truncated": False}
    )
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: _Opener(response, seen))
    tokens = ["LINK_TEMPLATE", "Expression", "2", "VARIABLE", "V1", "NODE", "Symbol", '"x402"']
    result = das_skills.das_retrieve(json.dumps({"tokens": tokens, "max_answers": 3}))
    assert result.startswith("DAS_RETRIEVE_OK untrusted=true count=1")
    request, _timeout = seen[0]
    assert json.loads(request.data) == {"tokens": tokens, "max_answers": 3}


def _assert_no_redirect_handler(handler):
    assert isinstance(handler, das_skills._NoRedirects)


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        "{}",
        '{"metta": "(Inheritance (Concept \\"x\\") $X)", "query": "(Evaluation $P $V)"}',
        '{"metta": "(Inheritance (Concept \\"x\\") $X)", "tokens": ["X"]}',
        '{"query": "(Evaluation $P $V)", "tokens": ["X"]}',
        '{"metta": "(Inheritance (Concept \\"x\\") $X)", "max_answers": 0}',
        '{"metta": "(Inheritance (Concept \\"x\\") $X)", "max_answers": true}',
        '{"metta": "(Inheritance (Concept \\"x\\") $X)", "max_answers": 1, "url": "http://evil.test"}',
        '{"metta": "(Inheritance (Concept \\"x\\") (Concept \\"y\\"))"}',
        '{"metta": "Inheritance (Concept \\"x\\") $X"}',
        '{"metta": ""}',
        '{"metta": "(Inheritance (Concept \\"x\\") $X)", "metta": "(Evaluation $P $V)"}',
    ],
)
def test_invalid_input_is_rejected_before_network(monkeypatch, body):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: pytest.fail("network called"))
    assert das_skills.das_retrieve(body).startswith("DAS_RETRIEVE_FAILED:")


def test_ground_pattern_guidance_before_network(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: pytest.fail("network called"))
    message = das_skills.das_retrieve('{"metta": "(Inheritance (Concept \\"x\\") (Concept \\"y\\"))"}')
    assert "$variable" in message
    assert message.startswith("DAS_RETRIEVE_FAILED:")


def test_oversized_input_is_rejected_before_network(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: pytest.fail("network called"))
    body = " " * das_skills.MAX_REQUEST_BYTES + '{"metta": "(Inheritance (Concept \\"x\\") $X)"}'
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
    assert "origin" in das_skills.das_retrieve('{"metta": "(Inheritance (Concept \\"x\\") $X)"}')


@pytest.mark.parametrize(
    "payload",
    [
        {"answers": [], "count": 1, "truncated": False},
        {"answers": [1, 2], "count": 2, "truncated": False},
        {"answers": [], "count": 0, "truncated": 0},
        {"results": [], "count": 0, "truncated": False},
        {"mode": "metta", "answers": [], "count": 0},
        {"mode": "metta", "answers": [], "count": 0, "truncated": False, "extra": 1},
    ],
)
def test_invalid_response_schema_is_rejected(monkeypatch, payload):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    monkeypatch.setattr(
        das_skills.urllib.request,
        "build_opener",
        lambda *_: _Opener(_Response(payload), []),
    )
    assert das_skills.das_retrieve('{"metta": "(Inheritance (Concept \\"x\\") $X)"}').startswith(
        "DAS_RETRIEVE_FAILED:"
    )


def test_malformed_response_is_rejected(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    response = _Response({})
    response.body = b'{"answers":'
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: _Opener(response, []))
    assert das_skills.das_retrieve('{"metta": "(Inheritance (Concept \\"x\\") $X)"}') == (
        "DAS_RETRIEVE_FAILED: proxy response is not valid UTF-8 JSON"
    )


def test_response_answer_count_is_bounded(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    response = _Response({"mode": "metta", "answers": [{}, {}], "count": 2, "truncated": True})
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: _Opener(response, []))
    assert das_skills.das_retrieve('{"metta": "(Inheritance (Concept \\"x\\") $X)", "max_answers": 1}') == (
        "DAS_RETRIEVE_FAILED: proxy returned too many answers"
    )


def test_response_size_is_bounded(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    response = _Response({"mode": "metta", "answers": [], "count": 0, "truncated": False})
    response.body = b"x" * (das_skills.MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: _Opener(response, []))
    assert "response exceeds" in das_skills.das_retrieve('{"metta": "(Inheritance (Concept \\"x\\") $X)"}')


def test_proxy_error_body_is_surfaced(monkeypatch):
    monkeypatch.setenv("OMEGA_DAS_ENABLED", "1")
    error = urllib.error.HTTPError(
        "http://read-proxy:8080/v1/query",
        400,
        "Bad Request",
        Message(),
        io.BytesIO(json.dumps({"error": "structural queries must start with LINK_TEMPLATE"}).encode()),
    )
    monkeypatch.setattr(das_skills.urllib.request, "build_opener", lambda *_: _Opener(error, []))
    result = das_skills.das_retrieve('{"tokens": ["decentralized", "AI"], "max_answers": 10}')
    assert result == (
        "DAS_RETRIEVE_FAILED: proxy returned HTTP 400: structural queries must start with LINK_TEMPLATE"
    )


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
    assert das_skills.das_retrieve('{"metta": "(Inheritance (Concept \\"x\\") $X)"}') == expected


def test_registration_and_helper_gating(monkeypatch):
    imports = (ROOT / "lib_omega.metta").read_text()
    skills = (ROOT / "src" / "skills.metta").read_text()
    entrypoint = (ROOT / "entrypoint.sh").read_text()
    launcher = (ROOT / "scripts" / "omega").read_text()
    assert "./src/das_skills.py" in imports
    assert "(das_skills.is_enabled)" in skills
    assert "(= (das-retrieve $body)" in skills
    assert "OMEGA_DAS_ENABLED OMEGA_DAS_PROXY_ORIGIN" in entrypoint
    assert '-e OMEGA_DAS_ENABLED="${OMEGA_DAS_ENABLED:-0}"' in launcher
    assert '-e OMEGA_DAS_PROXY_ORIGIN="${OMEGA_DAS_PROXY_ORIGIN:-http://read-proxy:8080}"' in launcher

    assert "!(if (py-call (das_skills.is_enabled))" in skills
    assert "(add-skill das-retrieve" in skills


def test_arc_lane_is_removed():
    """Fork cleanup (2026-10-20): the ARC campaign lane is archived, not deployed."""
    skills = (ROOT / "src" / "skills.metta").read_text()
    imports = (ROOT / "lib_omega.metta").read_text()
    assert "arc-read" not in skills
    assert "arc-submit" not in skills
    assert "arc_skills" not in imports
    assert not (ROOT / "src" / "arc_skills.py").exists()
