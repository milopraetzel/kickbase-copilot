"""Planungs-Dispo als Tausch-Finanzierung (Emil 21.–23.09.2026), befristet bis Sa 31.10.2026.

Ursprünglich bis Mo 05.10.2026; am 05.10. auf Emils Wahl („Tausch wieder erlauben“)
verlängert — ohne die Frist kaufte die Engine nur noch aus 2,78 M echtem Geld und nahm
ihren geplanten Tausch zurück (Record plan.planning_overdraft, 2026-10-05).

Kickbase erlaubt ein Konto bis −Teamwert/3 (`trading/finance.overdraft_floor`). Emils
Wortlaut vom 22.09.2026: „volle 33 ohne Bindung aber clever mit plan. außer olise";
am 23.09. auf den Vorschlag „Dispo als Tausch-Finanzierung": „ok yallah weiter".

Seit 23.09. heißt das: Die Engine darf bis zur Frist einen BESSEREN Starter über das
vorhandene Geld hinaus kaufen, wenn im selben Plan der bedingte Sofortverkauf des
schwächeren Spielers steht, den er ersetzt (Tausch-Paar: Gebot + wartender Verkauf mit
`after_squad_size`/`swap_for`). Netto muss aus echtem Geld gedeckt sein:
`Kaufpreis − Marktwert_verkauft × 0,90 ≤ gedecktes Planungsgeld`; der Dispo überbrückt
nur die Zeit zwischen Zuschlag und Verkauf, und die Glättung IST der gekoppelte
Verkauf. Offene Elf-Plätze werden weiter aus echtem Geld gefüllt (kein Dispo für reine
Lückenfüllung). Geschützte Spieler (Olise) sind nie Verkaufsseite eines Tauschs.

Eigenes kleines Modul, weil `cli/plan.py`, `decision/purchase_review.py` und
`worker/outcome_guard.py` dieselbe Frist und Rechnung brauchen und der Wächter nicht aus
`cli/` importieren soll (Importzyklus über `agent/executor` und `worker/kickoff_guard`).
Spec: docs/superpowers/specs/2026-09-22-planungs-dispo.md (§6).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from kickbase_agent.trading.finance import overdraft_floor

#: Ende des Planungs-Dispos: Di 01.06.2027 00:00 Berlin (Saisonende 2026/27;
#: verlängert am 20.10.2026 nach Profi-Audit, damit der Tausch-Dispo nicht am 01.11.
#: mitten in der Saison abreißt).
PLANNING_OVERDRAFT_UNTIL = datetime(2027, 6, 1, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
#: Anrechnung des gekoppelten Sofortverkaufs in Prozent des Marktwerts (95 % = 5 % Puffer
#: gegen ein 22:00-Uhr-Marktwert-Update zwischen Gebot und Zuschlag bei Fallern; 06.11.2026).
SWAP_SALE_CREDIT_PERCENT = 95
#: Unter-der-Woche-Upgrade-Brücke (Hebel F, 19./20.10.2026; am 06.11.2026 auf 4,5 M € bei
#: >= 48 h vor Anpfiff kalibriert): Erlaubt früh in der Woche (Sa–Mi, >= 48 h vor Frist)
#: beim gekoppelten Tausch eines Nicht-Starters oder fallenden Spielers bis zu 4,5 M €
#: Netto-Überhang über dem freien Konto innerhalb der 33-%-Grenze. Ab Donnerstag (< 48 h)
#: ist die ungedeckte Brücke 0 €, damit kein spätes Freitags-Defizit entsteht.
MIDWEEK_SWAP_BRIDGE_EUR = 4_500_000
MIDWEEK_SWAP_BRIDGE_MIN_LEAD = timedelta(hours=48)


def _aware(now: datetime) -> datetime:
    return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


def overdraft_active(now: datetime, *, until: datetime = PLANNING_OVERDRAFT_UNTIL) -> bool:
    """Gilt der Planungs-Dispo noch? Ein naives `now` gilt als UTC."""
    return _aware(now) < until


def planning_overdraft(
    now: datetime,
    team_value: int,
    cash_frei: int,
    *,
    until: datetime = PLANNING_OVERDRAFT_UNTIL,
) -> int:
    """Rahmen, um den ein Tausch-Kauf über Konto − Gebote hinausgehen darf.

    0 ab `until`; sonst der Abstand zur Kickbase-Kaufkraftgrenze (rund Teamwert/3, ein
    Kontominus wird wie bei Kickbase gegen den Teamwert genettet). Der Rahmen ist nur
    die 33-%-Obergrenze; die Netto-Deckung jedes Tauschs prüft der Aufrufer.
    """
    if not overdraft_active(now, until=until):
        return 0
    return max(0, -overdraft_floor(int(team_value), int(cash_frei)))


def swap_sale_proceeds(market_value: int) -> int:
    """Angerechneter Erlös des gekoppelten Sofortverkaufs: Marktwert × 0,90, abgerundet."""
    return max(0, int(market_value)) * SWAP_SALE_CREDIT_PERCENT // 100


def midweek_swap_bridge(
    now: datetime,
    deadline: datetime | None,
    *,
    sold_is_weak_or_falling: bool = True,
    until: datetime = PLANNING_OVERDRAFT_UNTIL,
) -> int:
    """Zusätzlicher Netto-Tauschrahmen unter der Woche (>= 50 h vor Anpfiff-Frist)."""
    if (
        not sold_is_weak_or_falling
        or deadline is None
        or not overdraft_active(now, until=until)
    ):
        return 0
    if _aware(deadline) - _aware(now) < MIDWEEK_SWAP_BRIDGE_MIN_LEAD:
        return 0
    return MIDWEEK_SWAP_BRIDGE_EUR
