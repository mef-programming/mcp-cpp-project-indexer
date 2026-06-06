from __future__ import annotations

from pathlib import Path
import runpy
import sys


_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
for _path in (_SRC / "indexer", _SRC / "server", _SRC / "ui"):
    sys.path.insert(0, str(_path))
runpy.run_path(str(_SRC / "indexer" / "build_module_map.py"), run_name="__main__")
