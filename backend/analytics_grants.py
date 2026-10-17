"""In-memory grants. Production issuer integration is deliberately absent."""
from dataclasses import dataclass, field
import hashlib
import math
import secrets
import threading
import time
from typing import Callable, Protocol

from backend.analytics_contracts import AnalyticsError, AUDIENCE, ORIGIN


@dataclass(frozen=True)
class IssuerRecord:
    issued_at: int
    audience: str
    origin: str
    dataset_id: str
    scopes: frozenset[str]
    result_ids: frozenset[str]
    upstream_capability: str = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, "scopes", frozenset(self.scopes))
        object.__setattr__(self, "result_ids", frozenset(self.result_ids))


class TrustedIssuer(Protocol):
    def consume(self, code: str) -> IssuerRecord | None:
        """Atomically consume once, including across intermediary restarts.

        Authenticate/authorize issuance out of band. Never reissue a code value.
        Return only server-side records; this interface has no HTTP issuer route.
        """


@dataclass(frozen=True)
class Grant:
    record: IssuerRecord = field(repr=False)
    expires_at: int


class GrantStore:
    def __init__(self, *, clock: Callable[[], float] = time.time, capacity=10000):
        self.clock, self.capacity = clock, capacity
        self._grants = {}
        self._used = {}
        self._lock = threading.RLock()

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).digest()

    def now(self):
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise AnalyticsError(503, "BACKEND_UNAVAILABLE")
        return value

    def _purge(self, now):
        self._grants = {key: grant for key, grant in self._grants.items() if now < grant.expires_at}
        self._used = {key: expiry for key, expiry in self._used.items() if now < expiry}

    def exchange(self, code, issuer):
        with self._lock:
            now = self.now()
            self._purge(now)
            key = self.digest(code)
            if key in self._used:
                raise AnalyticsError(401, "ACCESS_GRANT_INVALID")
            if len(self._grants) >= self.capacity or len(self._used) >= self.capacity:
                raise AnalyticsError(503, "BACKEND_UNAVAILABLE")
            record = issuer.consume(code)
            now = self.now()
            if not isinstance(record, IssuerRecord):
                raise AnalyticsError(401, "ACCESS_GRANT_INVALID")
            from pydantic import TypeAdapter
            from backend.analytics_contracts import ID
            try:
                from backend.analytics_contracts import encoded, METADATA_LIMIT
                encoded({"dataset_id": record.dataset_id, "scopes": sorted(record.scopes),
                         "result_ids": sorted(record.result_ids)}, METADATA_LIMIT)
                TypeAdapter(ID).validate_python(record.dataset_id)
                for result in record.result_ids:
                    TypeAdapter(ID).validate_python(result)
                valid = (type(record.issued_at) is int and record.issued_at <= now < record.issued_at + 60
                         and record.audience == AUDIENCE and record.origin == ORIGIN
                         and bool(record.upstream_capability)
                         and record.scopes in {frozenset({"summary:read"}), frozenset({"summary:read", "results:read"})}
                         and ("results:read" in record.scopes or not record.result_ids))
            except (ValueError, TypeError, AnalyticsError):
                valid = False
            # Issuer already consumed the code; reject rather than expand scopes.
            if not valid:
                raise AnalyticsError(422, "INVALID_ACCESS_REQUEST")
            stamp = int(now)
            token = secrets.token_urlsafe(32)
            grant = Grant(record, stamp + 600)
            self._used[key] = record.issued_at + 60
            self._grants[self.digest(token)] = grant
            return token, grant, stamp

    def authorize(self, token, dataset_id, *, results=False, result_id=None):
        with self._lock:
            grant = self._grants.get(self.digest(token))
            if grant is None:
                raise AnalyticsError(401, "ACCESS_GRANT_INVALID")
            if self.now() >= grant.expires_at:
                self._grants.pop(self.digest(token), None)
                raise AnalyticsError(401, "ACCESS_GRANT_INVALID")
            record = grant.record
            if record.audience != AUDIENCE or record.origin != ORIGIN:
                raise AnalyticsError(401, "ACCESS_GRANT_INVALID")
            required = {"summary:read", "results:read"} if results else {"summary:read"}
            if (dataset_id != record.dataset_id or not required <= record.scopes
                    or result_id is not None and result_id not in record.result_ids):
                raise AnalyticsError(403, "ACCESS_SCOPE_DENIED")
            return grant

    def revoke(self, token):
        """Trusted in-process control, not a public administration endpoint."""
        with self._lock:
            return self._grants.pop(self.digest(token), None) is not None
