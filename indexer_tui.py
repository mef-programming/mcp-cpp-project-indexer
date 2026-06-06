from __future__ import annotations

from pathlib import Path
import runpy
import sys


_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
for _path in (_SRC / "indexer", _SRC / "server", _SRC / "ui"):
    sys.path.insert(0, str(_path))
if not any(arg == "--indexer-root" or arg.startswith("--indexer-root=") for arg in sys.argv[1:]):
    sys.argv.extend(["--indexer-root", str(_ROOT)])
runpy.run_path(str(_SRC / "ui" / "indexer_tui.py"), run_name="__main__")
