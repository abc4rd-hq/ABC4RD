import hashlib
import hmac
import io
import json
import tempfile
import unittest
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from academy_core.app import create_app
from academy_core.db import connect
from academy_core.errors import AuthenticationError, ValidationError
from academy_core.payments.lemonsqueezy import (
    LEMONSQUEEZY_BASE_URL,
    LemonSqueezyClient,
    LemonSqueezyError,
    process_webhook,
    verify_webhook_signature,
)
from academy_core.service import AcademyCore


WEBHOOK_SECRET = "test-signing-secret"
STORE_ID = 123
VARIANT_ID = 456


def encoded(payload):
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def signature(raw):
    return hmac.new(WEBHOOK_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def order_payload(event_name="order_created", order_id="987"):
    refunded = event_name == "order_refunded"
    return {
        "meta": {
            "event_name": event_name,
            "custom_data": {"order_id": "opaque-order-1"},
        },
        "data": {
            "type": "orders",
            "id": order_id,
            "attributes": {
                "store_id": STORE_ID,
                "identifier": "provider-opaque-identifier",
                "order_number": 42,
                "user_name": "PII must not enter ledger",
                "user_email": "pii@example.invalid",
                "currency": "USD",
                "subtotal": 100,
                "discount_total": 0,
                "tax": 20,
                "total": 120,
                "subtotal_usd": 100,
                "total_usd": 120,
                "refunded_amount_usd": 120 if refunded else 0,
                "status": "refunded" if refunded else "paid",
                "refunded": refunded,
                "test_mode": True,
                "first_order_item": {
                    "variant_id": VARIANT_ID,
                    "product_name": "ABC4RD Academy pilot access",
                    "price": 100,
                    "test_mode": True,
                },
            },
        },
    }


class LemonSqueezyClientTest(unittest.TestCase):
    def test_checkout_is_fixed_to_one_us_dollar_before_tax(self):
        captured = {}

        def transport(method, url, headers, body, timeout):
            captured.update(
                method=method,
                url=url,
                headers=headers,
                body=json.loads(body.decode("utf-8")),
                timeout=timeout,
            )
            return {
                "data": {
                    "type": "checkouts",
                    "id": "checkout-uuid",
                    "attributes": {
                        "url": "https://abc4rd.lemonsqueezy.com/checkout/custom/signed",
                        "preview": {
                            "currency": "USD",
                            "subtotal": 100,
                            "tax": 20,
                            "total": 120,
                        },
                    },
                }
            }

        client = LemonSqueezyClient(
            "test-api-key", STORE_ID, VARIANT_ID, transport=transport
        )
        checkout = client.create_pilot_checkout(
            order_id="opaque-order-1",
            success_url="https://payments.abc4rd.org/checkout/success",
        )

        request = captured["body"]["data"]
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], LEMONSQUEEZY_BASE_URL + "checkouts")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-api-key")
        self.assertEqual(request["attributes"]["custom_price"], 100)
        self.assertTrue(request["attributes"]["test_mode"])
        self.assertFalse(request["attributes"]["checkout_options"]["discount"])
        self.assertEqual(
            request["attributes"]["checkout_data"]["custom"]["order_id"],
            "opaque-order-1",
        )
        self.assertEqual(checkout["amount_minor"], 100)
        self.assertEqual(checkout["currency"], "USD")
        self.assertTrue(checkout["test_mode"])

    def test_live_requires_explicit_gate(self):
        with self.assertRaises(LemonSqueezyError):
            LemonSqueezyClient(
                "live-api-key", STORE_ID, VARIANT_ID, test_mode=False
            )
        client = LemonSqueezyClient(
            "live-api-key",
            STORE_ID,
            VARIANT_ID,
            test_mode=False,
            allow_live=True,
            transport=lambda *args: {},
        )
        self.assertEqual(client.provider_name, "lemonsqueezy")

    def test_non_usd_store_preview_is_rejected(self):
        client = LemonSqueezyClient(
            "test-api-key",
            STORE_ID,
            VARIANT_ID,
            transport=lambda *args: {
                "data": {
                    "type": "checkouts",
                    "id": "checkout-uuid",
                    "attributes": {
                        "url": "https://example.lemonsqueezy.com/checkout/custom/signed",
                        "preview": {"currency": "EUR", "subtotal": 100},
                    },
                }
            },
        )
        with self.assertRaises(LemonSqueezyError):
            client.create_pilot_checkout(
                order_id="opaque-order-1",
                success_url="https://payments.abc4rd.org/checkout/success",
            )


class LemonSqueezyWebhookTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temporary.name) / "core.db")
        self.core = AcademyCore(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def process(self, payload, event_name=None, supplied_signature=None):
        raw = encoded(payload)
        return process_webhook(
            self.core,
            payload,
            raw,
            supplied_signature or signature(raw),
            WEBHOOK_SECRET,
            event_name or payload["meta"]["event_name"],
            store_id=STORE_ID,
            variant_id=VARIANT_ID,
        )

    def test_signature_and_duplicate_charge_are_idempotent(self):
        payload = order_payload()
        raw = encoded(payload)
        self.assertTrue(verify_webhook_signature(raw, signature(raw), WEBHOOK_SECRET))
        self.assertFalse(verify_webhook_signature(raw, "0" * 64, WEBHOOK_SECRET))

        first = self.process(payload)
        replay = self.process(payload)
        self.assertEqual(first, replay)
        self.assertTrue(first["ledger"]["recognized_charge"])
        self.assertNotIn("user_email", first["ledger"]["metadata"])
        self.assertNotIn("user_name", first["ledger"]["metadata"])

        connection = connect(self.database)
        try:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM payment_ledger_entries"
            ).fetchone()["count"]
        finally:
            connection.close()
        self.assertEqual(count, 1)

    def test_invalid_signature_is_rejected_before_write(self):
        with self.assertRaises(AuthenticationError):
            self.process(order_payload(), supplied_signature="0" * 64)

    def test_header_payload_event_mismatch_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.process(order_payload(), event_name="order_refunded")

    def test_wrong_store_variant_mode_and_price_are_rejected(self):
        cases = []
        wrong_store = order_payload(order_id="wrong-store")
        wrong_store["data"]["attributes"]["store_id"] = 999
        cases.append(wrong_store)
        wrong_variant = order_payload(order_id="wrong-variant")
        wrong_variant["data"]["attributes"]["first_order_item"]["variant_id"] = 999
        cases.append(wrong_variant)
        wrong_mode = order_payload(order_id="wrong-mode")
        wrong_mode["data"]["attributes"]["test_mode"] = False
        cases.append(wrong_mode)
        wrong_price = order_payload(order_id="wrong-price")
        wrong_price["data"]["attributes"]["subtotal"] = 200
        cases.append(wrong_price)
        for payload in cases:
            with self.subTest(order_id=payload["data"]["id"]):
                with self.assertRaises(ValidationError):
                    self.process(payload)

    def test_full_refund_is_separate_fact_and_partial_is_not_booked(self):
        charge = order_payload()
        self.process(charge)
        partial = order_payload("order_refunded", order_id="partial")
        partial["data"]["attributes"]["refunded"] = False
        partial["data"]["attributes"]["status"] = "partial_refund"
        partial["data"]["attributes"]["refunded_amount_usd"] = 50
        partial_result = self.process(partial)
        self.assertFalse(partial_result["recorded"])

        refund = order_payload("order_refunded")
        result = self.process(refund)
        self.assertEqual(
            result["ledger"]["fact_type"], "PROVIDER_CONFIRMED_REFUND"
        )
        self.assertFalse(result["ledger"]["recognized_charge"])


class LemonSqueezyApiTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temporary.name) / "core.db")

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, app, path, payload, headers=None):
        raw = encoded(payload)
        environ = {}
        setup_testing_defaults(environ)
        environ.update(
            REQUEST_METHOD="POST",
            PATH_INFO=path,
            CONTENT_TYPE="application/json",
            CONTENT_LENGTH=str(len(raw)),
            **{"wsgi.input": io.BytesIO(raw)},
        )
        for name, value in (headers or {}).items():
            environ["HTTP_" + name.upper().replace("-", "_")] = value
        captured = {}

        def start_response(status, response_headers):
            captured["status"] = status

        response = b"".join(app(environ, start_response))
        return captured["status"], json.loads(response.decode("utf-8"))

    def configured_checkout_app(self):
        def transport(method, url, headers, body, timeout):
            self.checkout_request = json.loads(body.decode("utf-8"))
            return {
                "data": {
                    "type": "checkouts",
                    "id": "checkout-uuid",
                    "attributes": {
                        "url": "https://abc4rd.lemonsqueezy.com/checkout/custom/signed",
                        "preview": {"currency": "USD", "subtotal": 100},
                    },
                }
            }

        return create_app(
            self.database,
            nowpayments_checkout_token="internal-checkout-token",
            lemonsqueezy_api_key="test-api-key",
            lemonsqueezy_store_id=STORE_ID,
            lemonsqueezy_variant_id=VARIANT_ID,
            lemonsqueezy_transport=transport,
        )

    def test_internal_checkout_requires_token_and_returns_one_dollar_url(self):
        app = self.configured_checkout_app()
        unauthorized, _ = self.call(
            app,
            "/v1/payments/lemonsqueezy/checkouts",
            {"order_id": "opaque-order-1"},
        )
        status, body = self.call(
            app,
            "/v1/payments/lemonsqueezy/checkouts",
            {"order_id": "opaque-order-1"},
            {"Authorization": "Bearer internal-checkout-token"},
        )
        self.assertEqual(unauthorized, "401 Unauthorized")
        self.assertEqual(status, "201 Created")
        self.assertEqual(body["amount_minor"], 100)
        self.assertEqual(body["currency"], "USD")

    def test_checkout_route_absent_without_provider_credentials(self):
        status, body = self.call(
            create_app(self.database),
            "/v1/payments/lemonsqueezy/checkouts",
            {"order_id": "opaque-order-1"},
        )
        self.assertEqual(status, "404 Not Found")
        self.assertEqual(body["error"], "not_found")

    def test_configured_webhook_accepts_signed_order(self):
        app = create_app(
            self.database,
            lemonsqueezy_webhook_secret=WEBHOOK_SECRET,
            lemonsqueezy_store_id=STORE_ID,
            lemonsqueezy_variant_id=VARIANT_ID,
        )
        payload = order_payload()
        raw = encoded(payload)
        status, body = self.call(
            app,
            "/v1/payments/lemonsqueezy/webhook",
            payload,
            {
                "X-Signature": signature(raw),
                "X-Event-Name": "order_created",
            },
        )
        self.assertEqual(status, "200 OK")
        self.assertTrue(body["recorded"])

    def test_webhook_route_absent_without_secret(self):
        payload = order_payload()
        raw = encoded(payload)
        status, body = self.call(
            create_app(self.database),
            "/v1/payments/lemonsqueezy/webhook",
            payload,
            {"X-Signature": signature(raw), "X-Event-Name": "order_created"},
        )
        self.assertEqual(status, "404 Not Found")
        self.assertEqual(body["error"], "not_found")


if __name__ == "__main__":
    unittest.main()
