"""Minimal NOWPayments adapter for the USD 1.00 ABC4RD pilot.

The adapter creates provider-hosted invoices and translates authenticated IPN
callbacks into append-only Academy Core observations. It never stores API keys,
wallet addresses, or an IPN secret in the database.
"""

import hashlib
import hmac
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from ..errors import ValidationError
from ..service import AcademyCore, PILOT_PRICE_CURRENCY, PILOT_PRICE_MINOR


NOWPAYMENTS_SANDBOX_BASE_URL = "https://api-sandbox.nowpayments.io/v1/"
NOWPAYMENTS_LIVE_BASE_URL = "https://api.nowpayments.io/v1/"
MAX_PROVIDER_RESPONSE_BYTES = 1024 * 1024
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TERMINAL_FACT_TYPES = {
    "finished": "PROVIDER_CONFIRMED_CHARGE",
    "refunded": "PROVIDER_CONFIRMED_REFUND",
}
KNOWN_NON_TERMINAL_STATUSES = {
    "waiting",
    "confirming",
    "confirmed",
    "sending",
    "partially_paid",
    "failed",
    "expired",
}


class NowPaymentsError(Exception):
    """Safe integration error that does not include credentials or response bodies."""


Transport = Callable[[str, str, Mapping[str, str], Optional[bytes], float], Mapping[str, Any]]


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NowPaymentsError("%s must be a non-empty string" % name)
    return value.strip()


def _https_url(value: str, name: str) -> str:
    candidate = _required_text(value, name)
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        raise NowPaymentsError("%s must be an absolute HTTPS URL" % name)
    return candidate


