"""Private interactive operator actions only. Never an HTTP issuance endpoint."""
import argparse
import getpass
import sys

from backend.local_dev_issuer import LocalDevelopmentIssuer
from backend.local_issuer_paths import from_environment


def private_terminal():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("Private terminal required")


def confirm():
    if input("After checking requester/dataset authorization, type AUTHORIZE: ") != "AUTHORIZE":
        raise RuntimeError("Not authorized")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local-development issuer; use a private, unrecorded terminal.")
    parser.add_argument("action", choices=["init", "add-credential", "issue", "revoke-code", "revoke-credential"])
    args = parser.parse_args(argv)
    try:
        private_terminal()
        paths = from_environment()
        if args.action == "init":
            confirm()
            LocalDevelopmentIssuer.initialize(paths)
            print("Private development issuer initialized; no credentials or codes created.")
            return 0
        issuer = LocalDevelopmentIssuer(paths)
        if args.action == "add-credential":
            reference = input("New private credential reference: ").strip()
            dataset = input("Authorized existing synthetic dataset ID: ").strip()
            upload = input("Associated existing upload ID: ").strip()
            scopes = input("Authorized scopes (comma separated): ").strip().split(",")
            results = input("Authorized result IDs (comma separated; empty for summary): ").strip()
            capability = getpass.getpass("Existing upstream capability (hidden): ")
            confirm()
            issuer.add_credential(reference, dataset_id=dataset, upload_id=upload, capability=capability,
                                  scopes=[s.strip() for s in scopes],
                                  result_ids=[s.strip() for s in results.split(",")] if results else [])
            print("Credential association stored privately.")
        elif args.action == "issue":
            requester = input("Authorized requester identity: ").strip()
            reference = input("Private credential reference: ").strip()
            dataset = input("Authorized existing synthetic dataset ID: ").strip()
            scopes = input("Explicit authorized scopes (comma separated): ").strip().split(",")
            results = input("Explicit authorized result IDs (comma separated; empty for summary): ").strip()
            confirm()
            code = issuer.issue(requester=requester, credential_ref=reference, dataset_id=dataset,
                                scopes=[s.strip() for s in scopes],
                                result_ids=[s.strip() for s in results.split(",")] if results else [])
            # Deliberate one-time private console display, never logger/file/argv.
            # Refusal of redirection happens before issuance; operator must disable
            # terminal transcription/recording. This is not production authentication.
            print("Single-use code (60 seconds; private handoff only): "+code)
        elif args.action == "revoke-code":
            code = getpass.getpass("Code to retire (hidden): ")
            confirm()
            issuer.revoke_code(code)
            print("Code retirement requested. Existing grants are separate.")
        else:
            reference = input("Credential reference to revoke: ").strip()
            confirm()
            issuer.revoke_credential(reference)
            print("Credential revoked for future exchanges. Existing grants are separate.")
        return 0
    except Exception:
        print("Local issuer action failed; no authority is returned. Review private state locally.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
