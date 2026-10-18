"""Own and clean the complete server process trees, including Windows launchers."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import psutil

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
for port in (8011,5173):
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',port)) == 0:
            raise SystemExit(f'Port {port} occupied; preserving existing service.')
logs = Path('.cache/acceptance');logs.mkdir(parents=True,exist_ok=True)
children = []
try:
    for port,command,name in [(8011,[sys.executable,'-B','scripts/isolated_backend.py'],'backend'),
                              (5173,['node','node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173','--strictPort'],'vite')]:
        output=(logs/(name+'.log')).open('w',encoding='utf-8')
        child=subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        children.append((child,output))
        deadline=time.monotonic()+45
        while True:
            if child.poll() is not None:raise RuntimeError(f'{name} exited; see {output.name}')
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',port))==0:break
            if time.monotonic()>deadline:raise RuntimeError(f'{name} startup timed out')
            time.sleep(.1)
    code=subprocess.call([sys.executable,'-B','scripts/browser_acceptance.py'])
finally:
    for child,output in reversed(children):
        if child.poll() is None:
            try: descendants=psutil.Process(child.pid).children(recursive=True)
            except psutil.NoSuchProcess: descendants=[]
            for process in reversed(descendants):
                try:process.terminate()
                except psutil.NoSuchProcess:pass
            child.terminate();child.wait(timeout=10)
            _,alive=psutil.wait_procs(descendants,timeout=10)
            for process in alive:
                try:process.kill()
                except psutil.NoSuchProcess:pass
        output.close()
sys.exit(code)