def _canonical_payload(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise NowPaymentsError("IPN payload must be JSON-serializable") from error


def verify_ipn_signature(
    payload: Mapping[str, Any], signature: str, ipn_secret: str
) -> bool:
    """Verify NOWPayments' HMAC-SHA512 signature over canonical JSON."""

    secret = _required_text(ipn_secret, "ipn_secret")
    supplied = _required_text(signature, "x-nowpayments-sig").lower()
    if not re.fullmatch(r"[0-9a-f]{128}", supplied):
        return False
    expected = hmac.new(
        secret.encode("utf-8"), _canonical_payload(payload), hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(expected, supplied)


class NowPaymentsClient:
    """Small JSON client with sandbox-by-default and an explicit LIVE gate."""

    def __init__(
        self,
        api_key: str,
        *,
        sandbox: bool = True,
        allow_live: bool = False,
        timeout: float = 15.0,
        transport: Optional[Transport] = None,
    ):
        self._api_key = _required_text(api_key, "api_key")
        if not sandbox and not allow_live:
            raise NowPaymentsError("LIVE API access requires allow_live=True")
        if timeout <= 0 or timeout > 60:
            raise NowPaymentsError("timeout must be between 0 and 60 seconds")
        self.sandbox = sandbox
        self.timeout = float(timeout)
        self.base_url = (
            NOWPAYMENTS_SANDBOX_BASE_URL if sandbox else NOWPAYMENTS_LIVE_BASE_URL
        )
        self._transport = transport or self._urlopen_transport

    @property
    def provider_name(self) -> str:
        return "nowpayments-sandbox" if self.sandbox else "nowpayments"

    def status(self) -> str:
        response = self._request("GET", "status")
        message = response.get("message")
        if message != "OK":
            raise NowPaymentsError("provider status is not OK")
        return message

    def create_pilot_invoice(
        self,
        *,
        order_id: str,
        ipn_callback_url: str,
        success_url: str,
        cancel_url: str,
        pay_currency: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a provider-hosted invoice fixed to the approved USD 1.00 price."""

        order_ref = _required_text(order_id, "order_id")
        if ORDER_ID_RE.fullmatch(order_ref) is None:
            raise NowPaymentsError(
                "order_id must be an opaque 1-128 character identifier"
            )
        request_data: Dict[str, Any] = {
            "price_amount": "1.00",
            "price_currency": "usd",
            "order_id": order_ref,
            "order_description": "ABC4RD Academy pilot access",
            "ipn_callback_url": _https_url(ipn_callback_url, "ipn_callback_url"),
            "success_url": _https_url(success_url, "success_url"),
            "cancel_url": _https_url(cancel_url, "cancel_url"),
            "is_fixed_rate": True,
            "is_fee_paid_by_user": False,
        }
        if pay_currency is not None:
            currency = _required_text(pay_currency, "pay_currency").lower()
            if re.fullmatch(r"[a-z0-9_-]{2,32}", currency) is None:
                raise NowPaymentsError("pay_currency has an invalid format")
            request_data["pay_currency"] = currency

        response = self._request("POST", "invoice", request_data)
        invoice_id = response.get("id") or response.get("invoice_id")
        if isinstance(invoice_id, bool) or not isinstance(invoice_id, (str, int)):
            raise NowPaymentsError("provider response has no invoice id")
        invoice_url = _https_url(response.get("invoice_url"), "provider invoice_url")
        return {
            "provider": self.provider_name,
            "invoice_id": str(invoice_id),
            "invoice_url": invoice_url,
            "order_id": order_ref,
            "amount_minor": PILOT_PRICE_MINOR,
            "currency": PILOT_PRICE_CURRENCY,
            "sandbox": self.sandbox,
        }

    def _request(
        self, method: str, path: str, payload: Optional[Mapping[str, Any]] = None
    ) -> Mapping[str, Any]:
        body = _canonical_payload(payload) if payload is not None else None
        headers = {"Accept": "application/json", "x-api-key": self._api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"
        result = self._transport(
            method,
            urljoin(self.base_url, path),
            headers,
            body,
            self.timeout,
        )
        if not isinstance(result, Mapping):
            raise NowPaymentsError("provider response must be a JSON object")
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
            raise NowPaymentsError(
                "provider returned HTTP %d" % error.code
            ) from error
        except (URLError, TimeoutError) as error:
            raise NowPaymentsError("provider request failed") from error
        if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
            raise NowPaymentsError("provider response exceeded 1 MiB")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NowPaymentsError("provider response was not valid JSON") from error
        if not isinstance(parsed, dict):
            raise NowPaymentsError("provider response must be a JSON object")
        return parsed


def _pilot_price(payload: Mapping[str, Any]) -> None:
    try:
        amount = Decimal(str(payload.get("price_amount")))
    except InvalidOperation as error:
        raise ValidationError("NOWPayments price_amount is invalid") from error
    currency = str(payload.get("price_currency", "")).upper()
    if amount != Decimal("1.00") or currency != PILOT_PRICE_CURRENCY:
        raise ValidationError("NOWPayments IPN does not match the USD 1.00 pilot price")


def process_ipn(
    core: AcademyCore,
    payload: Mapping[str, Any],
    signature: str,
    ipn_secret: str,
    *,
    sandbox: bool = True,
    abc4rd_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Authenticate an IPN and append a terminal provider fact exactly once."""

    if not isinstance(payload, Mapping):
        raise ValidationError("NOWPayments IPN body must be a JSON object")
    if not verify_ipn_signature(payload, signature, ipn_secret):
        raise ValidationError("NOWPayments IPN signature is invalid")

    payment_id = payload.get("payment_id")
    if isinstance(payment_id, bool) or not isinstance(payment_id, (str, int)):
        raise ValidationError("NOWPayments IPN payment_id is invalid")
    status = str(payload.get("payment_status", "")).lower()
    if status in KNOWN_NON_TERMINAL_STATUSES:
        return {
            "accepted": True,
            "recorded": False,
            "payment_id": str(payment_id),
            "payment_status": status,
        }
    fact_type = TERMINAL_FACT_TYPES.get(status)
    if fact_type is None:
        raise ValidationError("NOWPayments IPN payment_status is unknown")
    _pilot_price(payload)

    canonical = _canonical_payload(payload)
    evidence_digest = hashlib.sha256(canonical).hexdigest()
    provider = "nowpayments-sandbox" if sandbox else "nowpayments"
    provider_event_ref = "%s:%s:%s" % (provider, payment_id, status)
    metadata = {
        name: payload[name]
        for name in (
            "payment_status",
            "pay_currency",
            "network",
            "order_id",
            "purchase_id",
            "actually_paid",
            "outcome_amount",
            "outcome_currency",
        )
        if name in payload
    }
    entry: Dict[str, Any] = {
        "mode": "SANDBOX" if sandbox else "LIVE",
        "provider": provider,
        "provider_event_ref": provider_event_ref,
        "fact_type": fact_type,
        "entry_type": "NOWPAYMENTS_IPN_%s" % status.upper(),
        "amount_minor": PILOT_PRICE_MINOR,
        "currency": PILOT_PRICE_CURRENCY,
        "provider_evidence_ref": "sha256:%s" % evidence_digest,
        "metadata": metadata,
        "actor_type": "SYSTEM",
        "actor_ref": provider,
    }
    if abc4rd_id is not None:
        entry["abc4rd_id"] = abc4rd_id
    ledger = core.record_payment(entry, provider_event_ref)
    return {
        "accepted": True,
        "recorded": True,
        "payment_id": str(payment_id),
        "payment_status": status,
        "ledger": ledger,
    }
