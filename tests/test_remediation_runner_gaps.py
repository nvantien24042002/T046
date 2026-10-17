"""Runner fault boundaries; process/thread doubles never launch OS workers."""
import itertools
import json
import queue
import threading
from types import SimpleNamespace

import pytest

import datasets.runner as module
from datasets.limits import ValidationError
from datasets.runner import WorkerRunner, WorkerUnconfirmed
from tests.test_runner_cancellation_faults import Process, install, run


@pytest.mark.parametrize("wire,code", [
    (b"{broken\n", "WORKER_PROTOCOL_LIMIT"),
    (b"[]\n", "WORKER_PROTOCOL_LIMIT"),
    (b"null\n", "WORKER_PROTOCOL_LIMIT"),
    *[(json.dumps({"error": value}).encode()+b"\n", "WORKER_FAILED")
      for value in (None, 12, "", "bad-code", "X"*81)],
    (b"X"*4097+b"\n", "WORKER_PROTOCOL_LIMIT"),
])
@pytest.mark.parametrize("during_drain", [False, True])
def test_malformed_messages_keep_safe_error(monkeypatch, wire, code, during_drain):
    gate = threading.Event() if during_drain else None
    process = Process(output=wire, gate=gate)
    install(monkeypatch, process)
    queued = threading.Event()
    original = queue.Queue
    class ObservedQueue(original):
        def put(self, item, *args, **kwargs):
            super().put(item, *args, **kwargs)
            queued.set()
    monkeypatch.setattr(module.queue, "Queue", ObservedQueue)
    def started(_):
        if not during_drain:
            assert queued.wait(5)
    with pytest.raises(ValidationError, match="^"+code+"$"):
        run(WorkerRunner(started=started))
    assert process.returncode is not None


def test_live_reader_cleanup_is_bounded_and_does_not_close_stdout(monkeypatch):
    process = Process()
    install(monkeypatch, process)
    class StuckReader:
        ident = 123
        joins = 0
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass
        def is_alive(self):
            return True
        def join(self, timeout):
            assert timeout == .01
            type(self).joins += 1
    monkeypatch.setattr(module.threading, "Thread", StuckReader)
    ticks = itertools.count()
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(ValidationError, match="^WORKER_CLEANUP_FAILED$") as caught:
        run(WorkerRunner())
    assert str(caught.value.cleanup_error) == "WORKER_READER_UNCONFIRMED"
    assert StuckReader.joins <= 2
    assert not process.stdout.closed
    assert process.returncode is not None


@pytest.mark.parametrize("kill_error,wait_error,exit_on_wait,poll_error,uncertain", [
    (False, False, False, False, False),
    (False, True, False, False, False),
    (True, False, True, False, False),
    (True, True, False, False, True),
    (False, False, False, True, True),
])
def test_stop_confirmation_combinations(monkeypatch, kill_error, wait_error,
                                       exit_on_wait, poll_error, uncertain):
    class ControlledProcess(Process):
        waited = False
        def wait(self, timeout=None):
            self.waited = True
            if exit_on_wait:
                self.returncode = -9
            return super().wait(timeout)
        def poll(self):
            if poll_error and self.waited:
                raise OSError("injected final poll failure")
            return super().poll()
    process = ControlledProcess(kill_error=kill_error, wait_error=wait_error)
    install(monkeypatch, process)
    expected = WorkerUnconfirmed if uncertain else ValidationError
    with pytest.raises(expected) as caught:
        run(WorkerRunner())
    if uncertain:
        assert caught.value.process is process
        assert caught.value.__cause__ is not None
    else:
        assert process.returncode is not None
        assert str(caught.value) == ("WORKER_CLEANUP_FAILED" if kill_error or wait_error else "VALIDATION_CANCELLED")


@pytest.mark.parametrize("cleanup", ["job", "pipe"])
@pytest.mark.parametrize("original", ["cancel", "validation", "runtime", "unconfirmed"])
def test_cleanup_preserves_original_error_and_cause(monkeypatch, cleanup, original):
    process = Process(close_error=cleanup=="pipe", kill_error=original=="unconfirmed",
                      wait_error=original=="unconfirmed")
    install(monkeypatch, process)
    if cleanup == "job":
        class BadJob:
            def close(self):
                raise OSError("injected job close failure")
        monkeypatch.setattr(module, "WindowsJob", lambda *args: BadJob())
        # Exercise the job path on every test platform without mutating os.name globally.
        monkeypatch.setattr(module, "os", SimpleNamespace(
            name="nt", environ=module.os.environ, getpid=module.os.getpid))
        monkeypatch.setattr(module.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    failure = ValidationError("ORIGINAL_FAILURE") if original=="validation" else RuntimeError("original")
    def started(_):
        if original in {"validation", "runtime"}:
            raise failure
    with pytest.raises((ValidationError, RuntimeError)) as caught:
        run(WorkerRunner(started=started))
    if original == "unconfirmed":
        assert isinstance(caught.value, WorkerUnconfirmed)
        assert caught.value.process is process and caught.value.__cause__ is not None
    elif original == "cancel":
        assert str(caught.value) == "WORKER_CLEANUP_FAILED"
        assert isinstance(caught.value.cleanup_error, OSError)
    else:
        assert caught.value is failure
        assert isinstance(caught.value.cleanup_error, OSError)
