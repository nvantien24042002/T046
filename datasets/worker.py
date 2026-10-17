"""Private stdin/stdout worker protocol, never an HTTP service or public CLI."""
import json
import os
from pathlib import Path
import sqlite3
import sys

from datasets.limits import Limits, ValidationError


def emit(message):
    print(json.dumps(message),flush=True)


def main():
    try:
        raw = sys.stdin.buffer.readline(65537)
        if len(raw)>65536:
            raise ValidationError("WORKER_REQUEST_LIMIT")
        request = json.loads(raw)
        limits = Limits(**request["limits"])
        if os.name != "nt":
            import ctypes
            import resource
            import signal
            if not sys.platform.startswith("linux"):
                raise ValidationError("UNSUPPORTED_RESOURCE_GUARD_PLATFORM")
            # Parent death terminates an isolated Linux child; Windows uses the
            # parent-owned job object established before stdin is sent.
            if ctypes.CDLL(None).prctl(1,signal.SIGKILL,0,0,0) != 0 or os.getppid() != request["parent_pid"]:
                raise ValidationError("RESOURCE_GUARD_UNAVAILABLE")
            resource.setrlimit(resource.RLIMIT_AS,(limits.worker_memory_bytes,limits.worker_memory_bytes))
        from datasets.validate import validate_archive
        validate_archive(Path(request["payload"]),Path(request["work"]),request["dataset_id"],limits,emit)
        emit({"phase":"done"})
        return 0
    except ValidationError as error:
        emit({"error":str(error)})
    except MemoryError:
        emit({"error":"WORKER_MEMORY_LIMIT"})
    except sqlite3.Error:
        emit({"error":"INDEXING_FAILED"})
    except OSError:
        emit({"error":"VALIDATION_IO_FAILED"})
    except Exception:
        emit({"error":"VALIDATION_FAILED"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
