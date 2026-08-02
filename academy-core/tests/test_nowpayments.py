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
from academy_core.payments.nowpayments import (
    NOWPAYMENTS_LIVE_BASE_URL,
    NOWPAYMENTS_SANDBOX_BASE_URL,
    NowPaymentsClient,
    NowPaymentsError,
    process_ipn,
    verify_ipn_signature,
)
from academy_core.service import AcademyCore


IPN_SECRET = "sandbox-ipn-secret-for-tests"


def signature(payload):
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hmac.new(IPN_SECRET.encode("utf-8"), canonical, hashlib.sha512).hexdigest()


def finished_payload(payment_id=123456789):
    return {
        "payment_id": payment_id,
        "payment_status": "finished",
        "price_amount": 1,
        "price_currency": "usd",
        "pay_currency": "usdcbase",
        "actually_paid": "1.00",
        "order_id": "pilot-order-1",
        "network": "base",
    }


class NowPaymentsClientTest(unittest.TestCase):
    def test_sandbox_invoice_is_fixed_to_one_us_dollar(self):
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
                "id": 777,
                "invoice_url": "https://sandbox.nowpayments.io/payment/?iid=777",
            }

        client = NowPaymentsClient("sandbox-api-key", transport=transport)
        invoice = client.create_pilot_invoice(
            order_id="pilot-order-1",
            ipn_callback_url="https://payments.abc4rd.org/v1/nowpayments/ipn",
            success_url="https://learn.abc4rd.org/payment/success",
            cancel_url="https://learn.abc4rd.org/payment/cancel",
            pay_currency="usdcbase",
        )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], NOWPAYMENTS_SANDBOX_BASE_URL + "invoice")
        self.assertEqual(captured["headers"]["x-api-key"], "sandbox-api-key")
        self.assertEqual(captured["body"]["price_amount"], "1.00")
        self.assertEqual(captured["body"]["price_currency"], "usd")
        self.assertEqual(captured["body"]["pay_currency"], "usdcbase")
        self.assertEqual(invoice["amount_minor"], 100)
        self.assertEqual(invoice["currency"], "USD")
        self.assertTrue(invoice["sandbox"])

    def test_live_requires_explicit_gate(self):
        with self.assertRaises(NowPaymentsError):
            NowPaymentsClient("live-api-key", sandbox=False)

        client = NowPaymentsClient(
            "live-api-key",
            sandbox=False,
            allow_live=True,
            transport=lambda *args: {"message": "OK"},
        )
        self.assertEqual(client.base_url, NOWPAYMENTS_LIVE_BASE_URL)

    def test_callback_urls_must_be_https(self):
        client = NowPaymentsClient(
            "sandbox-api-key",
            transport=lambda *args: {
                "id": 1,
                "invoice_url": "https://sandbox.nowpayments.io/payment/?iid=1",
            },
        )
        with self.assertRaises(NowPaymentsError):
            client.create_pilot_invoice(
                order_id="pilot-order-1",
                ipn_callback_url="http://localhost/ipn",
                success_url="https://learn.abc4rd.org/success",
                cancel_url="https://learn.abc4rd.org/cancel",
            )


class NowPaymentsIpnTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temporary.name) / "core.db")
        self.core = AcademyCore(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_signature_verification_and_duplicate_webhook_are_idempotent(self):
        payload = finished_payload()
        signed = signature(payload)
        self.assertTrue(verify_ipn_signature(payload, signed, IPN_SECRET))
        self.assertFalse(verify_ipn_signature(payload, "0" * 128, IPN_SECRET))

        first = process_ipn(self.core, payload, signed, IPN_SECRET)
        replay = process_ipn(self.core, payload, signed, IPN_SECRET)
        self.assertEqual(first, replay)
        self.assertTrue(first["ledger"]["recognized_charge"])

        connection = connect(self.database)
        try:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM payment_ledger_entries"
            ).fetchone()["count"]
        finally:
            connection.close()
        self.assertEqual(count, 1)

    def test_invalid_signature_is_rejected_before_write(self):
        payload = finished_payload()
        with self.assertRaises(AuthenticationError):
            process_ipn(self.core, payload, "0" * 128, IPN_SECRET)

        connection = connect(self.database)
        try:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM payment_ledger_entries"
            ).fetchone()["count"]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_non_terminal_status_is_authenticated_but_not_booked(self):
        payload = dict(finished_payload(), payment_status="confirming")
        result = process_ipn(self.core, payload, signature(payload), IPN_SECRET)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["recorded"])

    def test_wrong_pilot_price_is_rejected(self):
        payload = dict(finished_payload(), price_amount="2.00")
        with self.assertRaises(ValidationError):
            process_ipn(self.core, payload, signature(payload), IPN_SECRET)

    def test_refund_is_recorded_as_a_separate_provider_fact(self):
        charged = finished_payload()
        process_ipn(self.core, charged, signature(charged), IPN_SECRET)
        refunded = dict(charged, payment_status="refunded")
        result = process_ipn(self.core, refunded, signature(refunded), IPN_SECRET)
        self.assertEqual(
            result["ledger"]["fact_type"], "PROVIDER_CONFIRMED_REFUND"
        )
        self.assertFalse(result["ledger"]["recognized_charge"])


class NowPaymentsIpnApiTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temporary.name) / "core.db")

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, app, payload, supplied_signature):
        raw = json.dumps(payload).encode("utf-8")
        environ = {}
        setup_testing_defaults(environ)
        environ.update(
            REQUEST_METHOD="POST",
            PATH_INFO="/v1/payments/nowpayments/ipn",
            CONTENT_TYPE="application/json",
            CONTENT_LENGTH=str(len(raw)),
            HTTP_X_NOWPAYMENTS_SIG=supplied_signature,
            **{"wsgi.input": io.BytesIO(raw)},
        )
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = headers

        response = b"".join(app(environ, start_response))
        return captured["status"], json.loads(response.decode("utf-8"))

    def test_configured_webhook_accepts_valid_ipn_and_replay(self):
        app = create_app(self.database, nowpayments_ipn_secret=IPN_SECRET)
        payload = finished_payload()
        signed = signature(payload)

        first_status, first = self.call(app, payload, signed)
        replay_status, replay = self.call(app, payload, signed)

        self.assertEqual(first_status, "200 OK")
        self.assertEqual(replay_status, "200 OK")
        self.assertEqual(first, replay)
        self.assertTrue(first["recorded"])

    def test_invalid_signature_returns_401(self):
        app = create_app(self.database, nowpayments_ipn_secret=IPN_SECRET)
        status, body = self.call(app, finished_payload(), "0" * 128)
        self.assertEqual(status, "401 Unauthorized")
        self.assertEqual(body["error"], "unauthorized")

    def test_webhook_route_is_absent_without_secret(self):
        app = create_app(self.database)
        payload = finished_payload()
        status, body = self.call(app, payload, signature(payload))
        self.assertEqual(status, "404 Not Found")
        self.assertEqual(body["error"], "not_found")


if __name__ == "__main__":
    unittest.main()
