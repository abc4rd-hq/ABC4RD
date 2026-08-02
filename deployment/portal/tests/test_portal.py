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
        self.subject = "keycloak-subject-test"
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


if __name__ == "__main__":
    unittest.main()
