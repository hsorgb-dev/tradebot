"""Tests run against the modules exactly as the notebook writes them."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import notebook_modules  # noqa: E402


def pytest_configure(config):
    target = Path(config.rootpath) / '.pytest_cache' / 'notebook_modules'
    notebook_modules.extract(target)
    sys.path.insert(0, str(target))
