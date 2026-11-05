"""T-120-Gebots-Freeze — hartes Schutzfenster vor der Spieltags-Deadline.

Letzter Codex-P0 vor SP1 (externes Review v0.4.0, 23.08.): Der T-120-Storno-
Wächter (worker/kickoff_guard.run_bid_cancel_guard) streicht 2 h vor dem ersten
Anpfiff alle Gebote, deren Zuschlag das Konto sprengen könnte — aber der
Intent-Executor (*/5 min), Auto-Trading und manuelle MCP-Gebote konnten danach
NEUE Gebote platzieren und den Schutz konterkarieren. Dieses Modul liefert die
reine Fenster-Logik; durchgesetzt wird sie im Preflight des ActionExecutors
(einzige Schreibstelle → schützt ALLE Gebots-Pfade gleichzeitig).

Ein hartes Fenster relativ zur Spieltags-Deadline (= frühester Anpfiff des nächsten
Spieltags, worker/kickoff_guard.matchday_deadline — EINE Deadline je Spieltag,
nie je Einzelspiel; nach dem Anpfiff zählt automatisch der Folgespieltag):

- T-120 min … Anpfiff: **FREEZE** — kein neues Gebot, ohne Ausnahme. Der
  Storno-Wächter hat aufgeräumt („Keine neuen Gebote bis Anpfiff!"); jedes
  neue Gebot öffnete die Konto-Rechnung wieder, und ein Gegen-Storno
  verbrennt den Offer-Slot des Listings (Otávio-Lehre).
Vor T-120 gelten die normale 33-%-Kaufkraft, die Pfand-Summenprüfung und der
Einlöser. Der frühere T-48h-Exposure-Guard beruhte auf der überholten Annahme,
Teamwert sei nur über ein rund 24-stündiges Listing liquide. Sofortverkauf ist
live in Sekunden belegt; der frühe Guard blockierte deshalb genau das sichere
Muster „Ersatz kaufen, dann Verlierer verkaufen" und widersprach der Regel, dass
das Konto erst zum tatsächlichen Anpfiff nicht negativ sein darf.

Verkäufe/Listings sind nie betroffen (sie verbessern das Konto), ebenso wenig
der Storno-Wächter selbst (action_type cancel_bid).

Decision-Record: knowledge/parameter_history.json (24.08.2026).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

#: Ab so vielen Minuten vor der Spieltags-Deadline sind neue Gebote eingefroren.
#: Deckungsgleich mit dem T-120-Storno-Wächter: was der Wächter aufräumt, darf
#: kein anderer Pfad danach wieder anrichten.
BID_FREEZE_MINUTES = 120

#: Kompatibilitätswert für Planner und Kader-Floor: Beide schalten jetzt am
#: selben T-120-Sicherheitsfenster um. Kein vorzeitiger 48-h-Handelsstopp mehr.
EXPOSURE_GUARD_HOURS = BID_FREEZE_MINUTES // 60

#: Konto-Puffer zum Anpfiff — identisch mit dem Puffer der Wächter-Kaskade
#: (worker/kickoff_guard nutzt diesen Wert als PURGE_MIN_BALANCE): das
#: Exposure-Gate lässt nichts durch, was der T-120-Storno wieder streichen müsste.
KICKOFF_MIN_BALANCE = 10_000

_BERLIN = ZoneInfo("Europe/Berlin")


def _fmt_berlin(dt: datetime) -> str:
    return dt.astimezone(_BERLIN).strftime("%a %d.%m. %H:%M")


def bid_freeze_reason(
    now: datetime,
    deadline: datetime | None,
    *,
    amount: int,
    budget_after_bids: int | None,
) -> str | None:
    """Refusal-Grund fürs Gebot im Deadline-Fenster — None, wenn kein Einwand.

    `deadline` ist die nächste Spieltags-Deadline (aware, UTC) oder None (keine
    bekannten Termine, z. B. Saisonpause → kein Fenster aktiv). Eine Deadline in
    der Vergangenheit zählt nicht (matchday_deadline liefert nur zukünftige;
    defensive Doppelprüfung hier). `budget_after_bids` ist Kickbase' `pbaa`
    (Konto nach Annahme ALLER offenen Gebote) — genau der Worst-Case-Kontostand.
    """
    if deadline is None:
        return None
    remaining = deadline - now
    if remaining <= timedelta(0):
        return None
    if remaining <= timedelta(minutes=BID_FREEZE_MINUTES):
        return (
            f"T-120-Freeze: erster Anpfiff {_fmt_berlin(deadline)} ist keine "
            f"{BID_FREEZE_MINUTES} min mehr entfernt — keine neuen Gebote bis zur "
            "Spieltags-Deadline (der Gebots-Storno-Wächter hat das Konto gesichert; "
            "ein neues Gebot würde die Rechnung wieder öffnen)."
        )
    return None
