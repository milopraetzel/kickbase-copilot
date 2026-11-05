"""Direkter Artikelabruf im einzelnen Profil über das bestehende web_extract-Tool."""

import os
import sys
from pathlib import Path

# Optionale Abhängigkeiten bleiben im Profil. DDGS startet einen Unterprozess;
# dieser braucht denselben Pfad, ohne die gemeinsame Hermes-Installation zu ändern.
_vendor = Path(__file__).with_name("_vendor")
if _vendor.is_dir():
    _path = str(_vendor)
    if _path not in sys.path:
        sys.path.append(_path)
    _pythonpath = [x for x in os.environ.get("PYTHONPATH", "").split(os.pathsep) if x]
    if _path not in _pythonpath:
        os.environ["PYTHONPATH"] = os.pathsep.join([*_pythonpath, _path])


def register(ctx):
    from .provider import DirectHTTPProvider

    ctx.register_web_search_provider(DirectHTTPProvider())
