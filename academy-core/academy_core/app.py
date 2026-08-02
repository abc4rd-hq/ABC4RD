import json
from typing import Any, Callable, Dict, Iterable, Optional, Tuple
from urllib.parse import parse_qs

from .errors import CoreError, ValidationError
from .service import AcademyCore


MAX_BODY_BYTES = 1024 * 1024


def _json_response(
    start_response: Callable, status: str, body: Dict[str, Any]
) -> Iterable[bytes]:
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(encoded))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [encoded]


def _read_json(environ: Dict[str, Any]) -> Dict[str, Any]:
    content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ValidationError("Content-Type must be application/json")
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError as error:
        raise ValidationError("invalid Content-Length") from error
    if length <= 0 or length > MAX_BODY_BYTES:
        raise ValidationError("JSON body must be between 1 byte and 1 MiB")
    raw = environ["wsgi.input"].read(length)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValidationError("body must be valid UTF-8 JSON") from error
    if not isinstance(body, dict):
        raise ValidationError("JSON body must be an object")
    return body


def create_app(
    database: str,
    live_payment_provider: Optional[str] = None,
    live_payment_gate_ref: Optional[str] = None,
) -> Callable:
    core = AcademyCore(database, live_payment_provider, live_payment_gate_ref)
    writes: Dict[Tuple[str, str], Callable] = {
        ("POST", "/v1/identities"): core.create_identity,
        ("POST", "/v1/consents"): core.record_consent,
        ("POST", "/v1/entitlements"): core.record_entitlement,
        ("POST", "/v1/events"): core.record_event,
        ("POST", "/v1/payment-ledger"): core.record_payment,
        ("POST", "/v1/reviews"): core.open_review,
        ("POST", "/v1/review-decisions"): core.decide_review,
        ("POST", "/v1/credentials"): core.register_credential,
    }

    def application(environ: Dict[str, Any], start_response: Callable):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        try:
            if method == "GET" and path == "/health":
                audit = core.verify_audit_chain()
                status = "ok" if audit["valid"] else "degraded"
                http_status = "200 OK" if audit["valid"] else "503 Service Unavailable"
                return _json_response(
                    start_response,
                    http_status,
                    {"status": status, "payment": core.payment_policy(), "audit": audit},
                )
            if method == "GET" and path == "/v1/audit/verify":
                return _json_response(start_response, "200 OK", core.verify_audit_chain())
            if method == "GET" and path == "/v1/audit":
                query = parse_qs(environ.get("QUERY_STRING", ""))
                try:
                    limit = int(query.get("limit", ["100"])[0])
                except ValueError as error:
                    raise ValidationError("limit must be an integer") from error
                return _json_response(start_response, "200 OK", core.list_audit(limit))
            if method == "GET" and path == "/v1/oversight-outbox":
                query = parse_qs(environ.get("QUERY_STRING", ""))
                try:
                    limit = int(query.get("limit", ["100"])[0])
                except ValueError as error:
                    raise ValidationError("limit must be an integer") from error
                return _json_response(
                    start_response, "200 OK", core.list_oversight_outbox(limit)
                )

            handler = writes.get((method, path))
            if handler is not None:
                body = _read_json(environ)
                idempotency_key = environ.get("HTTP_IDEMPOTENCY_KEY", "")
                return _json_response(
                    start_response, "201 Created", handler(body, idempotency_key)
                )
            return _json_response(
                start_response,
                "404 Not Found",
                {"error": "not_found", "message": "route was not found"},
            )
        except CoreError as error:
            labels = {400: "bad_request", 404: "not_found", 409: "conflict", 422: "validation_error"}
            return _json_response(
                start_response,
                "%d %s" % (error.status_code, _reason(error.status_code)),
                {"error": labels.get(error.status_code, "core_error"), "message": str(error)},
            )
        except Exception:
            return _json_response(
                start_response,
                "500 Internal Server Error",
                {"error": "internal_error", "message": "unexpected internal error"},
            )

    return application


def _reason(status_code: int) -> str:
    return {
        400: "Bad Request",
        404: "Not Found",
        409: "Conflict",
        422: "Unprocessable Entity",
    }.get(status_code, "Error")
