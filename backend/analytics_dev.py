"""Local Track A composition only; no issuer implementation or public issuance."""
import argparse
import importlib
import re
import sys


def build_app(issuer_factory):
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", issuer_factory):
        raise ValueError("Expected module:callable")
    module, name = issuer_factory.split(":")
    if module == "tests" or module.startswith("tests."):
        raise ValueError("Test issuers are not development deployment providers")
    factory = getattr(importlib.import_module(module), name)
    if not callable(factory):
        raise ValueError("Issuer factory must be callable")
    issuer = factory()
    if not callable(getattr(issuer, "consume", None)):
        raise ValueError("Trusted issuer must implement consume")
    from backend.analytics_intermediary import create_analytics_app
    return create_analytics_app(issuer=issuer)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local Track A; requires an approved private trusted issuer.")
    parser.add_argument("--issuer-factory", required=True, metavar="MODULE:CALLABLE",
                        help="Approved zero-argument factory; never put codes or credentials in this argument.")
    args = parser.parse_args(argv)
    try:
        app = build_app(args.issuer_factory)
    except Exception:
        print("Cannot initialize the approved issuer/application. Check private configuration locally.", file=sys.stderr)
        return 2
    import uvicorn
    # No reload/multiple workers: grant memory belongs to exactly one process.
    # No proxy headers: the application's loopback/Origin checks use direct peers.
    uvicorn.run(app, host="127.0.0.1", port=8012, workers=1, reload=False,
                proxy_headers=False, access_log=False, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
