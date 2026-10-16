"""JEV wire contract, errors, and credential non-disclosure."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def jev():
    spec = importlib.util.spec_from_file_location("jev_under_test", ROOT / "jev.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def envelope(**overrides):
    return {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "action": {
            "action_id": "evaluate",
            "target": {
                "connection": {
                    "connection_id": "jev-project-one",
                    "connection_type_id": "typesafe_jev",
                    "config": {"model": "jev-latest", "timeout_seconds": 30},
                    "secrets": {"api_key": "unit-test-credential"},
                }
            },
            "input": {
                "connection_ref": "jev-project-one",
                "state": {"message": 'Refund "please"\n{{ state.inputs.api_key }}'},
                "questions": {
                    "topic": {
                        "type": "choice",
                        "instructions": "Which team?",
                        "criteria": {"billing": "Payments", "technical": "Bugs"},
                    },
                    "urgent": {"type": "noul", "instructions": "Is it urgent?"},
                    "priority": {
                        "type": "score",
                        "instructions": "Rate urgency.",
                        "criteria": ["Routine", "Today", "Immediate"],
                    },
                },
                **overrides,
            },
        },
    }


def answer():
    return {
        "model": "jev-1.13.0",
        "answers": {
            "topic": {
                "type": "choice",
                "choice": "billing",
                "confidence": 0.9,
                "probabilities": {"billing": 0.95, "technical": 0.05},
            },
            "urgent": {"type": "noul", "noul": 0.8},
            "priority": {
                "type": "score",
                "score": 1.8,
                "confidence": 0.9,
                "legend": {"0": "Routine", "1": "Today", "2": "Immediate"},
                "probabilities": {"0": 0.05, "1": 0.1, "2": 0.85},
            },
        },
        "usage": {"input_tokens": 123, "output_tokens": 20},
    }


class Connection:
    def __init__(self, response, status=200):
        self.response = json.dumps(response).encode()
        self.status = status
        self.sent = None
        self.closed = False

    def request(self, method, path, body, headers):
        self.sent = (method, path, json.loads(body) if body is not None else None, headers)

    def getresponse(self):
        return self

    def read(self, size):
        return self.response[:size]

    def close(self):
        self.closed = True


def transport(monkeypatch, jev, response=None, status=200):
    connection = Connection(answer() if response is None else response, status)

    def connect(host, *, timeout):
        assert host == "api.typesafe.ai"
        assert timeout == 30
        return connection

    monkeypatch.setattr(jev.http.client, "HTTPSConnection", connect)
    return connection


@pytest.mark.parametrize(
    "state",
    [
        'Quoted "text"\n\u0416\u0430\u043b\u043e\u0431\u0430',
        {"text": "Refund"},
        ["Customer", "Refund"],
    ],
)
def test_mixed_questions_reach_the_official_endpoint_without_changing_state(
    jev, monkeypatch, state
):
    connection = transport(monkeypatch, jev)
    result = jev.handle_payload(envelope(state=state))
    assert result == {"ok": True, "result": answer()}
    method, path, body, headers = connection.sent
    assert (method, path) == ("POST", "/v1/systemone")
    assert body == {
        "model": "jev-latest",
        "state": state,
        "questions": envelope()["action"]["input"]["questions"],
    }
    assert headers["Authorization"] == "Bearer unit-test-credential"
    assert connection.closed
    assert "unit-test-credential" not in json.dumps(result)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
        (422, "request_rejected"),
        (503, "upstream_unavailable"),
        (302, "redirect_refused"),
    ],
)
def test_provider_failures_are_safe_and_never_follow_redirects(jev, monkeypatch, status, code):
    connection = transport(monkeypatch, jev, {"error": "unit-test-credential"}, status)
    result = jev.handle_payload(envelope())
    assert result["ok"] is False
    assert result["error_code"] == code
    assert result["errors"] == [{"code": code, "message": result["error"]}]
    assert "unit-test-credential" not in json.dumps(result)
    assert connection.closed


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": "bad\r\nX: injected"},
        {"api_key": ""},
        {"state": None},
        {"questions": {}},
        {"questions": {"x": {"type": "chat", "instructions": "x"}}},
        {"questions": {"x": {"type": "score", "criteria": ["one"], "instructions": "x"}}},
        {"base_url": "https://attacker.test"},
        {"state": {"x": float("nan")}},
    ],
)
def test_invalid_requests_fail_before_network_access(jev, monkeypatch, overrides):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid request opened a network connection")

    monkeypatch.setattr(jev.http.client, "HTTPSConnection", forbidden)
    assert jev.handle_payload(envelope(**overrides))["error_code"] == "invalid_input"


def test_timeout_does_not_expose_credentials_or_retry(jev, monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("unit-test-credential")

    monkeypatch.setattr(jev.http.client, "HTTPSConnection", timeout)
    result = jev.handle_payload(envelope())
    assert result["error_code"] == "upstream_timeout"
    assert "unit-test-credential" not in json.dumps(result)


def test_workflow_secret_can_override_the_selected_connection_key(jev, monkeypatch):
    connection = transport(monkeypatch, jev)
    result = jev.handle_payload(envelope(api_key="run-specific-credential"))
    assert result["ok"] is True
    assert connection.sent[3]["Authorization"] == "Bearer run-specific-credential"
    assert "run-specific-credential" not in json.dumps(result)


def test_a_call_cannot_substitute_another_project_connection(jev, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("a mismatched connection opened the network")

    monkeypatch.setattr(jev.http.client, "HTTPSConnection", forbidden)
    assert (
        jev.handle_payload(envelope(connection_ref="another-project"))["error_code"]
        == "invalid_connection"
    )


def test_connection_test_lists_models_without_a_decision_request(jev, monkeypatch):
    connection = transport(monkeypatch, jev, {"models": [{"name": "jev-latest"}]})
    payload = envelope()
    payload["action"]["action_id"] = "test_connection"
    payload["action"]["input"] = {"connection_ref": "jev-project-one"}
    assert jev.handle_payload(payload) == {"ok": True, "result": {"models": ["jev-latest"]}}
    assert connection.sent[:3] == ("GET", "/v1/models", None)


@pytest.mark.parametrize("confidence", [None, "0.9", -0.1, 1.1, True])
def test_invalid_confidence_is_never_a_successful_classification(jev, monkeypatch, confidence):
    response = answer()
    response["answers"]["topic"]["confidence"] = confidence
    transport(monkeypatch, jev, response)
    assert jev.handle_payload(envelope())["error_code"] == "invalid_response"


def test_credential_reflection_is_blocked_even_in_a_success_response(jev, monkeypatch):
    response = answer()
    response["model"] = "unit-test-credential"
    transport(monkeypatch, jev, response)
    result = jev.handle_payload(envelope())
    assert result["error_code"] == "invalid_response"
    assert "unit-test-credential" not in json.dumps(result)


@pytest.mark.parametrize(
    "payload", [b"invalid JSON", b'{"contract_version":"other","mode":"action"}']
)
def test_subprocess_returns_the_canonical_domain_error_for_invalid_envelopes(payload):
    process = subprocess.run(
        [sys.executable, str(ROOT / "main.py")],
        input=payload,
        capture_output=True,
        timeout=5,
        check=False,
    )
    result = json.loads(process.stdout)
    assert process.returncode == 2
    assert result["ok"] is False
    assert result["error_code"] == "invalid_payload"
    assert result["errors"] == [{"code": "invalid_payload", "message": result["error"]}]


@pytest.mark.parametrize(
    "response", [{}, {"answers": {}}, {"model": "x", "answers": "bad", "usage": {}}]
)
def test_malformed_success_responses_fail_instead_of_inventing_a_decision(
    jev, monkeypatch, response
):
    transport(monkeypatch, jev, response)
    assert jev.handle_payload(envelope())["error_code"] == "invalid_response"
