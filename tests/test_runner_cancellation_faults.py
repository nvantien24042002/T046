"""No OS processes are launched: exercise production runner control flow with pipes."""
import io
import queue
import subprocess
import threading
import itertools
from types import SimpleNamespace

import pytest

from datasets.limits import Limits, ValidationError
from datasets.runner import WorkerRunner, WorkerUnconfirmed


class Pipe(io.BytesIO):
    def __init__(self,data=b"",*,gate=None,close_error=False):
        super().__init__(data)
        self.gate,self.close_error = gate,close_error
    def readline(self,*args):
        if self.gate:
            assert self.gate.wait(5)
        return super().readline(*args)
    def close(self):
        if self.close_error:
            raise OSError("injected pipe close failure")
        super().close()


class Process:
    pid = 987654321  # Never passed to an OS operation by these tests.
    def __init__(self,*,output=b"",code=None,kill_error=False,wait_error=False,gate=None,close_error=False):
        self.stdin = Pipe()
        self.stdout = Pipe(output,gate=gate,close_error=close_error)
        self.returncode,self.kill_error,self.wait_error,self.gate = code,kill_error,wait_error,gate
    def poll(self):
        return self.returncode
    def kill(self):
        if self.gate:
            self.gate.set()
        if self.kill_error:
            raise OSError("injected kill failure")
        self.returncode = -9
    def wait(self,timeout=None):
        if self.wait_error:
            raise subprocess.TimeoutExpired("test worker",timeout)
        return self.returncode


def install(monkeypatch,process):
    import datasets.runner as module
    monkeypatch.setattr(module.subprocess,"Popen",lambda *a,**k: process)
    class Job:
        def close(self):
            pass
    monkeypatch.setattr(module,"WindowsJob",lambda *a: Job())
    if hasattr(module.os,"killpg"):
        monkeypatch.setattr(module.os,"killpg",lambda *a: process.kill())


def run(runner,*,cancelled=lambda: True,limits=None):
    runner.run("fake-payload","fake-work","fake-dataset",limits or Limits(),cancelled=cancelled)


def test_observed_worker_error_wins_cancel(monkeypatch):
    import datasets.runner as module
    process = Process(output=b'{"error":"INDEXING_FAILED"}\n')
    install(monkeypatch,process)
    queued = threading.Event()
    original = queue.Queue
    class ObservedQueue(original):
        def put(self,item,*args,**kwargs):
            super().put(item,*args,**kwargs)
            queued.set()
    monkeypatch.setattr(module.queue,"Queue",ObservedQueue)
    runner = WorkerRunner(started=lambda p: queued.wait(5) or pytest.fail("no worker message"))
    with pytest.raises(ValidationError,match="^INDEXING_FAILED$"):
        run(runner)


def test_error_in_pipe_during_stop_is_not_discarded(monkeypatch):
    process = Process(output=b'{"error":"VALIDATION_IO_FAILED"}\n',gate=threading.Event())
    install(monkeypatch,process)
    with pytest.raises(ValidationError,match="^VALIDATION_IO_FAILED$"):
        run(WorkerRunner())
    assert process.returncode is not None


def test_cancel_requires_confirmed_process_exit(monkeypatch):
    process = Process()
    install(monkeypatch,process)
    with pytest.raises(ValidationError,match="^VALIDATION_CANCELLED$"):
        run(WorkerRunner())
    assert process.returncode is not None


def test_expired_watchdog_is_not_relabelled_cancel(monkeypatch):
    import datasets.runner as module
    process = Process()
    install(monkeypatch,process)
    ticks = itertools.count()
    monkeypatch.setattr(module,"time",SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(ValidationError,match="^VALIDATION_TIMEOUT$"):
        run(WorkerRunner(),limits=Limits(timeout_seconds=1e-12))


def test_natural_failed_exit_is_not_cancel(monkeypatch):
    process = Process(code=1)
    install(monkeypatch,process)
    with pytest.raises(ValidationError,match="^WORKER_FAILED$"):
        run(WorkerRunner())


def test_stop_unconfirmed_is_distinct_and_retains_process_reference(monkeypatch):
    process = Process(kill_error=True,wait_error=True)
    install(monkeypatch,process)
    with pytest.raises(WorkerUnconfirmed) as caught:
        run(WorkerRunner())
    assert caught.value.process is process
    assert process.returncode is None


@pytest.mark.parametrize("stage", ["pipe","wait"])
def test_cleanup_failure_after_reap_is_not_successful_cancel(monkeypatch,stage):
    process = Process(close_error=stage=="pipe",wait_error=stage=="wait")
    install(monkeypatch,process)
    with pytest.raises(ValidationError,match="^WORKER_CLEANUP_FAILED$") as caught:
        run(WorkerRunner())
    assert process.returncode is not None
    assert isinstance(caught.value.cleanup_error,(OSError,subprocess.TimeoutExpired))
