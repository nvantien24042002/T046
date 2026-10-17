"""Read-only staging preflight validator for N2-05B E2E execution.

Validates environment variables, staging storage root, CORS origin configuration,
private issuer store, ResultStore and cache compatibility, port availability,
and registered route paths without making live network requests.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
from typing import Literal

from backend.analytics_cache_adapter import load_staging_cache_directory
from backend.analytics_configuration import PRODUCTION_REGISTRY
from backend.analytics_contracts import AUDIENCE, ORIGIN, UPSTREAM
from backend.analytics_intermediary import create_analytics_app
from backend.analytics_storage import ResultStore


Status = Literal["READY", "BLOCKED", "WARN"]


@dataclass
class CheckResult:
    category: str
    name: str
    status: Status
    details: str
    remediation: str | None = None


def check_port_free(port: int) -> bool:
    """Check if a TCP port on loopback is currently free."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            # Try binding to loopback
            sock.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False


def run_preflight() -> list[CheckResult]:
    """Execute all read-only preflight checks."""
    results: list[CheckResult] = []

    # -------------------------------------------------------------
    # 1. Environment Variable Presence
    # -------------------------------------------------------------
    required_env_vars = [
        "CVAT_ALLOWED_ORIGINS",
        "N2_05B_LOCAL_DEV",
        "N2_05B_ISSUER_PRIVATE_DIR",
        "N2_05B_ISSUER_LEDGER",
        "N2_05B_ISSUER_CREDENTIALS",
        "N2_05B_ISSUER_DATASET_ROOT",
    ]

    for var in required_env_vars:
        present = var in os.environ and bool(os.environ[var].strip())
        if present:
            results.append(CheckResult(
                category="Environment",
                name=var,
                status="READY",
                details="Variable is present in host environment.",
            ))
        else:
            results.append(CheckResult(
                category="Environment",
                name=var,
                status="BLOCKED",
                details="Variable is missing or empty in host environment.",
                remediation=f"Set ${var} in operator environment before service launch.",
            ))

    # -------------------------------------------------------------
    # 2. Upstream CORS Origin Configuration
    # -------------------------------------------------------------
    configured_origins = os.environ.get("CVAT_ALLOWED_ORIGINS", "")
    origin_list = [o.strip() for o in configured_origins.split(",") if o.strip()]
    if ORIGIN in origin_list:
        results.append(CheckResult(
            category="CORS",
            name="Frontend Origin Whitelist",
            status="READY",
            details=f"Origin '{ORIGIN}' is explicitly present in CVAT_ALLOWED_ORIGINS.",
        ))
    else:
        results.append(CheckResult(
            category="CORS",
            name="Frontend Origin Whitelist",
            status="BLOCKED",
            details=f"Origin '{ORIGIN}' is missing from CVAT_ALLOWED_ORIGINS.",
            remediation=f"Set CVAT_ALLOWED_ORIGINS={ORIGIN} (or include it in comma-separated list).",
        ))

    # -------------------------------------------------------------
    # 3. Staging Dataset Root
    # -------------------------------------------------------------
    staging_root_env = os.environ.get("N2_05B_ISSUER_DATASET_ROOT") or os.environ.get("CVAT_UPLOAD_DIR")
    expected_root = Path(r"D:\CVATPrivateUploads\stage3-data")
    staging_root = Path(staging_root_env).resolve() if staging_root_env else expected_root

    if staging_root.is_dir() and (staging_root / "registry.sqlite3").is_file():
        # Query ready datasets
        try:
            conn = sqlite3.connect(staging_root / "registry.sqlite3")
            cursor = conn.execute(
                "SELECT dataset_id, attempt_id FROM validation_datasets WHERE state='ready'"
            )
            ready_datasets = cursor.fetchall()
            conn.close()
            results.append(CheckResult(
                category="Staging Datasets",
                name="Staging Root & Registry",
                status="READY",
                details=f"Root exists at {staging_root}. Found {len(ready_datasets)} ready datasets in registry.",
            ))
        except Exception as exc:
            results.append(CheckResult(
                category="Staging Datasets",
                name="Staging Root & Registry",
                status="BLOCKED",
                details=f"Failed to query registry: {type(exc).__name__}",
                remediation="Ensure registry.sqlite3 is not corrupted or locked.",
            ))
    else:
        results.append(CheckResult(
            category="Staging Datasets",
            name="Staging Root & Registry",
            status="BLOCKED",
            details=f"Staging root {staging_root} does not exist or lacks registry.sqlite3.",
            remediation="Ensure staging storage root D:\\CVATPrivateUploads\\stage3-data exists.",
        ))

    # -------------------------------------------------------------
    # 4. Private Issuer Storage & ACLs
    # -------------------------------------------------------------
    issuer_dir_env = os.environ.get("N2_05B_ISSUER_PRIVATE_DIR")
    if issuer_dir_env:
        p_dir = Path(issuer_dir_env).resolve()
        ledger_path = Path(os.environ.get("N2_05B_ISSUER_LEDGER", "")).resolve() if os.environ.get("N2_05B_ISSUER_LEDGER") else None
        creds_path = Path(os.environ.get("N2_05B_ISSUER_CREDENTIALS", "")).resolve() if os.environ.get("N2_05B_ISSUER_CREDENTIALS") else None

        if p_dir.is_dir() and ledger_path and ledger_path.is_file() and creds_path and creds_path.is_file():
            # Check credentials format
            try:
                creds_data = json.loads(creds_path.read_text(encoding="utf-8"))
                creds_count = len(creds_data.get("credentials", {}))
                results.append(CheckResult(
                    category="Issuer",
                    name="Private Issuer Storage",
                    status="READY",
                    details=f"Private issuer initialized at {p_dir}. Found {creds_count} mapped credentials.",
                ))
            except Exception as exc:
                results.append(CheckResult(
                    category="Issuer",
                    name="Private Issuer Storage",
                    status="BLOCKED",
                    details=f"Failed to read credentials file: {type(exc).__name__}",
                    remediation="Re-initialize issuer credentials file using backend.local_issuer_cli.",
                ))
        else:
            results.append(CheckResult(
                category="Issuer",
                name="Private Issuer Storage",
                status="BLOCKED",
                details=f"Issuer files not fully initialized in {p_dir}.",
                remediation="Run 'python -m backend.local_issuer_cli init' in private terminal.",
            ))
    else:
        results.append(CheckResult(
            category="Issuer",
            name="Private Issuer Storage",
            status="BLOCKED",
            details="N2_05B_ISSUER_PRIVATE_DIR is not set.",
            remediation="Define N2_05B_ISSUER_PRIVATE_DIR (e.g. C:\\private\\issuer).",
        ))

    # -------------------------------------------------------------
    # 5. ResultStore & Cache Compatibility
    # -------------------------------------------------------------
    cache_dir = staging_root / ".analysis-cache-v1"
    sealed_dir = staging_root / ".datasets-v1" / "sealed"
    if cache_dir.is_dir():
        cache_files = list(cache_dir.glob("*.json"))
        if cache_files:
            # Audit cache files against Payload schema
            from backend.analytics_cache_adapter import validate_cache_payload
            malformed = []
            for cf in cache_files:
                try:
                    cdata = json.loads(cf.read_text(encoding="utf-8"))
                    ds_id = cdata.get("dataset_id")
                    if not ds_id or not cf.name.startswith(ds_id):
                        malformed.append(f"{cf.name}: dataset_id mismatch")
                    else:
                        validate_cache_payload(cdata, ds_id)
                except Exception as exc:
                    malformed.append(f"{cf.name}: {type(exc).__name__}")

            if not malformed:
                results.append(CheckResult(
                    category="ResultStore & Cache",
                    name="Precomputed Staging Cache",
                    status="READY",
                    details=f"All {len(cache_files)} cache files verified against verified Payload schema.",
                ))
            else:
                results.append(CheckResult(
                    category="ResultStore & Cache",
                    name="Precomputed Staging Cache",
                    status="BLOCKED",
                    details=f"Cache validation failed for {len(malformed)} file(s): {malformed}",
                    remediation="Re-run pipeline analysis to regenerate valid analysis cache bundles.",
                ))
        else:
            results.append(CheckResult(
                category="ResultStore & Cache",
                name="Precomputed Staging Cache",
                status="BLOCKED",
                details=f"No analysis cache JSON files found in {cache_dir}.",
                remediation="Ensure precomputed analysis cache is present in .analysis-cache-v1.",
            ))
    else:
        results.append(CheckResult(
            category="ResultStore & Cache",
            name="Precomputed Staging Cache",
            status="BLOCKED",
            details=f"Cache directory {cache_dir} not found.",
            remediation="Ensure .analysis-cache-v1 exists in staging root.",
        ))

    # Cross-validate sealed manifest hashes against registry payload_sha
    registry_file = staging_root / "registry.sqlite3"
    if registry_file.is_file() and sealed_dir.is_dir():
        try:
            conn = sqlite3.connect(registry_file)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT dataset_id, attempt_id, payload_sha FROM validation_datasets WHERE state='ready'").fetchall()
            conn.close()
            hash_mismatches = []
            for r in rows:
                mf = sealed_dir / r["attempt_id"] / "manifest.json"
                if not mf.is_file():
                    hash_mismatches.append(f"Missing manifest for {r['attempt_id']}")
                    continue
                mdata = json.loads(mf.read_text(encoding="utf-8"))
                if mdata.get("archive_sha256") != r["payload_sha"]:
                    hash_mismatches.append(f"{r['dataset_id']}: manifest hash != registry payload_sha")

            if not hash_mismatches:
                results.append(CheckResult(
                    category="ResultStore & Cache",
                    name="Archive Hash Provenance",
                    status="READY",
                    details=f"All {len(rows)} ready dataset manifests cross-validated with registry payload_sha.",
                ))
            else:
                results.append(CheckResult(
                    category="ResultStore & Cache",
                    name="Archive Hash Provenance",
                    status="BLOCKED",
                    details=f"Hash provenance mismatches detected: {hash_mismatches}",
                    remediation="Investigate sealed dataset directory integrity.",
                ))
        except Exception as exc:
            results.append(CheckResult(
                category="ResultStore & Cache",
                name="Archive Hash Provenance",
                status="BLOCKED",
                details=f"Registry-to-manifest cross-validation failed: {type(exc).__name__}",
                remediation="Check registry.sqlite3 permissions.",
            ))


    # -------------------------------------------------------------
    # 6. Port Availability (Loopback)
    # -------------------------------------------------------------
    ports_to_check = [
        (8011, "Upstream Backend Port"),
        (8012, "Analytics Intermediary Port"),
        (5173, "Frontend Dev Server Port"),
    ]

    for port, label in ports_to_check:
        free = check_port_free(port)
        if free:
            results.append(CheckResult(
                category="Ports",
                name=f"{label} ({port})",
                status="READY",
                details=f"Port {port} is free and ready for binding.",
            ))
        else:
            results.append(CheckResult(
                category="Ports",
                name=f"{label} ({port})",
                status="WARN",
                details=f"Port {port} is currently bound/occupied.",
                remediation=f"Inspect process listening on {port} before launching service.",
            ))

    # -------------------------------------------------------------
    # 7. Application Route Paths Verification
    # -------------------------------------------------------------
    class DummyIssuer:
        def consume(self, code: str):
            return None

    try:
        # Dummy in-memory store check
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dummy_store = ResultStore(Path(td) / "temp.db")
            app_with_store = create_analytics_app(
                issuer=DummyIssuer(),
                store=dummy_store,
                revisions=PRODUCTION_REGISTRY,
            )
            route_paths = [r.path for r in app_with_store.routes]

            expected_paths = [
                "/analytics/v1/grants/exchange",
                "/analytics/v1/datasets/{dataset_id}/summary",
                "/analytics/v1/datasets/{dataset_id}/results",
                "/analytics/v1/datasets/{dataset_id}/results/{result_id}",
                "/analytics/v1/datasets/{dataset_id}/preview",
            ]

            missing = [p for p in expected_paths if p not in route_paths]
            if not missing:
                results.append(CheckResult(
                    category="Routes",
                    name="Application Route Contract",
                    status="READY",
                    details=f"All {len(expected_paths)} expected route paths confirmed registered in FastAPI app.",
                ))
            else:
                results.append(CheckResult(
                    category="Routes",
                    name="Application Route Contract",
                    status="BLOCKED",
                    details=f"Missing expected routes: {missing}",
                    remediation="Check analytics_routes and intermediary registration.",
                ))
    except Exception as exc:
        results.append(CheckResult(
            category="Routes",
            name="Application Route Contract",
            status="BLOCKED",
            details=f"Route inspection failed: {type(exc).__name__}",
            remediation="Verify create_analytics_app factory arguments.",
        ))

    return results


def print_report(results: list[CheckResult]) -> int:
    """Print markdown preflight summary and return exit code."""
    blocked = [r for r in results if r.status == "BLOCKED"]
    warnings = [r for r in results if r.status == "WARN"]
    ready = [r for r in results if r.status == "READY"]

    print("# Staging E2E Preflight Audit Report")
    print()
    print(f"Summary: {len(ready)} READY, {len(warnings)} WARN, {len(blocked)} BLOCKED.")
    print()

    current_cat = None
    for r in results:
        if r.category != current_cat:
            current_cat = r.category
            print(f"### {current_cat}")
        status_marker = f"**{r.status}**"
        print(f"- [{status_marker}] {r.name}: {r.details}")
        if r.remediation and r.status == "BLOCKED":
            print(f"  *Remediation*: {r.remediation}")
    print()

    if blocked:
        print("## Verdict: BLOCKED")
        print(f"{len(blocked)} blocking prerequisite(s) must be remediated before starting live services.")
        return 1

    print("## Verdict: READY")
    print("All staging prerequisites are satisfied.")
    return 0


def main(argv: list[str] | None = None) -> int:
    results = run_preflight()
    return print_report(results)


if __name__ == "__main__":
    raise SystemExit(main())
