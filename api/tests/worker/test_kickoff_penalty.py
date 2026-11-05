"""Die 100-Punkte-Strafe je unbesetztem Aufstellungsplatz (Emils Fund 27.08.2026).

Offizielle Kickbase-Hilfe („Rund um Kontostand & Aufstellung"): „für jede
unbesetzte Position werden Dir 100 Punkte abgezogen." Emil kannte die Regel,
unsere Notbremse rechnete sie nicht mit — sie bezifferte einen Elf-Verkauf mit
60–90 statt 160–190 Punkten und meldete Emil damit einen zu billigen Preis.

Bewusst eine eigene Datei: `tests/worker/test_kickoff_guard.py` ist ein
versiegelter Sicherungs-Test (api/guardrails.lock). Was hier steht, ist neu und
soll das Siegel nicht anfassen; stuft Emil die Strafe später als Sicherung ein,
wandert die Datei ins Manifest.
"""

from __future__ import annotations

from kickbase_agent.worker.kickoff_guard import EMPTY_SLOT_PENALTY, purge_point_cost


def _sell(name: str, stufe: str) -> tuple[str, str, int, str]:
    return (f"id-{name}", name, 5_000_000, stufe)


def test_bank_and_trading_sales_cost_no_points():
    """Wer nicht in der Elf steht, hinterlässt keine Lücke — Verkauf ist gratis."""
    sells = [_sell("Reggiani", "S1 Trading"), _sell("Vogt", "S2 Bank")]
    assert purge_point_cost(sells) == 0


def test_every_sold_starter_costs_one_hundred():
    """Erreicht die Kaskade S3, ist die Bank leer — jeder Verkauf lässt einen
    Platz offen, und der kostet 100 Punkte."""
    sells = [_sell("Schäfer", "S3 Startelf!"), _sell("Højlund", "S3 Elf (Start unwahrsch.)")]
    assert purge_point_cost(sells) == 2 * EMPTY_SLOT_PENALTY


def test_keeper_counts_like_any_other_empty_slot():
    """Der Torwart steht ganz hinten in der Kaskade, kostet aber dasselbe."""
    assert purge_point_cost([_sell("Schwäbe", "S3 Torwart!!")]) == EMPTY_SLOT_PENALTY


def test_mixed_cascade_charges_only_the_lineup_sales():
    sells = [
        _sell("Reggiani", "S1 Trading"),
        _sell("Vogt", "S2 Bank"),
        _sell("Leszczynski", "S3 Elf (Start unwahrsch.)"),
    ]
    assert purge_point_cost(sells) == EMPTY_SLOT_PENALTY


def test_empty_cascade_is_free():
    assert purge_point_cost([]) == 0


def test_penalty_stays_cheaper_than_a_negative_balance():
    """Die Abwägung, auf der die ganze Notbremse steht: Selbst wenn sie die halbe
    Elf abverkaufen müsste, bleibt das billiger als ein Minus-Konto (dann gibt es
    NULL Punkte für den gesamten Spieltag, real ~1.000–1.400).

    Sichert die Aussage im Modul-Docstring gegen eine spätere Erhöhung der
    Strafe ab: Zöge Kickbase je Platz 300 statt 100 Punkte ab, wäre die
    Rechnung nicht mehr eindeutig und die Notbremse müsste neu begründet werden.
    """
    schlimmster_fall = purge_point_cost([_sell(f"p{i}", "S3 Startelf!") for i in range(5)])
    assert schlimmster_fall == 500
    assert schlimmster_fall < 1_000
