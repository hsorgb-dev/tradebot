"""Tests run against the modules exactly as the notebooks write them."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import notebook_modules  # noqa: E402


def pytest_configure(config):
    # Module names differ per version (v12/v13), so all can share sys.path;
    # the hash-pinned v02/v03 modules are identical in every notebook.
    for number, notebook in enumerate(notebook_modules.NOTEBOOKS):
        target = Path(config.rootpath) / '.pytest_cache' / f'notebook_modules_{number}'
        notebook_modules.extract(notebook, target)
        sys.path.insert(0, str(target))
