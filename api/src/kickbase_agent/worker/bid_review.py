"""Gebots-Nachschau: Erwartung gegen Wirklichkeit (Emils Auftrag 25.08.).

Jedes Gebot trägt seit heute eine Zuschlag-Erwartung im Audit (`payload.expectation`,
gesetzt vom Executor). Dieser Job bucht das Ergebnis dazu — Spieler im Kader =
gewonnen, Gebot verschwunden und kein Spieler = verloren — und meldet sich, wenn die
Schätzung systematisch danebenliegt.

Warum ein eigener Job und nicht im Reconcile: Der Reconcile kehrt bei leerer
Kandidatenliste sofort zurück („quiet"); die Nachschau muss aber laufen, gerade wenn
sonst nichts unklar ist. Beide lesen Kickbase nur.

Bewusst schweigsam: Bei weniger als fünf abgeschlossenen Geboten sagt er nichts —
eine Trefferquote aus vier Fällen wäre Scheingenauigkeit.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from kickbase_agent.db.session import SessionLocal
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.trading.expectation import (
    calibration,
    format_calibration,
    settle_bid_expectations,
)

log = logging.getLogger(__name__)


async def run_bid_review(channel=None) -> str:
    """Ergebnisse buchen + Kalibrierung prüfen. Rückgabe für Logs/Tests."""
    now = datetime.now(UTC)
    client, auth = await make_kickbase_client()
    league_id = auth.league_id
    if league_id is None:
        return "no_league"

    squad = await client.get_squad(league_id)
    squad_pids = {str(sp.player.id) for sp in squad}
    squad_names = {
        str(sp.player.id): f"{sp.player.first_name} {sp.player.last_name}".strip()
        for sp in squad
    }
    open_bid_pids = {
        str(b.get("player_id"))
        for b in await client.get_own_bids(league_id, auth.user_id or "")
    }

    receipts, my_name = [], ""
    try:
        ranking = await client.get_ranking(league_id)
        my_name = next((r.name for r in ranking if r.user_id == auth.user_id), "")
        receipts = await client.get_activity_feed(league_id, max_items=1000)
    except Exception:
        log.warning("Transferbelege fehlen; verschwundene Gebote bleiben ungeklärt.")

    with SessionLocal() as session:
        settled = settle_bid_expectations(
            session, squad_pids=squad_pids, open_bid_pids=open_bid_pids, now=now,
            league_id=league_id, receipts=receipts, my_name=my_name,
        )
        report = calibration(session, now=now, days=30)

    for s in settled:
        log.info(
            "GEBOT-ERGEBNIS player=%s %s (Prognose %s)", s.player_id, s.result, s.p
        )

    # Zuschläge SOFORT melden (Emils Bitte 25.08.: „sag Bescheid, wenn die da
    # sind"). Vorher wurde das Ergebnis nur still gebucht — der Moment, auf den
    # Emil wartet, kam nirgends an.
    if settled and channel is not None:
        await channel.send_info(_format_results(settled, squad_names))

    # Meldungs-Umbau (Emil, 31.08.): Die 30-Tage-Kalibrierung wurde stündlich
    # wiederholt („Meine Gebots-Erwartung war zu optimistisch" — 37× in einer
    # Woche, identischer Inhalt). Sie erzählt nur dann etwas Neues, wenn sich
    # die Stichprobe geändert hat — also NUR noch im selben Lauf wie ein frisch
    # abgeschlossenes Gebot. Die Zuschlags-Sofortmeldung darüber bleibt.
    text = format_calibration(report)
    if text and settled and channel is not None:
        await channel.send_info(text)
    return f"settled={len(settled)} sample={report.total}"


def _format_results(settled: list, names: dict[str, str]) -> str:
    """Ergebnis-Meldung: gewonnene Zuschläge zuerst, dann verfallene Gebote."""
    won = [s for s in settled if s.result == "won"]
    lost = [s for s in settled if s.result == "lost"]
    lines: list[str] = []
    if won:
        lines.append("🏆 **Zuschlag" + ("" if len(won) == 1 else "e") + "!**")
        for s in won:
            lines.append(f"• **{names.get(s.player_id, s.player_id)}** ist im Kader")
    if lost:
        lines.append("❌ **Gebot" + ("" if len(lost) == 1 else "e") + " verfallen:**")
        for s in lost:
            lines.append(
                f"• {names.get(s.player_id, s.player_id)} — anderer Zuschlag bestätigt "
                + (f"(Erwartung {s.p:.0%})" if s.p is not None else "(Chance nicht kalibriert)")
            )
    return "\n".join(lines)
