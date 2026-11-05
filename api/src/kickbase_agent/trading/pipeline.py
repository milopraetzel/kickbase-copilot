"""Trading-Fabrik: Bidding-Schleife (Mo–Do) + Wendepunkt-Verkauf.

Füllt freie Kaderplätze mit Marktwert-Steigern. Gebot = Marktwert + anteiliger
Overpay vom erwarteten Halte-Gewinn, sodass jeder Trade profitabel bleibt.
Alle Gebote einer Runde teilen sich das 33%-Limit des AKTUELLEN Zustands
("nach Annahme aller Gebote") — erst ein Zuschlag hebt den Teamwert an, und
zwar nur um den Marktwert des Spielers, nie um den Kaufpreis (siehe finance.py).
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from kickbase_agent.trading.finance import can_afford
from kickbase_agent.trading.scanner import Riser, momentum


@dataclass(frozen=True)
class TradingConfig:
    hold_days: int = 4              # geplante Haltedauer
    overpay_fraction: float = 0.5   # so viel vom erwarteten Gewinn als Overpay bieten
    min_pct_per_day: float = 0.005  # Mindest-Momentum, um überhaupt zu handeln
    window: int = 5                 # Tage fürs geglättete Momentum
    # Kaderlimit (Trading-Slots = Limit − Kadergröße). None → Settings-Wert
    # kickbase_squad_limit; der Orchestrator löst das beim Planen auf.
    max_squad_size: int | None = None


@dataclass(frozen=True)
class TradingBuy:
    player_id: str
    name: str
    market_value: int
    bid: int
    expected_gain: int
    rationale: str


@dataclass(frozen=True)
class TradingSell:
    player_id: str
    name: str
    market_value: int
    reason: str


def _m(v: float) -> str:
    return f"{v / 1_000_000:.1f}M"


def plan_trading_buys(
    risers: list[Riser],
    budget: int,
    team_value: int,
    open_slots: int,
    cfg: TradingConfig,
) -> list[TradingBuy]:
    """Fülle freie Trading-Slots mit den stärksten Steigern, im Overdraft-Rahmen.

    Alle geplanten Gebote sind gleichzeitig offen — sie werden kumuliert gegen
    das 33%-Limit des aktuellen Zustands geprüft (Kickbase rechnet "nach
    Annahme aller Gebote"). Der Teamwert wächst dabei NICHT mit: das täte er
    erst nach einem Zuschlag, und nur um den Marktwert des Spielers.
    """
    buys: list[TradingBuy] = []
    committed = 0  # Summe der bereits geplanten (gleichzeitig offenen) Gebote
    for r in risers:
        if len(buys) >= open_slots:
            break
        expected_gain = int(r.momentum.daily_change * cfg.hold_days)
        if expected_gain <= 0:
            continue
        overpay = int(expected_gain * cfg.overpay_fraction)
        bid = r.market_value + max(0, overpay)
        if not can_afford(budget, team_value, bid, open_bids=committed):
            continue  # nächster Kandidat könnte billiger sein
        buys.append(
            TradingBuy(
                player_id=r.player_id,
                name=r.name,
                market_value=r.market_value,
                bid=bid,
                expected_gain=expected_gain,
                rationale=(
                    f"{r.name}: +{_m(r.momentum.daily_change)}/Tag → "
                    f"~{_m(expected_gain)} Gewinn in {cfg.hold_days}T; "
                    f"Gebot {_m(bid)} (Overpay {_m(overpay)})."
                ),
            )
        )
        committed += bid
    return buys


def plan_trading_sells(
    holdings: list[tuple[str, str, int, list[int]]],
    cfg: TradingConfig,
) -> list[TradingSell]:
    """Verkaufe Trading-Spieler, deren Kurve abflacht oder dreht (Gewinn sichern)."""
    sells: list[TradingSell] = []
    for player_id, name, mv, values in holdings:
        m = momentum(values, cfg.window)
        if not m.rising:
            sells.append(TradingSell(player_id, name, mv, "Marktwert fällt — raus."))
        elif m.flattening:
            sells.append(TradingSell(player_id, name, mv, "Anstieg flacht ab — Gewinn sichern."))
    return sells


def plan_risk_sells(
    core_holdings: list[tuple[str, str, int, list[int], int | None]],
    cfg: TradingConfig,
    *,
    protected_ids: AbstractSet[str] | None = None,
) -> list[TradingSell]:
    """Liste Core-Risiken, statt fallenden MW passiv mitzuschleppen.

    Trading-Historie fehlt am Saisonstart oft; dann war bislang der gesamte Kader
    „Core" und fallende Spieler wurden nicht aktiv angefasst. Fable 5.1
    (02.09., Emil): Olise ist die einzige echte Ausnahme. Jeder andere Spieler
    mit fallendem Marktwert vernichtet Kapital und muss aktiv in Rotation:
    listen/ersetzen, nicht schönrechnen. Der finale Abgang bleibt in den
    nachgelagerten Pfaden an Kaderdeckung/Ersatz gekoppelt; das Listing-Signal
    darf aber nicht mehr an „Core" oder Startelf-Optimismus scheitern.
    """
    protected = protected_ids or set()
    sells: list[TradingSell] = []
    for player_id, name, mv, values, lineup_prob in core_holdings:
        if player_id in protected or len(values) < 2:
            continue
        m = momentum(values, cfg.window)
        if not m.rising:
            starter_note = (
                "Startelf unsicher" if lineup_prob is None or lineup_prob >= 4
                else f"KB-Stufe {lineup_prob}"
            )
            sells.append(
                TradingSell(
                    player_id,
                    name,
                    mv,
                    "Core-Rotation: MW fällt — Kapitalverlust stoppen; "
                    f"{starter_note}, Ersatz kaufen und Verkauf/Listing aktiv halten.",
                )
            )
    return sells
