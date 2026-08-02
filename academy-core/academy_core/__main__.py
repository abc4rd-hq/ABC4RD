import argparse
import os
from wsgiref.simple_server import make_server

from .app import create_app
from .db import initialize
from .service import AcademyCore


def main() -> None:
    parser = argparse.ArgumentParser(description="ABC4RD Academy Core")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize the SQLite schema")
    init_parser.add_argument("--db", default="var/academy-core.db")

    serve_parser = subparsers.add_parser("serve", help="run the local HTTP service")
    serve_parser.add_argument("--db", default="var/academy-core.db")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument("--live-payment-provider")
    serve_parser.add_argument("--live-payment-gate-ref")
    serve_parser.add_argument(
        "--nowpayments-ipn-secret-env", default="NOWPAYMENTS_IPN_SECRET"
    )
    serve_parser.add_argument("--nowpayments-live", action="store_true")
    serve_parser.add_argument("--lemonsqueezy-live", action="store_true")

    verify_parser = subparsers.add_parser("verify-audit", help="verify the audit hash chain")
    verify_parser.add_argument("--db", default="var/academy-core.db")

    args = parser.parse_args()
    if args.command == "init":
        initialize(args.db)
        print("initialized %s" % args.db)
        return
    if args.command == "verify-audit":
        result = AcademyCore(args.db).verify_audit_chain()
        print("valid=%s entries=%s head_hash=%s" % (
            result["valid"], result.get("entries", 0), result.get("head_hash", "")))
        raise SystemExit(0 if result["valid"] else 1)

    ipn_secret = os.environ.get(args.nowpayments_ipn_secret_env)
    application = create_app(
        args.db,
        args.live_payment_provider,
        args.live_payment_gate_ref,
        nowpayments_ipn_secret=ipn_secret,
        nowpayments_sandbox=not args.nowpayments_live,
        nowpayments_api_key=os.environ.get("NOWPAYMENTS_API_KEY"),
        nowpayments_checkout_token=os.environ.get("ABC4RD_CHECKOUT_TOKEN"),
        nowpayments_ipn_url=os.environ.get(
            "NOWPAYMENTS_IPN_URL",
            "https://payments.abc4rd.org/v1/payments/nowpayments/ipn",
        ),
        nowpayments_success_url=os.environ.get(
            "NOWPAYMENTS_SUCCESS_URL",
            "https://payments.abc4rd.org/checkout/success",
        ),
        nowpayments_cancel_url=os.environ.get(
            "NOWPAYMENTS_CANCEL_URL",
            "https://payments.abc4rd.org/checkout/cancel",
        ),
        lemonsqueezy_webhook_secret=os.environ.get(
            "LEMONSQUEEZY_WEBHOOK_SECRET"
        ),
        lemonsqueezy_test_mode=not args.lemonsqueezy_live,
        lemonsqueezy_api_key=os.environ.get("LEMONSQUEEZY_API_KEY"),
        lemonsqueezy_store_id=os.environ.get("LEMONSQUEEZY_STORE_ID"),
        lemonsqueezy_variant_id=os.environ.get("LEMONSQUEEZY_VARIANT_ID"),
        lemonsqueezy_success_url=os.environ.get(
            "LEMONSQUEEZY_SUCCESS_URL",
            "https://payments.abc4rd.org/checkout/success",
        ),
    )
    with make_server(args.host, args.port, application) as server:
        print(
            "academy-core listening on http://%s:%d (NO PAYMENT SETTLEMENT)"
            % (args.host, args.port)
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("academy-core stopped")


if __name__ == "__main__":
    main()
