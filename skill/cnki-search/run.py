"""Compatibility entrypoint for the cnki-search CLI."""

from __future__ import annotations

import importlib
import os
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.join(SKILL_DIR, "scripts")
SRC_DIR = os.path.join(SKILL_DIR, "src")

for path in (SCRIPT_DIR, SRC_DIR, SKILL_DIR, os.path.dirname(SKILL_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

_cli = importlib.import_module("cli")

if __name__ == "__main__":
    raise SystemExit(_cli.main())

sys.modules[__name__] = _cli
