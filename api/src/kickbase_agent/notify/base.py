"""Kanal-unabhängige Approval-Schnittstelle.

Der Worker erzeugt kanalneutrale ChannelMessages; eine konkrete Implementierung
(Discord, Telegram, Dashboard) stellt sie zu und meldet Button-Klicks zurück.
So bleibt die Entscheidungs-/Ausführungslogik vom Kanal entkoppelt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChannelMessage:
    title: str
    body: str
    # Bei Approval-Nachrichten: die transfer_actions-ID hinter den Buttons.
    action_id: int | None = None


class ApprovalChannel(Protocol):
    async def send_approval(self, message: ChannelMessage) -> None:
        """Vorschlag mit Annehmen/Ablehnen-Buttons zustellen."""
        ...

    async def send_info(
        self, text: str, *, kind: str | None = None, title: str | None = None
    ) -> None:
        """Reine Info/Report ohne Buttons.

        `kind`/`title` (optional, nur Keyword) kennzeichnen den Text für die
        Berichte-Ablage (notify/report_store: briefing | recherche | schatten |
        auto_trading | planner | executor | retro | bid_watch | info | sonstiges).
        Kanäle ohne Ablage dürfen die Parameter ignorieren; Aufrufer, die sie
        weglassen, bleiben abwärtskompatibel (kind wird dann aus dem Text geraten).
        """
        ...
