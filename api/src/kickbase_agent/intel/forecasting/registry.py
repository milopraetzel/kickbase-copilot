"""Versionierte Kandidaten, explizite Freigabe und atomarer Rückfall zur Basis."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from kickbase_agent.intel.forecasting.features import FEATURE_NAMES, FEATURE_VERSION
from kickbase_agent.intel.forecasting.training import artifact_version, canonical, promotion_gate

DEFAULT_REGISTRY_DIR = Path("data/models/forecast")


def artifact_valid(artifact: Any, *, require_qualified: bool = True) -> bool:
    try:
        if (artifact["schema_version"] != 1 or artifact["feature_version"] != FEATURE_VERSION
                or artifact["feature_names"] != FEATURE_NAMES
                or artifact["model_version"] != artifact_version(artifact)
                or artifact["min_history"] != 3
                or artifact["label_scope"] != "observed_appearances_only"):
            return False
        for target in ("points", "minutes"):
            model = artifact["models"][target]
            for key in ("coefficients", "center", "scale"):
                if len(model[key]) != len(FEATURE_NAMES):
                    return False
                if not all(type(v) in (int, float) and math.isfinite(v) for v in model[key]):
                    return False
            if any(v <= 0 for v in model["scale"]) or not math.isfinite(model["intercept"]):
                return False
            lo, hi = model["output_bounds"]
            if not math.isfinite(lo) or not math.isfinite(hi) or lo > hi:
                return False
        gate = promotion_gate(artifact["evaluation"])
        return gate == artifact["evaluation"]["promotion_gate"] and (
            not require_qualified or gate["passed"]
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def read_candidate(
    version: str, *, registry_dir: str | Path = DEFAULT_REGISTRY_DIR,
) -> dict[str, Any]:
    if not re.fullmatch(r"ridge-[a-f0-9]{20}", version):
        raise ValueError("Ungültige Modellversion.")
    artifact = json.loads((Path(registry_dir) / "candidates" / f"{version}.json").read_text())
    if (not artifact_valid(artifact, require_qualified=False)
            or artifact["model_version"] != version):
        raise ValueError("Modellartefakt beschädigt oder nicht kompatibel.")
    return artifact


def _resolve(pointer: dict[str, Any], directory: Path) -> dict[str, Any] | None:
    artifact = read_candidate(pointer["model_version"], registry_dir=directory)
    if hashlib.sha256(canonical(artifact)).hexdigest() != pointer["sha256"]:
        return None
    return artifact if artifact_valid(artifact) else None


def load_active(registry_dir: str | Path = DEFAULT_REGISTRY_DIR) -> dict[str, Any] | None:
    """Fehlend, beschädigt oder unqualifiziert führt immer zur dokumentierten Basis."""
    directory = Path(registry_dir)
    try:
        return _resolve(json.loads((directory / "active.json").read_text()), directory)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _atomic(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(canonical(content))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def promote(version: str, *, registry_dir: str | Path = DEFAULT_REGISTRY_DIR) -> dict[str, Any]:
    directory = Path(registry_dir)
    artifact = read_candidate(version, registry_dir=directory)
    if not artifact_valid(artifact):
        reasons = artifact["evaluation"]["promotion_gate"]["reasons"]
        raise ValueError(f"Freigabe verweigert: {reasons}")
    active_path = directory / "active.json"
    if active_path.exists():
        previous = json.loads(active_path.read_text())
        if _resolve(previous, directory) is None:
            raise ValueError("Aktive Referenz beschädigt; zuerst explizit zur Basis zurückrollen.")
        _atomic(directory / "previous.json", previous)
    pointer = {
        "model_version": version, "sha256": hashlib.sha256(canonical(artifact)).hexdigest(),
    }
    _atomic(active_path, pointer)
    return pointer


def rollback(
    *, registry_dir: str | Path = DEFAULT_REGISTRY_DIR, to_baseline: bool = False,
) -> dict[str, Any]:
    directory = Path(registry_dir)
    active = directory / "active.json"
    if to_baseline:
        active.unlink(missing_ok=True)
        return {"model_version": "baseline-recency-v1"}
    previous = json.loads((directory / "previous.json").read_text())
    if _resolve(previous, directory) is None:
        raise ValueError("Vorheriges Modell nicht gültig; --baseline verwenden.")
    _atomic(active, previous)
    return previous
