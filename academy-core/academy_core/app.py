import hmac
import json
from typing import Any, Callable, Dict, Iterable, Optional, Tuple
from urllib.parse import parse_qs

from .errors import AuthenticationError, CoreError, ValidationError
from .payments.lemonsqueezy import (
    LemonSqueezyClient,
    LemonSqueezyError,
    Transport as LemonSqueezyTransport,
    process_webhook as process_lemonsqueezy_webhook,
)
from .payments.nowpayments import (
    NowPaymentsClient,
    NowPaymentsError,
    Transport,
    process_ipn,
)
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


def _read_json_with_raw(environ: Dict[str, Any]) -> Tuple[Dict[str, Any], bytes]:
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
    return body, raw


def _read_json(environ: Dict[str, Any]) -> Dict[str, Any]:
    body, _ = _read_json_with_raw(environ)
    return body


def create_app(
    database: str,
    live_payment_provider: Optional[str] = None,
    live_payment_gate_ref: Optional[str] = None,
    nowpayments_ipn_secret: Optional[str] = None,
    nowpayments_sandbox: bool = True,
    nowpayments_api_key: Optional[str] = None,
    nowpayments_checkout_token: Optional[str] = None,
    nowpayments_ipn_url: str = "https://payments.abc4rd.org/v1/payments/nowpayments/ipn",
    nowpayments_success_url: str = "https://payments.abc4rd.org/checkout/success",
    nowpayments_cancel_url: str = "https://payments.abc4rd.org/checkout/cancel",
    nowpayments_transport: Optional[Transport] = None,
    lemonsqueezy_webhook_secret: Optional[str] = None,
    lemonsqueezy_test_mode: bool = True,
    lemonsqueezy_api_key: Optional[str] = None,
    lemonsqueezy_store_id: Optional[Any] = None,
    lemonsqueezy_variant_id: Optional[Any] = None,
    lemonsqueezy_success_url: str = "https://payments.abc4rd.org/checkout/success",
    lemonsqueezy_transport: Optional[LemonSqueezyTransport] = None,
) -> Callable:
    core = AcademyCore(database, live_payment_provider, live_payment_gate_ref)
    if nowpayments_ipn_secret is not None:
        if not isinstance(nowpayments_ipn_secret, str) or not nowpayments_ipn_secret.strip():
            raise ValidationError("nowpayments_ipn_secret must be non-empty when configured")
        nowpayments_ipn_secret = nowpayments_ipn_secret.strip()
    if not nowpayments_sandbox and live_payment_provider != "nowpayments":
        raise ValidationError(
            "LIVE NOWPayments IPN requires live_payment_provider=nowpayments"
        )
    if nowpayments_api_key is not None and not all(
        isinstance(value, str) and value.strip()
        for value in (nowpayments_api_key, nowpayments_checkout_token)
    ):
        raise ValidationError(
            "NOWPayments checkout requires both API key and checkout token"
        )
    if nowpayments_api_key is not None:
        nowpayments_api_key = nowpayments_api_key.strip()
        nowpayments_checkout_token = nowpayments_checkout_token.strip()
    if not isinstance(lemonsqueezy_test_mode, bool):
        raise ValidationError("lemonsqueezy_test_mode must be a boolean")
    if not lemonsqueezy_test_mode and live_payment_provider != "lemonsqueezy":
        raise ValidationError(
            "LIVE Lemon Squeezy webhook requires live_payment_provider=lemonsqueezy"
        )
    if lemonsqueezy_api_key is not None and not all(
        (
            isinstance(lemonsqueezy_api_key, str)
            and lemonsqueezy_api_key.strip(),
            lemonsqueezy_store_id is not None,
            lemonsqueezy_variant_id is not None,
            isinstance(nowpayments_checkout_token, str)
            and nowpayments_checkout_token.strip(),
        )
    ):
        raise ValidationError(
            "Lemon Squeezy checkout requires API key, store id, variant id, and checkout token"
        )
    if lemonsqueezy_webhook_secret is not None and not all(
        (
            isinstance(lemonsqueezy_webhook_secret, str)
            and lemonsqueezy_webhook_secret.strip(),
            lemonsqueezy_store_id is not None,
            lemonsqueezy_variant_id is not None,
        )
    ):
        raise ValidationError(
            "Lemon Squeezy webhook requires secret, store id, and variant id"
        )
    if (
        lemonsqueezy_store_id is not None or lemonsqueezy_variant_id is not None
    ) and lemonsqueezy_api_key is None and lemonsqueezy_webhook_secret is None:
        raise ValidationError(
            "Lemon Squeezy store and variant require checkout or webhook configuration"
        )
    if lemonsqueezy_api_key is not None:
        lemonsqueezy_api_key = lemonsqueezy_api_key.strip()
    if lemonsqueezy_webhook_secret is not None:
        lemonsqueezy_webhook_secret = lemonsqueezy_webhook_secret.strip()
    if isinstance(nowpayments_checkout_token, str):
        nowpayments_checkout_token = nowpayments_checkout_token.strip()
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
            if (
                method == "POST"
                and path == "/v1/payments/nowpayments/invoices"
                and nowpayments_api_key is not None
                and nowpayments_checkout_token is not None
            ):
                supplied = environ.get("HTTP_AUTHORIZATION", "")
                prefix = "Bearer "
                if not supplied.startswith(prefix) or not hmac.compare_digest(
                    supplied[len(prefix) :], nowpayments_checkout_token
                ):
                    raise AuthenticationError("invalid checkout authorization")
                body = _read_json(environ)
                unknown = sorted(set(body) - {"order_id", "pay_currency"})
                if unknown:
                    raise ValidationError(
                        "unknown fields: %s" % ", ".join(unknown)
                    )
                client = NowPaymentsClient(
                    nowpayments_api_key,
                    sandbox=nowpayments_sandbox,
                    allow_live=not nowpayments_sandbox,
                    transport=nowpayments_transport,
                )
                invoice = client.create_pilot_invoice(
                    order_id=body.get("order_id"),
                    pay_currency=body.get("pay_currency"),
                    ipn_callback_url=nowpayments_ipn_url,
                    success_url=nowpayments_success_url,
                    cancel_url=nowpayments_cancel_url,
                )
                return _json_response(start_response, "201 Created", invoice)
            if (
                method == "POST"
                and path == "/v1/payments/lemonsqueezy/checkouts"
                and lemonsqueezy_api_key is not None
                and nowpayments_checkout_token is not None
            ):
                supplied = environ.get("HTTP_AUTHORIZATION", "")
                prefix = "Bearer "
                if not supplied.startswith(prefix) or not hmac.compare_digest(
                    supplied[len(prefix) :], nowpayments_checkout_token
                ):
                    raise AuthenticationError("invalid checkout authorization")
                body = _read_json(environ)
                unknown = sorted(set(body) - {"order_id", "abc4rd_id"})
                if unknown:
                    raise ValidationError(
                        "unknown fields: %s" % ", ".join(unknown)
                    )
                client = LemonSqueezyClient(
                    lemonsqueezy_api_key,
                    lemonsqueezy_store_id,
                    lemonsqueezy_variant_id,
                    test_mode=lemonsqueezy_test_mode,
                    allow_live=not lemonsqueezy_test_mode,
                    transport=lemonsqueezy_transport,
                )
                checkout = client.create_pilot_checkout(
                    order_id=body.get("order_id"),
                    abc4rd_id=body.get("abc4rd_id"),
                    success_url=lemonsqueezy_success_url,
                )
                return _json_response(start_response, "201 Created", checkout)
            if (
                method == "POST"
                and path == "/v1/payments/nowpayments/ipn"
                and nowpayments_ipn_secret is not None
            ):
                body = _read_json(environ)
                signature = environ.get("HTTP_X_NOWPAYMENTS_SIG", "")
                result = process_ipn(
                    core,
                    body,
                    signature,
                    nowpayments_ipn_secret,
                    sandbox=nowpayments_sandbox,
                )
                return _json_response(start_response, "200 OK", result)
            if (
                method == "POST"
                and path == "/v1/payments/lemonsqueezy/webhook"
                and lemonsqueezy_webhook_secret is not None
            ):
                body, raw_body = _read_json_with_raw(environ)
                result = process_lemonsqueezy_webhook(
                    core,
                    body,
                    raw_body,
                    environ.get("HTTP_X_SIGNATURE", ""),
                    lemonsqueezy_webhook_secret,
                    environ.get("HTTP_X_EVENT_NAME", ""),
                    test_mode=lemonsqueezy_test_mode,
                    store_id=lemonsqueezy_store_id,
                    variant_id=lemonsqueezy_variant_id,
                )
                return _json_response(start_response, "200 OK", result)

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
            labels = {
                400: "bad_request",
                401: "unauthorized",
                404: "not_found",
                409: "conflict",
                422: "validation_error",
            }
            return _json_response(
                start_response,
                "%d %s" % (error.status_code, _reason(error.status_code)),
                {"error": labels.get(error.status_code, "core_error"), "message": str(error)},
            )
        except (NowPaymentsError, LemonSqueezyError) as error:
            return _json_response(
                start_response,
                "502 Bad Gateway",
                {"error": "payment_provider_error", "message": str(error)},
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
        401: "Unauthorized",
        404: "Not Found",
        409: "Conflict",
        422: "Unprocessable Entity",
    }.get(status_code, "Error")
