"""Recorded-LMS replay test harness — Task 3.9 / spec §9.

The ``recorded_lms`` fixture intercepts ``httpx`` calls and either replays a
stored response from ``tests/fixtures/lms_responses/<sha>.json`` (default) or
captures a live LMS response when ``RECORD_LMS=1`` is set in the env.

Hash key: sha256 of the canonical JSON over the request body's
``messages``, ``tools``, and ``response_format`` keys (sort_keys=True).
Transient fields (timestamps, request IDs) are excluded so the same logical
chat call always replays from the same fixture file.

Capture mode (``RECORD_LMS=1``) requires a live LMS server reachable at the
client's ``base_url``; the captured response body is written to disk at
``tests/fixtures/lms_responses/<sha>.json`` for future replay. M5+ tests
that need recorded transcripts can request this fixture and rely on the
files committed alongside the test code.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "lms_responses"


def _hash_request(body: dict[str, Any]) -> str:
    """Stable hash over the request fields that determine the response."""
    key = {
        "messages": body.get("messages"),
        "tools": body.get("tools"),
        "response_format": body.get("response_format"),
    }
    serialized = json.dumps(key, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_response_from_fixture(stored: dict[str, Any]) -> httpx.Response:
    headers = dict(stored.get("headers") or {})
    if "sse_chunks" in stored:
        # Streamed-form fixture: reconstruct the SSE bytes.
        text = "".join(stored["sse_chunks"])
        headers.setdefault("content-type", "text/event-stream")
        return httpx.Response(
            status_code=stored.get("status_code", 200),
            headers=headers,
            text=text,
        )
    body = stored.get("body")
    if body is None:
        return httpx.Response(
            status_code=stored.get("status_code", 200),
            headers=headers,
        )
    return httpx.Response(
        status_code=stored.get("status_code", 200),
        headers=headers,
        json=body,
    )


@pytest.fixture
def fixture_dir() -> Path:
    return _FIXTURES_DIR


@pytest.fixture
def recorded_lms(respx_mock: Any, fixture_dir: Path) -> Any:
    """Replay LMS responses from disk; capture if ``RECORD_LMS=1``.

    Replay mode (default): every POST /chat/completions is hashed by its
    canonical request body and replayed from
    ``tests/fixtures/lms_responses/<sha>.json``. Missing fixture → assertion
    error with the captured hash so the developer knows what to record.

    Capture mode (``RECORD_LMS=1``): forwards the request to the live LMS
    backend and writes the captured response to disk before returning it.
    Existing fixtures are NOT overwritten unless ``RECORD_LMS=overwrite``.
    """
    record_env = os.environ.get("RECORD_LMS", "")
    record = bool(record_env) and record_env != "0"
    overwrite = record_env == "overwrite"
    fixture_dir.mkdir(parents=True, exist_ok=True)

    def _live_send(req: httpx.Request) -> httpx.Response:
        """Send ``req`` to the real upstream, bypassing respx interception.

        respx hooks ``httpx``'s transport layer globally for the duration of
        the test, so a naive ``httpx.Client(...).send(req)`` recurses back
        into the mock router. We use stdlib ``http.client.HTTPConnection``
        directly — it speaks raw HTTP without going through httpx and is
        therefore not intercepted.

        Constraints (intentionally narrow):
        * ``http://`` only — LM Studio is always plaintext-localhost. We do
          not enable HTTPS to sidestep TLS-validation pitfalls of the stdlib
          ``HTTPSConnection`` API and to keep the recording surface minimal.
        * Loopback hosts only — refuses non-localhost targets so a misconfigured
          ``base_url`` cannot exfiltrate request bodies during recording.
        """
        import http.client

        scheme = (req.url.scheme or "").lower()
        if scheme != "http":
            raise ValueError(
                f"recorded_lms live mode only supports http://; got {scheme!r}"
            )
        host = req.url.host
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError(
                f"recorded_lms live mode only targets loopback; got host={host!r}"
            )
        port = req.url.port or 80
        path = req.url.raw_path.decode("ascii") or "/"
        conn = http.client.HTTPConnection(host, port, timeout=120.0)
        try:
            headers = {
                k: v
                for k, v in req.headers.items()
                if k.lower() not in ("host", "content-length")
            }
            conn.request(
                req.method, path, body=req.content or None, headers=headers
            )
            r = conn.getresponse()
            body_bytes = r.read()
            resp_headers = dict(r.getheaders())
            status = r.status
        finally:
            conn.close()
        return httpx.Response(
            status_code=status,
            headers=resp_headers,
            content=body_bytes,
        )

    def replay_or_capture(req: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(req.content.decode("utf-8")) if req.content else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        sha = _hash_request(body)
        fixture_path = fixture_dir / f"{sha}.json"

        if record:
            if fixture_path.exists() and not overwrite:
                # Replay rather than re-capture to avoid silent corruption
                # (per plan §3.9.5 pitfall: gate destructive overwrites).
                return _build_response_from_fixture(_load_fixture(fixture_path))
            live_resp = _live_send(req)
            try:
                live_resp.read()  # materialize body before transport closes
            except httpx.ResponseNotRead:
                pass
            payload: dict[str, Any] = {
                "request_hash": sha,
                "status_code": live_resp.status_code,
                "headers": dict(live_resp.headers),
            }
            ctype = live_resp.headers.get("content-type", "")
            if "event-stream" in ctype:
                # Re-chunk the SSE body so replay can reconstruct the stream.
                text = live_resp.text
                payload["sse_chunks"] = [
                    chunk + "\n\n" for chunk in text.split("\n\n") if chunk
                ]
            else:
                try:
                    payload["body"] = live_resp.json()
                except ValueError:
                    payload["body"] = {"_raw_text": live_resp.text}
            fixture_path.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
            return _build_response_from_fixture(payload)

        if not fixture_path.exists():
            raise AssertionError(
                f"No fixture for request hash {sha} ({req.method} {req.url}); "
                f"run with RECORD_LMS=1 to capture against a live LMS."
            )
        return _build_response_from_fixture(_load_fixture(fixture_path))

    respx_mock.route(host="localhost").mock(side_effect=replay_or_capture)
    return respx_mock
