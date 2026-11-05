"""Belegte Ligaauktionen trainieren; Kandidaten niemals still aktivieren."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from kickbase_agent.trading.competition import (
    DEFAULT_REGISTRY_DIR,
    BidObservation,
    load_competition,
    promote_competition,
    rollback_competition,
    train_competition,
)
from kickbase_agent.trading.competition import (
    observations_from_actions as observations_from_actions,
)

app = typer.Typer(help="Liga-Wettbewerbsmodell und belegte Auktionsdaten.")


def observations_from_strategy(
    strategy: dict,
    league_id: str,
    user_id: str,
) -> list[BidObservation]:
    observations = []
    for trade in strategy.get("trades", []):
        if (
            trade.get("kind") != "kb_purchase"
            or trade.get("update_boundary")
            or trade.get("synthetic_id")
            or not trade.get("market_value")
            or not trade.get("buyer_id")
        ):
            continue
        try:
            observations.append(
                BidObservation(
                    event_id=str(trade["feed_id"]),
                    league_id=league_id,
                    at=datetime.fromisoformat(trade["at"]),
                    market_value=trade["market_value"],
                    amount=trade["amount"],
                    position=trade["position"],
                    kind="upper" if str(trade["buyer_id"]) == user_id else "exact",
                    source="archived_quote",
                    prospective=False,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return observations


@app.command("train")
def train_command(
    strategy: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    league_id: Annotated[str, typer.Option()],
    user_id: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option()],
) -> None:
    """Archivierte Auktionspreise lernen; Freigabe braucht prospektive eigene Ergebnisse."""
    import json

    observations = observations_from_strategy(json.loads(strategy.read_text()), league_id, user_id)
    report = train_competition(
        observations, league_id=league_id, listing_type="kickbase", as_of=datetime.now(UTC)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    typer.echo(f"{report['rows']} Ereignisse; {report['reason']}\n{output}")


@app.command("train-ledger")
def train_ledger(
    league_id: Annotated[str, typer.Option()],
    listing_type: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option()],
) -> None:
    """Eigene, nach Tatzeitdaten bestätigte Gebote neu auswerten; kein Aktivieren."""
    import json

    from sqlalchemy import select

    from kickbase_agent.db.models import TransferAction
    from kickbase_agent.db.session import SessionLocal

    if listing_type not in {"kickbase", "manager"}:
        raise typer.BadParameter("Listingart muss kickbase oder manager sein.")
    with SessionLocal() as session:
        observations = observations_from_actions(
            session.scalars(
                select(TransferAction).where(
                    TransferAction.league_id == league_id,
                    TransferAction.action_type == "bid",
                )
            ).all()
        )
    report = train_competition(
        observations, league_id=league_id, listing_type=listing_type, as_of=datetime.now(UTC)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    typer.echo(f"{report['rows']} bestätigte Eigenfälle; {report['reason']}\n{output}")


Registry = Annotated[Path, typer.Option("--registry-dir")]


def _show(value: dict) -> None:
    import json

    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


@app.command("promote")
def promote_command(
    candidate: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    league_id: Annotated[str, typer.Option()],
    listing_type: Annotated[str, typer.Option()],
    registry_dir: Registry = DEFAULT_REGISTRY_DIR,
) -> None:
    """Nur einen zum Kontext passenden, unveränderten geprüften Kandidaten freigeben."""
    try:
        _show(
            promote_competition(
                candidate, league_id=league_id, listing_type=listing_type, registry_dir=registry_dir
            )
        )
    except (ValueError, KeyError, OSError, TypeError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("rollback")
def rollback_command(
    league_id: Annotated[str, typer.Option()],
    listing_type: Annotated[str, typer.Option()],
    registry_dir: Registry = DEFAULT_REGISTRY_DIR,
    baseline: Annotated[bool, typer.Option("--baseline")] = False,
) -> None:
    """Vorherige geprüfte Version oder mit --baseline unbekannte Zuschlagschance nutzen."""
    try:
        _show(
            rollback_competition(
                league_id=league_id,
                listing_type=listing_type,
                registry_dir=registry_dir,
                to_baseline=baseline,
            )
        )
    except (ValueError, KeyError, OSError, TypeError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("status")
def status_command(
    league_id: Annotated[str, typer.Option()],
    listing_type: Annotated[str, typer.Option()],
    registry_dir: Registry = DEFAULT_REGISTRY_DIR,
) -> None:
    """Nur lesend: tatsächlich ladbare Version und Registrierung nennen."""
    artifact = load_competition(league_id, listing_type, registry_dir=registry_dir)
    _show(
        {
            "league_id": league_id,
            "listing_type": listing_type,
            "registry_dir": str(registry_dir),
            "active_loaded": artifact is not None,
            "version": artifact["version"] if artifact else "unknown",
        }
    )
