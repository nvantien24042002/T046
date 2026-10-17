"""Fail-closed local issuer paths/ACLs. No runtime dataset contents are opened."""
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess

from storage.files import no_links, regular


class IssuerUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Local development issuer unavailable; review private configuration locally.")


def private_acl(path, *, protected=False):
    """Allow current user, SYSTEM and local Administrators only on Windows.

    POSIX uses current owner and no group/other permissions. Local administrators
    and the authorized owner remain trusted; this is not a race-proof sandbox.
    """
    info = path.stat()
    if os.name != "nt":
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise IssuerUnavailable()
        return
    # Encoded literal path prevents command interpolation. Output contains only
    # permission metadata, never credential file contents or application secrets.
    encoded_path = base64.b64encode(str(path).encode("utf-16le")).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop';"
        "$p=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('"+encoded_path+"'));"
        "$a=Get-Acl -LiteralPath $p;"
        "$sid=[Security.Principal.SecurityIdentifier];"
        "$me=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
        "$rules=@($a.GetAccessRules($true,$true,$sid)|ForEach-Object{"
        "[pscustomobject]@{sid=$_.IdentityReference.Value;type=$_.AccessControlType.ToString()}});"
        "[pscustomobject]@{me=$me;owner=$a.GetOwner($sid).Value;"
        "protected=$a.AreAccessRulesProtected;rules=$rules}|ConvertTo-Json -Compress -Depth 4"
    )
    try:
        output = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16le")).decode("ascii")],
            stderr=subprocess.DEVNULL, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        data = json.loads(output)
        owner_allowed = {data["me"], "S-1-5-18", "S-1-5-32-544"}
        allowed = owner_allowed | {"S-1-3-4"}
        permits = [rule for rule in data["rules"] if rule["type"] == "Allow"]
        if (data["owner"] not in owner_allowed or not permits
                or any(rule["sid"] not in allowed for rule in permits)
                or protected and not data["protected"]):
            raise IssuerUnavailable()
    except Exception:
        raise IssuerUnavailable() from None


def absolute(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise IssuerUnavailable()
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or str(path).startswith(("\\\\", "//")):
        raise IssuerUnavailable()
    return Path(os.path.abspath(path))


@dataclass(frozen=True)
class IssuerPaths:
    private_dir: Path
    ledger: Path
    credentials: Path
    dataset_root: Path

    @property
    def claims(self):
        return self.private_dir / (self.ledger.name+".consumed")

    def validate(self, *, initializing=False):
        try:
            root, ledger, credentials, dataset = [absolute(value) for value in
                (self.private_dir, self.ledger, self.credentials, self.dataset_root)]
            forbidden = [Path(__file__).resolve().parents[1], dataset]
            if os.name == "nt":
                forbidden.append(Path(r"D:\CVATPrivateUploads"))
                import ctypes
                drive_type = ctypes.windll.kernel32.GetDriveTypeW
                drive_type.argtypes, drive_type.restype = [ctypes.c_wchar_p], ctypes.c_uint
                if drive_type(str(root.anchor)) != 3:
                    raise IssuerUnavailable()  # fixed local volume only
            if (ledger.parent != root or credentials.parent != root
                    or ledger == credentials or credentials.name in {
                        ledger.name+suffix for suffix in ("-journal", "-wal", "-shm", ".consumed")}):
                raise IssuerUnavailable()
            # Lexical rejection happens before probing an excluded dataset root.
            if any(root == item or root.is_relative_to(item) or item.is_relative_to(root)
                   for item in forbidden):
                raise IssuerUnavailable()
            for parent in (root, *root.parents):
                if (parent / ".git").exists():
                    raise IssuerUnavailable()
            no_links(root)
            if not root.is_dir():
                raise IssuerUnavailable()
            private_acl(root, protected=True)
            for path in (ledger, credentials, *(Path(str(ledger)+suffix) for suffix in ("-journal", "-wal", "-shm"))):
                regular(path)
                if path.exists():
                    private_acl(path)
            no_links(self.claims)
            if self.claims.exists():
                if not self.claims.is_dir():
                    raise IssuerUnavailable()
                private_acl(self.claims)
            if not initializing and (not ledger.is_file() or not credentials.is_file() or not self.claims.is_dir()):
                raise IssuerUnavailable()
        except Exception:
            raise IssuerUnavailable() from None


def from_environment():
    try:
        if os.environ.get("N2_05B_LOCAL_DEV") != "1":
            raise IssuerUnavailable()
        paths = IssuerPaths(*[absolute(os.environ[key]) for key in (
            "N2_05B_ISSUER_PRIVATE_DIR", "N2_05B_ISSUER_LEDGER",
            "N2_05B_ISSUER_CREDENTIALS", "N2_05B_ISSUER_DATASET_ROOT")])
        if os.environ.get("CVAT_UPLOAD_DIR") and paths.dataset_root != absolute(os.environ["CVAT_UPLOAD_DIR"]):
            raise IssuerUnavailable()
        return paths
    except Exception:
        raise IssuerUnavailable() from None
