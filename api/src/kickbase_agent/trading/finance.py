"""Overdraft-Rechnung nach der offiziellen Kickbase-33%-Regel.

Quelle (help.kickbase.com, "Wie weit darf ich ins Minus? Die 33% Regel."):
"Du kannst maximal 33% deines Mannschaftswerts plus den Betrag, der sich auf
deinem Konto befindet, ins Minus gehen." Das offizielle Rechenbeispiel nettet
einen negativen Kontostand gegen den Teamwert: Teamwert 100 M, Konto −10 M →
bereinigter Teamwert 90 M → 33 % davon = 30 M → Konto darf nicht unter −30 M.

Zwei Fakten, die die frühere Rechnung hier falsch machte:

1. Der Teamwert ist die Summe der (täglich von Kickbase gesetzten) Marktwerte.
   Beim Kauf steigt er nur um den MARKTWERT des Spielers, nie um den Kaufpreis —
   ein Overpay verpufft ersatzlos.
2. Beim Bieten prüft die App den Kontostand "nach Annahme aller Gebote" gegen
   das Limit aus dem AKTUELLEN Zustand — der einlaufende Spieler hebt das
   Minus-Limit beim Bieten NICHT an (erst der Zuschlag, und nur um den MW).

Zahlenbeispiel (Kauf für 15 M bei Marktwert 10 M; Konto 20 M, Teamwert 90 M):
- Limit beim Bieten: −(90 M + 0) / 3 = −30 M. Neuer Kontostand 20 − 15 = 5 M,
  bleibt über −30 M → Kauf erlaubt.
- Nach dem Zuschlag: Teamwert 90 + 10 = 100 M (nur der Marktwert zählt),
  Konto 5 M. Die 5 M Overpay sind weg (Vermögen sinkt von 110 M auf 105 M).
- Die frühere Formel hätte den Teamwert mit 105 M (= +Kaufpreis) angesetzt und
  das Minus-Limit dadurch zu großzügig mitwachsen lassen.
"""

from __future__ import annotations

_OVERDRAFT_FRACTION = 3  # Minus bis zu 1/3 des bereinigten Teamwerts


def overdraft_floor(team_value: int, balance: int) -> int:
    """Tiefstmöglicher Kontostand (≤ 0) bei gegebenem Teamwert und Kontostand.

    Basis ist der bereinigte Teamwert: Teamwert plus negativer Kontostand
    (offizielles Beispiel: Teamwert 100 M, Konto −10 M → 90 M → Limit −30 M).
    Ein positiver Kontostand hebt das Limit nicht zusätzlich an.
    """
    adjusted = max(0, team_value + min(balance, 0))
    return -(adjusted // _OVERDRAFT_FRACTION)


def can_afford(budget: int, team_value: int, price: int, open_bids: int = 0) -> bool:
    """Bleibt das Konto "nach Annahme aller Gebote" über dem Minus-Limit?

    ``open_bids`` = Summe bereits offener Gebote derselben Runde: Die App prüft
    alle offenen Gebote ZUSAMMEN gegen das Limit des aktuellen Zustands. Der
    gekaufte Spieler hebt das Limit erst nach dem Zuschlag — und dann nur um
    seinen Marktwert (siehe team_value_after_buy), nie um den Kaufpreis.
    """
    return budget - open_bids - price >= overdraft_floor(team_value, budget)


def max_single_bid(budget: int, team_value: int) -> int:
    """Höchstgebot für EINEN Spieler (ohne weitere offene Gebote).

    budget − X ≥ floor  ⟺  X ≤ budget − floor. Nie negativ: 0 bedeutet, dass
    kein Gebot möglich ist (Konto bereits am/unter dem Limit — erst verkaufen).
    """
    return max(0, budget - overdraft_floor(team_value, budget))


def team_value_after_buy(team_value: int, market_value: int) -> int:
    """Teamwert nach einem Zuschlag: +MARKTWERT des Spielers, nie +Kaufpreis.

    Beispiel: Kauf für 15 M bei Marktwert 10 M → Teamwert steigt um 10 M; die
    5 M Overpay senken nur das Konto. Für Planungen über mehrere Runden hinweg
    ist DAS der neue Teamwert — nicht Teamwert + Gebotssumme.
    """
    return team_value + market_value
