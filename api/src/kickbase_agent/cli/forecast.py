"""Lokale Prognosekandidaten trainieren, prüfen und ausdrücklich freigeben."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from kickbase_agent.intel.forecasting.dataset import utc
from kickbase_agent.intel.forecasting.registry import (
    DEFAULT_REGISTRY_DIR,
    load_active,
    promote,
    read_candidate,
    rollback,
)
from kickbase_agent.intel.forecasting.training import evaluate_candidate, train_candidate

app = typer.Typer(help="Numerische Prognosen und geprüfte Modellfreigaben", no_args_is_help=True)
Source = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
Registry = Annotated[Path, typer.Option("--registry-dir")]


def _show(value: dict[str, Any]) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


@app.command("train")
def train(
    source: Source,
    registry_dir: Registry = DEFAULT_REGISTRY_DIR,
    as_of: Annotated[str, typer.Option("--as-of", help="UTC-Stichtag; leer = jetzt")] = "",
) -> None:
    """Einen Kandidaten erzeugen; aktiviert niemals selbst ein Modell."""
    cutoff = utc(as_of) if as_of else datetime.now(UTC)
    if cutoff is None:
        raise typer.BadParameter("--as-of braucht eine ISO-Zeit mit Zeitzone.")
    try:
        artifact = train_candidate(source, registry_dir=registry_dir, as_of=cutoff)
    except (ValueError, KeyError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _show({"model_version": artifact["model_version"], "audit": artifact["audit"],
           "split": artifact["split"], "evaluation": artifact["evaluation"]})


@app.command("evaluate")
def evaluate(
    version: str,
    source: Source,
    registry_dir: Registry = DEFAULT_REGISTRY_DIR,
) -> None:
    """Eingefrorene Gewichte auf denselben, fingerprint-geprüften Zeitgruppen messen."""
    try:
        artifact = read_candidate(version, registry_dir=registry_dir)
        _show(evaluate_candidate(source, artifact))
    except (ValueError, KeyError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("promote")
def promote_command(
    version: str, registry_dir: Registry = DEFAULT_REGISTRY_DIR,
) -> None:
    """Nur einen unveränderten Kandidaten mit bestandenem Evaluationsvertrag aktivieren."""
    try:
        _show(promote(version, registry_dir=registry_dir))
    except (ValueError, KeyError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("rollback")
def rollback_command(
    registry_dir: Registry = DEFAULT_REGISTRY_DIR,
    baseline: Annotated[bool, typer.Option("--baseline")] = False,
) -> None:
    """Zur vorherigen qualifizierten Version oder ausdrücklich zur Basis zurückkehren."""
    try:
        _show(rollback(registry_dir=registry_dir, to_baseline=baseline))
    except (ValueError, KeyError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("status")
def status(registry_dir: Registry = DEFAULT_REGISTRY_DIR) -> None:
    artifact = load_active(registry_dir)
    _show({"registry_dir": str(registry_dir), "active_loaded": artifact is not None,
           "model_version": artifact["model_version"] if artifact else "baseline-recency-v1"})
