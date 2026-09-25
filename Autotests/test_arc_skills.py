import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import arc_skills  # noqa: E402
from src import helper  # noqa: E402


class _Response:
    status = 200

    def __init__(self, body=b'{"ok":true}'):
        self.body = body

    def read(self, size=-1):
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@pytest.mark.parametrize(
    "url",
    [
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks/next",
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks",
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks/a1b2c3d4",
    ],
)
def test_arc_read_allows_only_documented_task_urls(monkeypatch, url):
    seen = []
    monkeypatch.setattr(arc_skills, "_request", lambda req: (seen.append(req) or (200, "{}")))
    assert arc_skills.arc_read(url).startswith("ARC_READ_OK status=200")
    assert seen[0].method == "GET"


@pytest.mark.parametrize(
    "url",
    [
        "https://172.17.0.1:18080/arc/v1/evaluation/tasks",
        "http://evil.test:18080/arc/v1/evaluation/tasks",
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks/123",
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks/a1B2c3D4",
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks/deadbeef/submissions",
        "http://172.18.0.1:18080/arc/v1/evaluation/tasks/deadbeef",
        "http://arc-proxy-host:18080/arc/v1/evaluation/tasks/deadbeef",
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks?next=true",
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks/../secrets",
    ],
)
def test_arc_read_denies_everything_else(monkeypatch, url):
    monkeypatch.setattr(arc_skills, "_request", lambda _req: pytest.fail("network called"))
    assert arc_skills.arc_read(url).startswith("ARC_READ_FAILED:")


def test_arc_submit_posts_exact_proxy_schema_and_intent_without_bearer(monkeypatch):
    captured = []
    monkeypatch.setattr(arc_skills, "_request", lambda req: (captured.append(req) or (201, "accepted")))
    url = "http://172.17.0.1:18080/arc/v1/evaluation/tasks/deadbeef/submissions"
    body = {"outputs": [[[0, 9], [2, 3]]], "reasoning": "color mapping"}
    result = arc_skills.arc_submit(url, json.dumps(body))
    assert result == "ARC_SUBMIT_OK status=201 body=accepted"
    request = captured[0]
    assert request.method == "POST"
    assert request.data == b'{"outputs":[[[0,9],[2,3]]],"reasoning":"color mapping"}'
    headers = {key.lower(): value for key, value in request.header_items()}
    assert "authorization" not in headers
    assert headers["x-arc-submission-intent"] == arc_skills.SUBMISSION_INTENT


@pytest.mark.parametrize(
    "url",
    [
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks/deadbeef",
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks/next/submissions",
        "http://localhost:18080/arc/v1/evaluation/tasks/deadbeef/submissions",
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks/deadbeef/submissions?force=1",
    ],
)
def test_arc_submit_denies_other_urls(monkeypatch, url):
    monkeypatch.setattr(arc_skills, "_request", lambda _req: pytest.fail("network called"))
    assert arc_skills.arc_submit(url, '{"answer":[[1]]}').startswith("ARC_SUBMIT_FAILED:")


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        "{}",
        '{"outputs":[],"reasoning":"r"}',
        '{"outputs":[[[1],[2,3]]],"reasoning":"r"}',
        '{"outputs":[[[10]]],"reasoning":"r"}',
        '{"outputs":[[[true]]],"reasoning":"r"}',
        '{"outputs":[[[1]]],"outputs":[[[2]]],"reasoning":"r"}',
        '{"outputs":[[[1]]],"reasoning":""}',
        '{"outputs":[[[1]]],"reasoning":"r","authorization":"Bearer secret"}',
        '{"url":"http://evil.test"}',
    ],
)
def test_arc_submit_denies_invalid_bodies_before_network(monkeypatch, body):
    monkeypatch.setattr(arc_skills, "_request", lambda _req: pytest.fail("network called"))
    url = "http://172.17.0.1:18080/arc/v1/evaluation/tasks/1234abcd/submissions"
    assert arc_skills.arc_submit(url, body).startswith("ARC_SUBMIT_FAILED: invalid body:")


def test_arc_read_bounds_response(monkeypatch):
    oversized = b"x" * (arc_skills.MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(arc_skills.urllib.request, "build_opener", lambda *_args: _Opener(oversized))
    url = "http://172.17.0.1:18080/arc/v1/evaluation/tasks"
    assert "response exceeds" in arc_skills.arc_read(url)


def test_arc_python_module_and_metta_skills_are_registered():
    imports = (ROOT / "lib_omegaclaw.metta").read_text()
    skills = (ROOT / "src" / "skills.metta").read_text()

    assert "./src/arc_skills.py" in imports
    assert "(= (arc-read $url)" in skills
    assert "(= (arc-submit $url $body)" in skills


def test_arc_submit_survives_llm_command_normalization_as_two_arguments():
    command = (
        "arc-submit "
        "http://172.17.0.1:18080/arc/v1/evaluation/tasks/deadbeef/submissions "
        '{"outputs":[[[1,2],[3,4]]],"reasoning":"rotation"}'
    )

    assert helper.balance_parentheses(command) == (
        '((arc-submit '
        '"http://172.17.0.1:18080/arc/v1/evaluation/tasks/deadbeef/submissions" '
        '"{\\"outputs\\":[[[1,2],[3,4]]],\\"reasoning\\":\\"rotation\\"}"))'
    )


class _Opener:
    def __init__(self, body):
        self.body = body

    def open(self, _request, timeout):
        assert timeout == arc_skills.REQUEST_TIMEOUT_SECONDS
        return _Response(self.body)
