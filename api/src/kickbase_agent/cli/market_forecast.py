"""Nur lokale Marktwertkandidaten trainieren; keine Aktivierung oder Handel."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from kickbase_agent.intel.forecasting.market_value import train_market_candidate

app = typer.Typer(help="Marktwertmodelle für 1/3/7 Tage zeitlich prüfen", no_args_is_help=True)


@app.command("train")
def train(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    as_of: Annotated[str, typer.Option("--as-of")] = "",
) -> None:
    """Datierte Tageshistorie trainieren und mit Flat/letztem Trend vergleichen."""
    try:
        cutoff = (datetime.fromisoformat(as_of.replace("Z", "+00:00"))
                  if as_of else datetime.now(UTC))
        result = train_market_candidate(source, as_of=cutoff, output_dir=output_dir)
    except (ValueError, OSError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Kandidat: {result['candidate_path']}\nPrüfbericht: {result['report_path']}")
