"""Compile the backend, reconstruct the demo, and replay the golden reports."""
import compileall
import os
from pathlib import Path
import subprocess
import sys
import unittest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


def main():
    if not compileall.compile_dir(str(BACKEND / 'src'), quiet=1):
        return 1
    subprocess.run([sys.executable, '-m', 'unison_snapshot', 'demo'], cwd=REPO,
                   env={**os.environ, 'PYTHONPATH': str(BACKEND / 'src')}, check=True)
    sys.path.insert(0, str(BACKEND / 'src'))
    suite = unittest.defaultTestLoader.discover(str(BACKEND / 'tests/golden'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
