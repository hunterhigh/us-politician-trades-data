from __future__ import annotations

import compileall
import subprocess
import sys

if not compileall.compile_dir("src", quiet=1):
    raise SystemExit(1)
raise SystemExit(subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]))
