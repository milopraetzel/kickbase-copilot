"""Einfrier-Wächter: stündlich prüfen, ob der Agent sich selbst stilllegen muss.

Bewusst ein eigener, winziger Job statt eines Anhängsels: Die Auslöser (Tagesverlust,
unklare Writes) entstehen über den ganzen Tag verteilt, und eine Sicherung, die erst
am nächsten Morgen greift, ist keine.

Er liest nur die eigene DB — kein Kickbase-Zugriff, kein Write nach außen. Meldung
gibt es ausschließlich beim ERSTEN Einfrieren; ein bestehender Zustand raschelt
nicht stündlich weiter (die Meldung stünde sonst neben jeder anderen und niemand
läse sie noch).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from kickbase_agent.agent import freeze as fz
from kickbase_agent.db.session import SessionLocal

log = logging.getLogger(__name__)


async def run_freeze_watch(channel=None, now: datetime | None = None) -> str:
    """Auslöser prüfen. Rückgabe 'quiet' = alles normal (der Normalfall)."""
    stamp = now or datetime.now(UTC)
    with SessionLocal() as session:
        row = fz.evaluate_and_freeze(session, now=stamp)
        if row is None:
            return "quiet"
        text = fz.format_alarm(row)
        source = row.source
    log.warning("SELBST-EINGEFROREN (%s)", source)
    if channel is not None:
        await channel.send_info(text)
    return f"frozen:{source}"
