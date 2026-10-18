"""Test-only backend runner: fresh temporary storage, no production credentials."""
import os
import secrets
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(os.environ['N205B_BACKEND_SOURCE'])))
os.environ['PYTHONPATH'] = os.environ['N205B_BACKEND_SOURCE']
import uvicorn
from backend.integrated_api import create_integrated_app
from storage.config import UploadConfig

with tempfile.TemporaryDirectory(prefix='n205b-fe-backend-') as folder:
    config = UploadConfig(Path(folder) / 'private', secrets.token_urlsafe(32),
                          chunk_bytes=65536, min_free_bytes=0, max_bytes=10*1024**2, quota_bytes=100*1024**2)
    app = create_integrated_app(config, {'acceptance': secrets.token_urlsafe(32)})
    uvicorn.run(app, host='127.0.0.1', port=8011, log_level='warning', access_log=False)
