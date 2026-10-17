"""Test-only child process: reports indexing and waits to be terminated."""
import sys
import time


if __name__ == "__main__":
    sys.stdin.buffer.readline(65537)
    print('{"phase":"indexing"}', flush=True)
    while True:
        time.sleep(0.01)
