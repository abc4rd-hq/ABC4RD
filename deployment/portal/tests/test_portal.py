from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import main


class PortalReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.original_data_path = main.DATA_PATH
        main.DATA_PATH = Path(self.tempdir.name) / "state.json"
        self.addCleanup(setattr, main, "DATA_PATH", self.original_data_path)
        self.original_signing_key = main.SIGNING_KEY
        self.original_verify_base_url = main.VERIFY_BASE_URL
        main.SIGNING_KEY = "portal-certificate-test-signing-key"
        main.VERIFY_BASE_URL = "https://verify.abc4rd.org"
        self.addCleanup(setattr, main, "SIGNING_KEY", self.original_signing_key)
        self.addCleanup(setattr, main, "VERIFY_BASE_URL", self.original_verify_base_url)
        self.subject = "keycloak-subject-test"
        self.certificate_id = "ABC4RD-PILOT-TEST-0001"
        main.DATA_PATH.write_text(
            json.dumps(
                {
                    "participants": [
                        {
                            "abc4rd_id": "abc4rd-id-test",
                            "keycloak_subject": self.subject,
                            "display_label": "PILOT-TEST",
                            "courses": [
                                {
                                    "course_ref": main.LIBRARY_COURSE_REF,
                                    "title": "Pilot course",
                                    "passed": True,
                                    "grade_percent": 100,
                                    "certificate": {
                                        "id": self.certificate_id,
                                        "issued_at": "2026-08-03T00:00:00Z",
                                    },
                                }
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.client = main.app.test_client()

    def token(self) -> str:
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": self.subject}).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return f"header.{payload}.signature"

    def test_reader_requires_a_synchronized_entitlement(self) -> None:
        response = self.client.get("/library/pilot-0001")
        self.assertEqual(response.status_code, 403)

    def test_mobile_setup_has_no_separate_registration(self) -> None:
        response = self.client.get("/mobile")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"https://matrix.abc4rd.org", response.data)
        self.assertIn("Отдельно регистрироваться".encode("utf-8"), response.data)
        self.assertIn("25 МБ".encode("utf-8"), response.data)

    def test_reader_is_private_watermarked_and_audited_idempotently(self) -> None:
        core_response = mock.MagicMock()
        core_response.__enter__.return_value = core_response
        core_response.read.return_value = b"{}"
        headers = {"X-Forwarded-Access-Token": self.token()}
        with mock.patch.object(main.urlrequest, "urlopen", return_value=core_response) as open_core:
            first = self.client.get("/library/pilot-0001", headers=headers)
            second = self.client.get("/library/pilot-0001", headers=headers)

        self.assertEqual(first.status_code, 200)
        self.assertIn(b"abc4rd-id-test", first.data)
        self.assertEqual(first.headers["Cache-Control"], "private, no-store, max-age=0")
        self.assertEqual(first.headers["X-Robots-Tag"], "noindex, noarchive, nosnippet")
        self.assertIn("frame-ancestors 'none'", first.headers["Content-Security-Policy"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(open_core.call_count, 2)
        first_key = open_core.call_args_list[0].args[0].get_header("Idempotency-key")
        second_key = open_core.call_args_list[1].args[0].get_header("Idempotency-key")
        self.assertEqual(first_key, second_key)

    def test_certificate_pdf_is_downloadable_and_contains_qr_image(self) -> None:
        response = self.client.get(
            f"/certificate/{self.certificate_id}.pdf",
            headers={"X-Forwarded-Access-Token": self.token()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF-"))
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn(self.certificate_id, response.headers["Content-Disposition"])
        self.assertIn(b"/Subtype /Image", response.data)

    def test_public_certificate_link_accepts_only_valid_signature(self) -> None:
        verify_url = main._certificate_verify_url(self.certificate_id)
        expected_prefix = f"https://verify.abc4rd.org/c/{self.certificate_id}?sig="
        self.assertTrue(verify_url.startswith(expected_prefix))
        signature = verify_url.removeprefix(expected_prefix)

        valid = self.client.get(f"/c/{self.certificate_id}?sig={signature}")
        invalid = self.client.get(f"/c/{self.certificate_id}?sig=invalid")
        unknown = self.client.get(f"/c/UNKNOWN-CERTIFICATE?sig={signature}")

        self.assertEqual(valid.status_code, 200)
        self.assertIn("Сертификат действителен".encode("utf-8"), valid.data)
        self.assertIn(b"PILOT-TEST", valid.data)
        self.assertIn(b"Pilot course", valid.data)
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(unknown.status_code, 404)


if __name__ == "__main__":
    unittest.main()
