"""Staging launcher for Analytics Intermediary with Track B verified result routes.

Binds loopback-only to 127.0.0.1:8012 with single worker, no reload, no proxy-headers trust,
and sanitized error logging. Requires an approved private trusted issuer and configured ResultStore.
"""
import argparse
import importlib
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any

from backend.analytics_cache_adapter import CacheAdapterError, load_staging_cache_directory
from backend.analytics_configuration import PRODUCTION_REGISTRY
from backend.analytics_grants import TrustedIssuer
from backend.analytics_intermediary import create_analytics_app
from backend.analytics_storage import ResultStore


DEFAULT_ISSUER_FACTORY = "backend.local_dev_issuer:create_issuer"
LOOPBACK_HOST = "127.0.0.1"
INTERMEDIARY_PORT = 8012


class SanitizedFormatter(logging.Formatter):
    """Sanitizes log messages to ensure secrets, tokens, and credentials are never emitted."""

    def formatException(self, ei):
        return f"Exception class: {ei[0].__name__}"

    def format(self, record):
        message = str(super().format(record))
        # Redact any Bearer tokens, hex hashes of sensitive length, or private paths if present
        message = re.sub(r"Bearer\s+[A-Za-z0-9_-]+", "Bearer [REDACTED]", message)
        message = re.sub(r"[A-Za-z0-9_-]{32,512}", "[REDACTED_TOKEN]", message)
        return message


def resolve_issuer(factory_path: str) -> TrustedIssuer:
    """Resolve and instantiate approved TrustedIssuer callable."""
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", factory_path):
        raise ValueError("Issuer factory must be in MODULE:CALLABLE format")

    module_name, func_name = factory_path.split(":")
    if module_name == "tests" or module_name.startswith("tests."):
        raise ValueError("Test issuers are prohibited in staging launcher")

    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, func_name)
    except Exception as exc:
        raise ValueError(f"Failed to import issuer factory '{factory_path}': {type(exc).__name__}") from None

    if not callable(factory):
        raise ValueError(f"Issuer factory '{factory_path}' is not callable")

    try:
        issuer = factory()
    except Exception as exc:
        raise ValueError(f"Failed to initialize issuer: {type(exc).__name__}") from None

    if not callable(getattr(issuer, "consume", None)):
        raise ValueError("Trusted issuer must implement 'consume(code)'")

    return issuer


def parse_result_id_map(raw_map: str | None) -> dict[str, str]:
    """Parse result ID mapping from a JSON string or JSON file path."""
    if not raw_map:
        return {}

    candidate_path = Path(raw_map)
    if candidate_path.is_file():
        content = candidate_path.read_text(encoding="utf-8")
    else:
        content = raw_map

    try:
        data = json.loads(content)
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise ValueError("result_id_map must be a JSON object mapping string dataset_ids to string result_ids")
        return data
    except Exception as exc:
        raise ValueError(f"Failed to parse result_id_map: {exc}") from exc


def build_staging_app(
    *,
    issuer_factory: str = DEFAULT_ISSUER_FACTORY,
    result_store_path: str | Path | None = None,
    populate_staging_root: str | Path | None = None,
    result_id_map: dict[str, str] | None = None,
) -> Any:
    """Construct FastAPI application with verified-results routes and ResultStore."""
    issuer = resolve_issuer(issuer_factory)

    store = None
    if result_store_path is not None:
        store_path = Path(result_store_path).resolve()
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store = ResultStore(store_path)

        if populate_staging_root is not None:
            if not result_id_map:
                raise ValueError(
                    "--populate-from-cache requires an explicit --result-id-map to avoid manufacturing synthetic result IDs."
                )
            staging_root = Path(populate_staging_root).resolve()
            if not staging_root.is_dir():
                raise ValueError(f"Staging root directory '{staging_root}' does not exist")
            load_staging_cache_directory(staging_root, store, result_id_map)

    return create_analytics_app(
        issuer=issuer,
        store=store,
        revisions=PRODUCTION_REGISTRY,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for the staging intermediary launcher."""
    parser = argparse.ArgumentParser(
        description="Staging Analytics Intermediary (127.0.0.1:8012 loopback only)."
    )
    parser.add_argument(
        "--issuer-factory",
        default=os.environ.get("N2_05B_ISSUER_FACTORY", DEFAULT_ISSUER_FACTORY),
        metavar="MODULE:CALLABLE",
        help="Approved trusted issuer factory (default: backend.local_dev_issuer:create_issuer).",
    )
    parser.add_argument(
        "--result-store",
        default=os.environ.get("N2_05B_RESULT_STORE"),
        metavar="PATH",
        help="Path to ResultStore SQLite database (enables verified-results routes).",
    )
    parser.add_argument(
        "--populate-from-cache",
        default=os.environ.get("N2_05B_POPULATE_STAGING_ROOT"),
        metavar="STAGING_ROOT",
        help="Path to staging storage root (e.g. D:\\CVATPrivateUploads\\stage3-data) to populate ResultStore on startup.",
    )
    parser.add_argument(
        "--result-id-map",
        default=os.environ.get("N2_05B_RESULT_ID_MAP"),
        metavar="JSON_OR_PATH",
        help="JSON string or file path mapping dataset IDs to explicit result IDs (required with --populate-from-cache).",
    )
    args = parser.parse_args(argv)

    try:
        parsed_map = parse_result_id_map(args.result_id_map)
        app = build_staging_app(
            issuer_factory=args.issuer_factory,
            result_store_path=args.result_store,
            populate_staging_root=args.populate_from_cache,
            result_id_map=parsed_map,
        )
    except Exception as exc:
        print(
            f"Staging launcher initialization failed: {type(exc).__name__}: {exc}. Check private configuration locally.",
            file=sys.stderr,
            flush=True,
        )
        return 2

    import uvicorn

    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "safe": {
                "()": SanitizedFormatter,
                "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
            }
        },
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "formatter": "safe",
                "stream": "ext://sys.stderr",
            }
        },
        "root": {"handlers": ["stderr"], "level": "INFO"},
        "loggers": {"uvicorn": {"handlers": ["stderr"], "level": "WARNING", "propagate": False}},
    }

    uvicorn.run(
        app,
        host=LOOPBACK_HOST,
        port=INTERMEDIARY_PORT,
        workers=1,
        reload=False,
        proxy_headers=False,
        access_log=False,
        log_config=log_config,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
