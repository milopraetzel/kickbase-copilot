"""Öffentliche Webprobe im temporären Profil; keine Modell-, Sitzungs- oder Spielaufrufe."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

root = Path(__file__).resolve().parent
plugin_root = root / ("plugin" if (root / "plugin").exists() else "plugins")
repo = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(os.environ.get("HERMES_SOURCE_DIR", root.parent / "hermes-heartbeat" / "repo"))
)
sys.path.insert(0, str(repo))
with tempfile.TemporaryDirectory(prefix="hermes-direct-smoke-") as temp:
    profile = Path(temp)
    os.environ["HERMES_HOME"] = temp
    patch = json.loads((root / "profile-web.patch.json").read_text())
    config = {
        "web": patch["web"],
        "plugins": {
            "enabled": patch["plugins_enabled_add"],
            "disabled": patch["plugins_disabled_add"],
        },
    }
    shutil.copytree(plugin_root / "web_direct_http", profile / "plugins" / "web" / "direct_http")
    (profile / "config.yaml").write_text(yaml.safe_dump(config))
    from tools.web_tools import (
        _get_extract_backend,
        _get_search_backend,
        web_extract_tool,
        web_search_tool,
    )

    print(
        json.dumps(
            {
                "at": datetime.now(UTC).isoformat(),
                "search": _get_search_backend(),
                "extract": _get_extract_backend(),
            }
        ),
        flush=True,
    )
    searched = json.loads(
        web_search_tool("site:ligainsider.de Kacper Potulski Mainz September 2026", limit=3)
    )
    print(
        json.dumps(
            {
                "search_success": searched.get("success"),
                "error": searched.get("error"),
                "hits": [
                    {"title": x.get("title"), "domain": urlsplit(x.get("url", "")).hostname}
                    for x in searched.get("data", {}).get("web", [])
                ],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    import importlib
    import importlib.metadata

    from tools.environments.local import _sanitize_subprocess_env

    versions = {
        name: {
            "version": importlib.metadata.version(name),
            "file": importlib.import_module(name).__file__,
        }
        for name in ("ddgs", "trafilatura")
    }
    child_script = (
        "import json,ddgs,trafilatura,importlib.metadata as m;"
        "print(json.dumps({n:{'version':m.version(n),'file':mod.__file__} "
        "for n,mod in [('ddgs',ddgs),('trafilatura',trafilatura)]}))"
    )
    child = subprocess.run(
        [sys.executable, "-c", child_script],
        env=_sanitize_subprocess_env(dict(os.environ)),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    print(
        json.dumps(
            {"dependencies_parent": versions, "dependencies_child": json.loads(child.stdout)},
            ensure_ascii=False,
        ),
        flush=True,
    )
    urls = [
        "https://www.ligainsider.de/angelo-stiller_20766/vfb-bangt-um-stiller-diagnose-bis-montag-418050/",
        "https://www.kicker.de/offiziell-gladbach-und-trainer-polanski-gehen-getrennte-wege-1180954/artikel",
    ]
    extracted = json.loads(asyncio.run(web_extract_tool(urls)))
    print(
        json.dumps(
            {
                "articles": [
                    {
                        "url": x.get("url"),
                        "title": x.get("title"),
                        "chars": len(x.get("content", "")),
                        "error": x.get("error"),
                    }
                    for x in extracted.get("results", [])
                ]
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
