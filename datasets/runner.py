"""One guarded worker, bounded protocol, cancellation and wall-time watchdogs."""
from dataclasses import asdict
import json
import os
import queue
import subprocess
import sys
import threading
import time

from datasets.limits import ValidationError


class WorkerUnconfirmed(RuntimeError):
    """No terminal outcome is safe until process termination is confirmed."""


class WindowsJob:
    """Parent-owned handle: process memory cap and kill worker on parent death."""
    def __init__(self, process, memory):
        import ctypes as c
        from ctypes import wintypes as w
        class Basic(c.Structure):
            _fields_ = [("per_process",c.c_longlong),("per_job",c.c_longlong),("flags",w.DWORD),
                ("min_ws",c.c_size_t),("max_ws",c.c_size_t),("active",w.DWORD),
                ("affinity",c.c_size_t),("priority",w.DWORD),("scheduling",w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(name,c.c_ulonglong) for name in ("read","write","other","read_bytes","write_bytes","other_bytes")]
        class Extended(c.Structure):
            _fields_ = [("basic",Basic),("io",IO),("process_memory",c.c_size_t),
                        ("job_memory",c.c_size_t),("peak_process",c.c_size_t),("peak_job",c.c_size_t)]
        self.api = c.WinDLL("kernel32",use_last_error=True)
        self.api.CreateJobObjectW.argtypes,self.api.CreateJobObjectW.restype = [c.c_void_p,w.LPCWSTR],w.HANDLE
        self.api.SetInformationJobObject.argtypes,self.api.SetInformationJobObject.restype = [w.HANDLE,c.c_int,c.c_void_p,w.DWORD],w.BOOL
        self.api.AssignProcessToJobObject.argtypes,self.api.AssignProcessToJobObject.restype = [w.HANDLE,w.HANDLE],w.BOOL
        self.api.CloseHandle.argtypes,self.api.CloseHandle.restype = [w.HANDLE],w.BOOL
        self.handle = self.api.CreateJobObjectW(None,None)
        info = Extended()
        info.basic.flags = 0x2000 | 0x100 | 0x8  # kill-on-close, process memory, one process.
        info.basic.active = 1
        info.process_memory = memory
        if (not self.handle or not self.api.SetInformationJobObject(self.handle,9,c.byref(info),c.sizeof(info))
                or not self.api.AssignProcessToJobObject(self.handle,int(process._handle))):
            self.close()
            raise ValidationError("RESOURCE_GUARD_UNAVAILABLE")

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class WorkerRunner:
    def __init__(self, *, module="datasets.worker", started=lambda process: None):
        self.module, self.started = module, started

    def run(self, payload, work, dataset_id, limits, *, cancelled=lambda: False, progress=lambda phase: None,
            space_ok=lambda: True):
        env = os.environ.copy()
        executable = sys.executable
        if os.name == "nt" and sys.executable != sys._base_executable:
            # Same venv redirector bypass used by Python multiprocessing, so the
            # job attaches to the actual worker, not a launcher with a racing child.
            executable = sys._base_executable
            env["__PYVENV_LAUNCHER__"] = sys.executable
        process = subprocess.Popen([executable,"-m",self.module], stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
        job, stopped = None, threading.Event()
        messages = queue.Queue(maxsize=16)
        def receive():
            try:
                while not stopped.is_set():
                    line = process.stdout.readline(4097)
                    if not line:
                        break
                    if len(line) > 4096:
                        line = b'{"error":"WORKER_PROTOCOL_LIMIT"}'
                    while not stopped.is_set():
                        try:
                            messages.put(line,timeout=0.1)
                            break
                        except queue.Full:
                            pass
            except (OSError,ValueError):
                pass
        reader = threading.Thread(target=receive,daemon=True)
        failure = None
        reported_error = None
        def consume(line, *, phases=True):
            nonlocal reported_error, decode_start, success
            try:
                message = json.loads(line)
                if not isinstance(message,dict):
                    raise ValueError()
            except (ValueError,TypeError):
                if reported_error is None:
                    reported_error = ValidationError("WORKER_PROTOCOL_LIMIT")
                raise reported_error
            if "error" in message:
                code = message["error"]
                if not isinstance(code,str) or not code or len(code)>80 or not all(c.isupper() or c=="_" for c in code):
                    code = "WORKER_FAILED"
                if reported_error is None:
                    reported_error = ValidationError(code)
                raise reported_error
            if phases:
                phase = message.get("phase")
                if phase == "decode_start":
                    decode_start = time.monotonic()
                elif phase == "decode_end":
                    decode_start = None
                elif phase == "indexing":
                    progress("indexing")
                elif phase == "done":
                    success = True
        decode_start, success = None,False
        try:
            if os.name == "nt":
                job = WindowsJob(process,limits.worker_memory_bytes)
            elif not sys.platform.startswith("linux"):
                raise ValidationError("UNSUPPORTED_RESOURCE_GUARD_PLATFORM")
            raw = json.dumps({"payload":str(payload),"work":str(work),"dataset_id":dataset_id,
                              "limits":asdict(limits),"parent_pid":os.getpid()}).encode()+b"\n"
            if len(raw) > 65536:
                raise ValidationError("WORKER_REQUEST_LIMIT")
            reader.start()
            process.stdin.write(raw)
            process.stdin.close()
            self.started(process)
            start, decode_start, success = time.monotonic(),None,False
            while True:
                now = time.monotonic()
                # Observe queued worker failures before deciding to cancel.
                for _ in range(16):
                    try:
                        consume(messages.get_nowait())
                    except queue.Empty:
                        break
                if process.poll() is not None and not reader.is_alive() and messages.empty():
                    break
                if now-start > limits.timeout_seconds or decode_start is not None and now-decode_start > limits.decode_seconds:
                    raise ValidationError("VALIDATION_TIMEOUT")
                if not space_ok():
                    raise ValidationError("INSUFFICIENT_STORAGE")
                if cancelled():
                    if process.poll() not in (None,0):
                        raise ValidationError("WORKER_FAILED")
                    raise ValidationError("VALIDATION_CANCELLED")
                try:
                    line = messages.get(timeout=0.05)
                    consume(line)
                except queue.Empty:
                    if process.poll() is not None and not reader.is_alive() and messages.empty():
                        break
            if process.wait(timeout=5) != 0 or not success:
                raise ValidationError("WORKER_FAILED")
        except BaseException as error:
            failure = error
        finally:
            cleanup_error = None
            try:
                if job:
                    job.close()
            except Exception as error:
                cleanup_error = error
            try:
                if process.poll() is None:
                    if os.name == "nt":
                        process.kill()
                    else:
                        import signal
                        os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception as error:
                cleanup_error = cleanup_error or error
            try:
                process.wait(timeout=10)
            except Exception as error:
                cleanup_error = cleanup_error or error
            try:
                confirmed = process.poll() is not None
            except Exception as error:
                cleanup_error = cleanup_error or error
                confirmed = False
            if confirmed and reader.ident is not None:
                deadline = time.monotonic()+2
                while True:
                    for _ in range(16):
                        try:
                            consume(messages.get_nowait(),phases=False)
                        except queue.Empty:
                            break
                        except ValidationError:
                            pass
                    if not reader.is_alive() and messages.empty():
                        break
                    if time.monotonic() >= deadline:
                        cleanup_error = cleanup_error or RuntimeError("WORKER_READER_UNCONFIRMED")
                        break
                    reader.join(timeout=0.01)
            stopped.set()
            for file in (process.stdin,process.stdout):
                if file is process.stdout and reader.is_alive():
                    # A live pipe reader may hold BufferedReader's lock. Closing
                    # it here could hang instead of reporting uncertain cleanup.
                    continue
                if file and not file.closed:
                    try:
                        file.close()
                    except Exception as error:
                        cleanup_error = cleanup_error or error
            if not confirmed:
                error = WorkerUnconfirmed("WORKER_STOP_UNCONFIRMED")
                error.process = process
                raise error from (cleanup_error or failure)
            if reported_error is not None and (failure is None or isinstance(failure,ValidationError)):
                failure = reported_error
            if cleanup_error is not None:
                if failure is None or str(failure) == "VALIDATION_CANCELLED":
                    failure = ValidationError("WORKER_CLEANUP_FAILED")
                failure.cleanup_error = cleanup_error
            if failure is not None:
                raise failure
