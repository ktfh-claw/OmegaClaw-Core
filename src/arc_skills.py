"""Narrow, proxy-only skills for ARC evaluation tasks.

This module deliberately does not accept arbitrary hosts, paths, headers, or
HTTP methods.  Authentication and submission authorization belong to the host
proxy at ``ARC_PROXY_ORIGIN`` and must not be recreated in the agent container.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit


ARC_PROXY_ORIGIN = "http://172.17.0.1:18080"
_TASK_PATH = re.compile(r"/arc/v1/evaluation/tasks/[0-9a-f]{8}")
_SUBMISSION_PATH = re.compile(
    r"/arc/v1/evaluation/tasks/[0-9a-f]{8}/submissions"
)
_READ_PATHS = {
    "/arc/v1/evaluation/tasks/next",
    "/arc/v1/evaluation/tasks",
}
MAX_RESPONSE_BYTES = 256 * 1024
MAX_REQUEST_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 20
SUBMISSION_INTENT = "explicit-user-authorized"


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _path_for(url: str) -> str | None:
    """Return a path only for a canonical URL on the fixed ARC proxy."""
    if not isinstance(url, str) or not url or url.strip() != url:
        return None
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "172.17.0.1"
        or port != 18080
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.netloc != "172.17.0.1:18080"
    ):
        return None
    return parsed.path


def _read_body(response: Any) -> str:
    data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
    return data.decode("utf-8", errors="replace")


def _request(request: urllib.request.Request) -> tuple[int, str]:
    opener = urllib.request.build_opener(_NoRedirects())
    with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return int(response.status), _read_body(response)


def _failure(operation: str, message: str) -> str:
    return f"{operation}_FAILED: {message}"


def arc_read(url: str) -> str:
    """GET one of the exact, allowlisted ARC task endpoints."""
    path = _path_for(url)
    if path not in _READ_PATHS and not (path and _TASK_PATH.fullmatch(path)):
        return _failure("ARC_READ", "URL is not an allowed ARC proxy task endpoint")

    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        status, body = _request(request)
        return f"ARC_READ_OK status={status} body={body}"
    except urllib.error.HTTPError as exc:
        try:
            detail = _read_body(exc)
        except ValueError as size_error:
            detail = str(size_error)
        return _failure("ARC_READ", f"proxy returned HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _failure("ARC_READ", str(exc))


def _is_grid(value: Any) -> bool:
    if not isinstance(value, list) or not value or len(value) > 30:
        return False
    width = len(value[0]) if isinstance(value[0], list) else 0
    if width < 1 or width > 30:
        return False
    return all(
        isinstance(row, list)
        and len(row) == width
        and all(type(cell) is int and 0 <= cell <= 9 for cell in row)
        for row in value
    )


def _valid_outputs(value: Any) -> bool:
    return (
        isinstance(value, list)
        and 1 <= len(value) <= 3
        and all(_is_grid(grid) for grid in value)
    )


def _submission_bytes(body: str) -> bytes:
    if not isinstance(body, str):
        raise ValueError("body must be a JSON string")
    encoded = body.encode("utf-8")
    if not encoded or len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError(f"body must be 1..{MAX_REQUEST_BYTES} UTF-8 bytes")
    try:
        def reject_duplicate_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate JSON field: {key}")
                result[key] = value
            return result

        payload = json.loads(body, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"body is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict) or set(payload) != {"outputs", "reasoning"}:
        raise ValueError("body must contain exactly outputs and reasoning")
    if not _valid_outputs(payload["outputs"]):
        raise ValueError("outputs must contain 1..3 rectangular ARC grids up to 30x30 with cells 0..9")
    if not isinstance(payload["reasoning"], str) or not 1 <= len(payload["reasoning"]) <= 10_000:
        raise ValueError("reasoning must be a non-empty string of at most 10000 characters")
    # Emit normalized JSON so duplicate keys, unusual whitespace, and other
    # unvalidated input details are never forwarded.
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def arc_submit(url: str, body: str) -> str:
    """POST a validated ARC answer to an exact submission endpoint."""
    path = _path_for(url)
    if not path or not _SUBMISSION_PATH.fullmatch(path):
        return _failure("ARC_SUBMIT", "URL is not an allowed ARC proxy submission endpoint")
    try:
        data = _submission_bytes(body)
    except ValueError as exc:
        return _failure("ARC_SUBMIT", f"invalid body: {exc}")

    # The intent marker identifies this deliberately selected skill path; it is
    # not a credential or authorization. The host campaign controller remains
    # the sole authority for whether a submission may proceed.
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-ARC-Submission-Intent": SUBMISSION_INTENT,
        },
    )
    try:
        status, response_body = _request(request)
        return f"ARC_SUBMIT_OK status={status} body={response_body}"
    except urllib.error.HTTPError as exc:
        try:
            detail = _read_body(exc)
        except ValueError as size_error:
            detail = str(size_error)
        return _failure("ARC_SUBMIT", f"proxy returned HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _failure("ARC_SUBMIT", str(exc))
