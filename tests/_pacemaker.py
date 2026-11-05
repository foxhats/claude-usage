"""Helpers for tests that touch Claude Pace Maker (an optional, separate tool).

Some tests exercise the real pace-maker package (its UsageModel opens the
SQLite DB, enables WAL, reads fallback state, ...). Those can only run where
pace-maker is importable, so they are marked with `requires_pacemaker` and
skipped elsewhere instead of failing.

pace-maker is looked for the same way the app looks for it, plus the layout
the original author develops in:
  * ../claude-pace-maker/src  (a checkout next to this repo)
  * the path in ~/.claude-pace-maker/install_source (a normal install)
  * anything already on sys.path

To run them: git clone https://github.com/LightspeedDMS/claude-pace-maker
next to this repo.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _candidate_paths():
    paths = [Path(__file__).resolve().parent.parent.parent / "claude-pace-maker" / "src"]
    try:
        from claude_usage.code_mode.pacemaker_integration import PaceMakerReader

        found = PaceMakerReader()._get_pacemaker_src_path()
        if found:
            paths.append(Path(found))
    except Exception:
        pass
    return [p for p in paths if p.is_dir()]


def _pacemaker_available():
    saved = list(sys.path)
    try:
        for p in _candidate_paths():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
        return importlib.util.find_spec("pacemaker.usage_model") is not None
    except (ImportError, ValueError):
        return False
    finally:
        sys.path[:] = saved


PACEMAKER_AVAILABLE = _pacemaker_available()

requires_pacemaker = pytest.mark.skipif(
    not PACEMAKER_AVAILABLE,
    reason="needs Claude Pace Maker installed (see tests/_pacemaker.py)",
)


def fake_pacemaker_modules():
    """sys.modules entries for a stand-in `pacemaker.usage_model` package.

    For unit tests that only patch `pacemaker.usage_model.UsageModel` and never
    use the real thing. Use with patch.dict(sys.modules, fake_pacemaker_modules()).
    """
    pkg = types.ModuleType("pacemaker")
    pkg.__path__ = []  # mark as a package
    usage_model = types.ModuleType("pacemaker.usage_model")

    class UsageModel:  # replaced by patch() in the tests
        def __init__(self, *args, **kwargs):
            raise ImportError("stand-in pacemaker.usage_model; patch UsageModel")

    usage_model.UsageModel = UsageModel
    pkg.usage_model = usage_model
    return {"pacemaker": pkg, "pacemaker.usage_model": usage_model}
