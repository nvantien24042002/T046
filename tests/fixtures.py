"""Deterministic tiny fixtures; legacy byte buffers here are capped test data only."""
from __future__ import annotations

import ast
import hashlib
import io
from pathlib import Path
import types
import zipfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
XML = '''<annotations><image id="0" name="flat.png" width="64" height="64">
<tag label="scene_info"><attribute name="weather">unknown</attribute><attribute name="timeofday">night</attribute></tag>
<box label="car" xtl="8" ytl="8" xbr="40" ybr="40" occluded="0"><attribute name="color">red</attribute></box>
<box label="car" xtl="8" ytl="8" xbr="40" ybr="40" occluded="1"/>
<box label="person" xtl="30" ytl="30" xbr="20" ybr="40"/>
</image><image id="1" name="texture.png" width="64" height="64">
<tag label="weather" value="rain"/><box label="person" xtl="-1" ytl="2" xbr="20" ybr="30"/>
</image><image id="2" name="missing.png" width="64" height="64">
<box label="bus" xtl="8" ytl="8" xbr="40" ybr="40"/>
</image></annotations>'''


def make_zip(path: Path, *, image_only: bool = False, filler: int = 0) -> Path:
    rng = np.random.default_rng(20261016)
    arrays = {"flat.png": np.full((64, 64, 3), 100, dtype=np.uint8),
              "texture.png": rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, array in arrays.items():
            buffer = io.BytesIO()
            Image.fromarray(array).save(buffer, format="PNG")
            archive.writestr("images/" + name, buffer.getvalue())
        if not image_only:
            archive.writestr("annotations.xml", XML)
        if filler:
            archive.writestr("transport-test.bin", rng.bytes(filler))
    return path


def legacy_functions(filename: str = "app.py") -> types.ModuleType:
    """Execute ONLY imports/constants/function definitions, never app UI/import.

    Test-only baseline harness. Production must not depend on this AST adapter.
    """
    path = ROOT / filename
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [node for node in tree.body if isinstance(node, (
        ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef
    )) or (isinstance(node, ast.Assign) and all(
        isinstance(target, ast.Name) and target.id.isupper() for target in node.targets
    ))]
    module = types.ModuleType("legacy_baseline")
    # dataclass needs a registered module during execution.
    import sys
    sys.modules[module.__name__] = module
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), module.__dict__)
    module.st = types.SimpleNamespace(progress=lambda *a, **k: types.SimpleNamespace(
        progress=lambda *a, **k: None, empty=lambda: None))
    return module


def source_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
