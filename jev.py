"""TypeSafe JEV transport. Provider code stays outside Flow Steward Core."""

from __future__ import annotations

import http.client
import json
import math
import ssl
from typing import Any, cast

HOST = "api.typesafe.ai"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class JevError(Exception):
    def __init__(
        self, code: str, message: str, *, http_status: int | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def failure(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "error": message,
        "errors": [{"code": code, "message": message}],
    }


def _require(
    condition: bool,
    code: str = "invalid_input",
    message: str = "Invalid JEV request input.",
) -> None:
    if not condition:
        raise JevError(code, message)


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise JevError(
            "invalid_input", "Input must contain finite UTF-8 JSON values."
        ) from exc


def _request(input_: dict[str, Any], model: str) -> dict[str, Any]:
    state = input_.get("state")
    questions = input_.get("questions")
    _require(isinstance(state, (str, dict, list)))
    _require(isinstance(questions, dict) and 1 <= len(questions) <= 256)
    _require(isinstance(model, str) and 0 < len(model.strip()) <= 256)
    questions = cast(dict, questions)
    for name, question in questions.items():
        _require(isinstance(name, str) and bool(name) and isinstance(question, dict))
        kind = question.get("type")
        _require(kind in {"choice", "score", "noul"})
        _require(not (set(question) - {"type", "instructions", "criteria"}))
        _require(isinstance(question.get("instructions"), (str, dict, list)))
        criteria = question.get("criteria")
        if kind == "choice":
            _require(isinstance(criteria, dict) and 1 <= len(criteria) <= 255)
            _require(all(isinstance(k, str) and k for k in criteria))
        elif kind == "score":
            _require(isinstance(criteria, list) and 2 <= len(criteria) <= 10)
        elif criteria is not None:
            _require(
                isinstance(criteria, dict) and not (set(criteria) - {"true", "false"})
            )
        if criteria is not None:
            values = criteria.values() if isinstance(criteria, dict) else criteria
            _require(all(v is None or isinstance(v, (str, dict, list)) for v in values))
    request = {"model": model.strip(), "state": state, "questions": questions}
    _require(
        len(_json_bytes(request)) <= MAX_BODY_BYTES,
        message="JEV request exceeds the connector's 2 MiB limit.",
    )
    return request


def _connection(payload: dict, input_: dict) -> tuple[str, str, int]:
    action = payload.get("action") or {}
    target = action.get("target") or payload.get("target") or {}
    connection = target.get("connection") if isinstance(target, dict) else None
    _require(
        isinstance(connection, dict),
        "invalid_connection",
        "Select a JEV project connection.",
    )
    connection = cast(dict, connection)
    identity = connection.get("connection_id") or connection.get(
        "project_connection_id"
    )
    kind = connection.get("connection_type_id") or connection.get("connection_type")
    _require(
        bool(identity)
        and input_.get("connection_ref") == identity
        and kind == "typesafe_jev",
        "invalid_connection",
        "The selected JEV project connection does not match this invocation.",
    )
    config = connection.get("config") or connection.get("connection_config") or {}
    secrets = connection.get("secrets") or {}
    _require(
        isinstance(config, dict) and isinstance(secrets, dict), "invalid_connection"
    )
    key = input_["api_key"] if "api_key" in input_ else secrets.get("api_key")
    _require(
        isinstance(key, str)
        and bool(key.strip())
        and key.isascii()
        and not any(ord(c) < 33 or ord(c) == 127 for c in key),
        message="Configure a valid TypeSafe API key on the connection or workflow secret input.",
    )
    timeout = config.get("timeout_seconds", 30)
    _require(
        isinstance(timeout, int)
        and not isinstance(timeout, bool)
        and 1 <= timeout <= 60,
        "invalid_connection",
        "Connection timeout must be an integer from 1 to 60 seconds.",
    )
    return (
        cast(str, key),
        input_.get("model", config.get("model") or "jev-latest"),
        timeout,
    )


def _http(
    method: str, path: str, *, key: str, timeout: int, request: Any = None
) -> Any:
    connection = None
    try:
        connection = http.client.HTTPSConnection(HOST, timeout=timeout)
        connection.request(
            method,
            path,
            body=_json_bytes(request) if request is not None else None,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        response = connection.getresponse()
        status = response.status
        if status != 200:
            code = "request_rejected"
            if status in {401, 403}:
                code = "authentication_failed"
            elif status == 429:
                code = "rate_limited"
            elif 300 <= status < 400:
                code = "redirect_refused"
            elif status >= 500:
                code = "upstream_unavailable"
            raise JevError(
                code, f"TypeSafe returned HTTP {status}.", http_status=status
            )
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        _require(
            len(raw) <= MAX_RESPONSE_BYTES,
            "invalid_response",
            "TypeSafe response exceeds 4 MiB.",
        )
        _require(
            key.encode("ascii") not in raw,
            "invalid_response",
            "TypeSafe returned a credential-bearing response.",
        )
        try:
            return json.loads(raw, parse_constant=lambda _: None)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise JevError(
                "invalid_response", "TypeSafe returned invalid JSON."
            ) from exc
    except TimeoutError as exc:
        raise JevError(
            "upstream_timeout", "The TypeSafe request timed out; it was not retried."
        ) from exc
    except ssl.SSLError as exc:
        raise JevError("tls_failed", "TypeSafe TLS verification failed.") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise JevError(
            "connection_failed", "Could not reach the TypeSafe API."
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _number(value: Any, low: float, high: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and low <= value <= high
    )


def _answer(response: Any, request: dict) -> dict:
    def require(condition: bool) -> None:
        _require(
            condition,
            "invalid_response",
            "TypeSafe returned an unexpected decision schema.",
        )

    require(isinstance(response, dict))
    model, answers, usage = (
        response.get("model"),
        response.get("answers"),
        response.get("usage"),
    )
    require(isinstance(model, str) and 0 < len(model) <= 256)
    require(isinstance(answers, dict) and set(answers) == set(request["questions"]))
    require(
        isinstance(usage, dict)
        and all(
            isinstance(usage.get(k), int)
            and not isinstance(usage[k], bool)
            and usage[k] >= 0
            for k in ("input_tokens", "output_tokens")
        )
    )
    result = {}
    for name, question in request["questions"].items():
        answer = answers[name]
        kind = question["type"]
        require(isinstance(answer, dict) and answer.get("type") == kind)
        if kind == "noul":
            require(_number(answer.get("noul"), 0, 1))
            result[name] = {"type": kind, "noul": answer["noul"]}
            continue
        require(_number(answer.get("confidence"), 0, 1))
        probabilities = answer.get("probabilities")
        keys = (
            set(question["criteria"])
            if kind == "choice"
            else {str(i) for i in range(len(question["criteria"]))}
        )
        require(isinstance(probabilities, dict) and set(probabilities) == keys)
        require(all(_number(v, 0, 1) for v in probabilities.values()))
        require(abs(sum(probabilities.values()) - 1) < 0.02)
        value_key = "choice" if kind == "choice" else "score"
        value = answer.get(value_key)
        require(
            isinstance(value, str) and value in keys
            if kind == "choice"
            else _number(value, 0, len(keys) - 1)
        )
        result[name] = {
            "type": kind,
            value_key: value,
            "confidence": answer["confidence"],
            "probabilities": probabilities,
        }
        if kind == "score":
            legend = answer.get("legend")
            require(isinstance(legend, dict) and set(legend) == keys)
            result[name]["legend"] = legend
    return {
        "model": model,
        "answers": result,
        "usage": {k: usage[k] for k in ("input_tokens", "output_tokens")},
    }


def handle_payload(payload: Any) -> dict[str, Any]:
    try:
        _require(
            isinstance(payload, dict)
            and payload.get("contract_version") == "extension_host_v2"
            and payload.get("mode") == "action",
            "invalid_payload",
            "Expected an extension_host_v2 action envelope.",
        )
        action = payload.get("action")
        _require(isinstance(action, dict), "invalid_payload")
        operation = action.get("action_id") or action.get("operation_id")
        _require(
            operation in {"evaluate", "test_connection"},
            "invalid_payload",
            "Unknown TypeSafe operation.",
        )
        input_ = action.get("input")
        _require(isinstance(input_, dict), "invalid_payload")
        allowed = (
            {"connection_ref", "api_key", "state", "questions", "model"}
            if operation == "evaluate"
            else {"connection_ref"}
        )
        _require(not (set(input_) - allowed))
        key, model, timeout = _connection(payload, input_)
        if operation == "test_connection":
            response = _http("GET", "/v1/models", key=key, timeout=timeout)
            models = response.get("models") if isinstance(response, dict) else None
            _require(
                isinstance(models, list)
                and bool(models)
                and all(
                    isinstance(m, dict) and isinstance(m.get("name"), str)
                    for m in models
                ),
                "invalid_response",
                "TypeSafe returned an unexpected model list.",
            )
            return {
                "ok": True,
                "result": {"models": [m["name"] for m in cast(list, models)]},
            }
        request = _request(input_, model)
        result = _answer(
            _http("POST", "/v1/systemone", key=key, timeout=timeout, request=request),
            request,
        )
        return {"ok": True, "result": result}
    except JevError as exc:
        result = failure(exc.code, str(exc))
        # A validation rejection did not perform the evaluation. These explicit
        # facts let the host use the authored error edge instead of escalating
        # an unknown operation outcome. Timeouts and other failures stay unknown.
        if exc.http_status in {400, 422}:
            result.update(
                failure_class="validation",
                retryable=False,
                external_effect_status="failed",
                definitely_no_external_effect=True,
                http_status=exc.http_status,
            )
        return result
    except Exception:
        return failure(
            "internal_error", "TypeSafe connector could not complete this request."
        )
