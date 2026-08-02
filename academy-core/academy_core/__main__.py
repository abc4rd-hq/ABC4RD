import argparse
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

    application = create_app(
        args.db, args.live_payment_provider, args.live_payment_gate_ref
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
