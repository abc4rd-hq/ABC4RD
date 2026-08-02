"""Lemon Squeezy Merchant-of-Record adapter for the USD 1.00 pilot."""

import hashlib
import hmac
import json
import re
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from ..errors import AuthenticationError, ValidationError
from ..service import AcademyCore, PILOT_PRICE_CURRENCY, PILOT_PRICE_MINOR


LEMONSQUEEZY_BASE_URL = "https://api.lemonsqueezy.com/v1/"
MAX_PROVIDER_RESPONSE_BYTES = 1024 * 1024
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class LemonSqueezyError(Exception):
    """Safe integration error without credentials or provider response bodies."""


Transport = Callable[[str, str, Mapping[str, str], Optional[bytes], float], Mapping[str, Any]]


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LemonSqueezyError("%s must be a non-empty string" % name)
    return value.strip()


def _positive_id(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise LemonSqueezyError("%s must be a positive integer" % name)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise LemonSqueezyError("%s must be a positive integer" % name) from error
    if parsed <= 0:
        raise LemonSqueezyError("%s must be a positive integer" % name)
    return parsed


def _https_url(value: Any, name: str) -> str:
    candidate = _required_text(value, name)
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        raise LemonSqueezyError("%s must be an absolute HTTPS URL" % name)
    return candidate


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise LemonSqueezyError("provider payload must be JSON-serializable") from error


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verify Lemon Squeezy's HMAC-SHA256 signature over the raw body."""

    signing_secret = _required_text(secret, "webhook_secret")
    if not isinstance(raw_body, bytes) or not isinstance(signature, str):
        return False
    supplied = signature.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", supplied) is None:
        return False
    expected = hmac.new(
        signing_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, supplied)


class LemonSqueezyClient:
    """Small JSON:API client that remains in Test mode unless explicitly gated."""

    def __init__(
        self,
        api_key: str,
        store_id: Any,
        variant_id: Any,
        *,
        test_mode: bool = True,
        allow_live: bool = False,
        timeout: float = 15.0,
        transport: Optional[Transport] = None,
    ):
        self._api_key = _required_text(api_key, "api_key")
        self.store_id = _positive_id(store_id, "store_id")
        self.variant_id = _positive_id(variant_id, "variant_id")
        if not isinstance(test_mode, bool):
            raise LemonSqueezyError("test_mode must be a boolean")
        if not test_mode and not allow_live:
            raise LemonSqueezyError("LIVE API access requires allow_live=True")
        if timeout <= 0 or timeout > 60:
            raise LemonSqueezyError("timeout must be between 0 and 60 seconds")
        self.test_mode = test_mode
        self.timeout = float(timeout)
        self._transport = transport or self._urlopen_transport

    @property
    def provider_name(self) -> str:
        return "lemonsqueezy-test" if self.test_mode else "lemonsqueezy"

    def create_pilot_checkout(
        self,
        *,
        order_id: str,
        success_url: str,
        abc4rd_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a hosted checkout with a fixed USD 1.00 pre-tax product price."""

        order_ref = _required_text(order_id, "order_id")
        if ORDER_ID_RE.fullmatch(order_ref) is None:
            raise LemonSqueezyError(
                "order_id must be an opaque 1-128 character identifier"
            )
        custom_data: Dict[str, Any] = {"order_id": order_ref}
        if abc4rd_id is not None:
            identity_ref = _required_text(abc4rd_id, "abc4rd_id")
            if len(identity_ref) > 128:
                raise LemonSqueezyError("abc4rd_id must not exceed 128 characters")
            custom_data["abc4rd_id"] = identity_ref

        request_data = {
            "data": {
                "type": "checkouts",
                "attributes": {
                    "custom_price": PILOT_PRICE_MINOR,
                    "product_options": {
                        "name": "ABC4RD Academy pilot access",
                        "description": "International Academy pilot course access",
                        "redirect_url": _https_url(success_url, "success_url"),
                        "enabled_variants": [self.variant_id],
                    },
                    "checkout_options": {
                        "embed": False,
                        "media": False,
                        "logo": True,
                        "desc": True,
                        "discount": False,
                    },
                    "checkout_data": {"custom": custom_data},
                    "preview": True,
                    "test_mode": self.test_mode,
                },
                "relationships": {
                    "store": {
                        "data": {"type": "stores", "id": str(self.store_id)}
                    },
                    "variant": {
                        "data": {"type": "variants", "id": str(self.variant_id)}
                    },
                },
            }
        }
        response = self._request("POST", "checkouts", request_data)
        data = response.get("data")
        if not isinstance(data, Mapping) or data.get("type") != "checkouts":
            raise LemonSqueezyError("provider response has no checkout resource")
        checkout_id = data.get("id")
        attributes = data.get("attributes")
        if not isinstance(checkout_id, str) or not checkout_id.strip():
            raise LemonSqueezyError("provider response has no checkout id")
        if not isinstance(attributes, Mapping):
            raise LemonSqueezyError("provider response has no checkout attributes")
        preview = attributes.get("preview")
        if not isinstance(preview, Mapping):
            raise LemonSqueezyError("provider response has no checkout preview")
        if str(preview.get("currency", "")).upper() != PILOT_PRICE_CURRENCY:
            raise LemonSqueezyError("Lemon Squeezy store currency must be USD")
        if preview.get("subtotal") != PILOT_PRICE_MINOR:
            raise LemonSqueezyError("provider preview does not match the USD 1.00 price")
        checkout_url = _https_url(attributes.get("url"), "provider checkout url")
        return {
            "provider": self.provider_name,
            "checkout_id": checkout_id,
            "checkout_url": checkout_url,
            "order_id": order_ref,
            "amount_minor": PILOT_PRICE_MINOR,
            "currency": PILOT_PRICE_CURRENCY,
            "test_mode": self.test_mode,
        }

    def _request(
        self, method: str, path: str, payload: Optional[Mapping[str, Any]] = None
    ) -> Mapping[str, Any]:
        body = _json_bytes(payload) if payload is not None else None
        headers = {
            "Accept": "application/vnd.api+json",
            "Authorization": "Bearer %s" % self._api_key,
        }
        if body is not None:
            headers["Content-Type"] = "application/vnd.api+json"
        result = self._transport(
            method,
            urljoin(LEMONSQUEEZY_BASE_URL, path),
            headers,
            body,
            self.timeout,
        )
        if not isinstance(result, Mapping):
            raise LemonSqueezyError("provider response must be a JSON object")
        return result

    @staticmethod
    def _urlopen_transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> Mapping[str, Any]:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise LemonSqueezyError(
                "provider returned HTTP %d" % error.code
            ) from error
        except (URLError, TimeoutError) as error:
            raise LemonSqueezyError("provider request failed") from error
        if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
            raise LemonSqueezyError("provider response exceeded 1 MiB")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LemonSqueezyError("provider response was not valid JSON") from error
        if not isinstance(parsed, dict):
            raise LemonSqueezyError("provider response must be a JSON object")
        return parsed


def _integer_amount(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError("Lemon Squeezy %s must be a non-negative integer" % name)
    return value


def process_webhook(
    core: AcademyCore,
    payload: Mapping[str, Any],
    raw_body: bytes,
    signature: str,
    webhook_secret: str,
    event_name: str,
    *,
    test_mode: bool = True,
    store_id: Any,
    variant_id: Any,
) -> Dict[str, Any]:
    """Authenticate an order event and record a full charge/refund exactly once."""

    if not isinstance(payload, Mapping):
        raise ValidationError("Lemon Squeezy webhook body must be a JSON object")
    if not verify_webhook_signature(raw_body, signature, webhook_secret):
        raise AuthenticationError("Lemon Squeezy webhook signature is invalid")
    meta = payload.get("meta")
    data = payload.get("data")
    if not isinstance(meta, Mapping) or not isinstance(data, Mapping):
        raise ValidationError("Lemon Squeezy webhook resource is invalid")
    supplied_event = str(event_name or "").strip()
    payload_event = str(meta.get("event_name", "")).strip()
    if supplied_event not in ("order_created", "order_refunded"):
        raise ValidationError("Lemon Squeezy webhook event is not supported")
    if payload_event != supplied_event:
        raise ValidationError("Lemon Squeezy event header does not match payload")
    if data.get("type") != "orders":
        raise ValidationError("Lemon Squeezy webhook data must be an order")

    order_id = data.get("id")
    if isinstance(order_id, bool) or not isinstance(order_id, (str, int)):
        raise ValidationError("Lemon Squeezy order id is invalid")
    attributes = data.get("attributes")
    if not isinstance(attributes, Mapping):
        raise ValidationError("Lemon Squeezy order attributes are invalid")
    if attributes.get("test_mode") is not test_mode:
        raise ValidationError("Lemon Squeezy webhook mode does not match configuration")
    try:
        expected_store_id = _positive_id(store_id, "store_id")
        expected_variant_id = _positive_id(variant_id, "variant_id")
    except LemonSqueezyError as error:
        raise ValidationError(str(error)) from error
    if attributes.get("store_id") != expected_store_id:
        raise ValidationError("Lemon Squeezy webhook store does not match configuration")
    item = attributes.get("first_order_item")
    if not isinstance(item, Mapping) or item.get("variant_id") != expected_variant_id:
        raise ValidationError("Lemon Squeezy webhook variant does not match configuration")

    currency = str(attributes.get("currency", "")).upper()
    subtotal = _integer_amount(attributes.get("subtotal"), "subtotal")
    subtotal_usd = _integer_amount(attributes.get("subtotal_usd"), "subtotal_usd")
    discount_total = _integer_amount(
        attributes.get("discount_total", 0), "discount_total"
    )
    if (
        currency != PILOT_PRICE_CURRENCY
        or subtotal != PILOT_PRICE_MINOR
        or subtotal_usd != PILOT_PRICE_MINOR
        or discount_total != 0
    ):
        raise ValidationError(
            "Lemon Squeezy order does not match the USD 1.00 pre-tax pilot price"
        )

    status = str(attributes.get("status", "")).lower()
    if supplied_event == "order_created":
        if status != "paid":
            raise ValidationError("Lemon Squeezy order_created must have paid status")
        fact_type = "PROVIDER_CONFIRMED_CHARGE"
    else:
        refunded_amount_usd = _integer_amount(
            attributes.get("refunded_amount_usd", 0), "refunded_amount_usd"
        )
        if attributes.get("refunded") is not True or refunded_amount_usd < PILOT_PRICE_MINOR:
            return {
                "accepted": True,
                "recorded": False,
                "order_id": str(order_id),
                "event_name": supplied_event,
                "reason": "partial_refund",
            }
        if status != "refunded":
            raise ValidationError("full Lemon Squeezy refund must have refunded status")
        fact_type = "PROVIDER_CONFIRMED_REFUND"

    custom_data = meta.get("custom_data")
    if not isinstance(custom_data, Mapping):
        custom_data = {}
    provider = "lemonsqueezy-test" if test_mode else "lemonsqueezy"
    provider_event_ref = "%s:%s:%s" % (provider, order_id, supplied_event)
    evidence_digest = hashlib.sha256(raw_body).hexdigest()
    metadata = {
        "event_name": supplied_event,
        "identifier": attributes.get("identifier"),
        "order_number": attributes.get("order_number"),
        "status": status,
        "subtotal": subtotal,
        "tax": attributes.get("tax", 0),
        "total": attributes.get("total"),
        "total_usd": attributes.get("total_usd"),
        "refunded_amount_usd": attributes.get("refunded_amount_usd", 0),
        "variant_id": expected_variant_id,
        "opaque_order_id": custom_data.get("order_id"),
    }
    entry: Dict[str, Any] = {
        "mode": "SANDBOX" if test_mode else "LIVE",
        "provider": provider,
        "provider_event_ref": provider_event_ref,
        "fact_type": fact_type,
        "entry_type": "LEMONSQUEEZY_%s" % supplied_event.upper(),
        "amount_minor": PILOT_PRICE_MINOR,
        "currency": PILOT_PRICE_CURRENCY,
        "provider_evidence_ref": "sha256:%s" % evidence_digest,
        "metadata": metadata,
        "actor_type": "SYSTEM",
        "actor_ref": provider,
    }
    abc4rd_id = custom_data.get("abc4rd_id")
    if isinstance(abc4rd_id, str) and abc4rd_id.strip():
        entry["abc4rd_id"] = abc4rd_id.strip()
    ledger = core.record_payment(entry, provider_event_ref)
    return {
        "accepted": True,
        "recorded": True,
        "order_id": str(order_id),
        "event_name": supplied_event,
        "ledger": ledger,
    }
