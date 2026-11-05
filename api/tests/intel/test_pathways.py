"""build_pathways (intel/pathways.py): bedingte Pfade je offenem Gebot."""

from __future__ import annotations

from kickbase_agent.intel.pathways import build_pathways

#: Zeilen im /api/bids-Format (server/routes/live.py).
BID_BIG = {
    "player_id": "8329", "name": "Olise",
    "amount_eur": 68_017_197, "listing_price_eur": 66_000_000,
}
BID_SMALL = {
    "player_id": "99", "name": "Hidden Gem",
    "amount_eur": 5_120_000, "listing_price_eur": 5_000_000,
}
PLANNED = [
    {"kind": "bid", "player_name": "Kimmich", "amount_eur": 19_000_000},
    {"kind": "lineup", "player_name": "", "amount_eur": 0},  # kein Kauf -> ignoriert
]
CANDIDATES = ["Vozar", "Kimmich", "Sané"]  # Kimmich doppelt -> dedupliziert


def _by_kind(pathways):
    out: dict[str, list[dict]] = {}
    for p in pathways:
        out.setdefault(p["kind"], []).append(p)
    return out


def test_win_path_with_negative_konto_has_purge_hint():
    pathways = build_pathways([BID_BIG], budget_eur=10_000_000, team_value_eur=90_000_000)
    win = _by_kind(pathways)["win"][0]
    assert win["if"] == "Zuschlag Olise (68,02 M)"
    assert win["player_id"] == "8329" and win["player_name"] == "Olise"
    assert win["konto_eur"] == 10_000_000 - 68_017_197
    assert win["then"][0] == f"Konto → {win['konto_fmt']}"
    assert any(s.startswith("Purge-Pfad: Verkäufe nötig bis ≥ 0 vor Anpfiff") for s in win["then"])
    # Spielraum: Konto danach + (Teamwert + MW-Proxy 66 M) / 3, keine uebrigen Gebote:
    # -58.017.197 + 52.000.000 = -6.017.197
    assert win["then"][1] == "Spielraum → -6,02 M (33-%-Regel, übrige Gebote gebunden)"


def test_win_path_positive_konto_has_no_purge_hint():
    pathways = build_pathways([BID_SMALL], budget_eur=10_000_000, team_value_eur=90_000_000)
    win = _by_kind(pathways)["win"][0]
    assert win["konto_eur"] == 4_880_000
    assert not any("Purge" in s for s in win["then"])


def test_lose_path_frees_amount_and_names_candidates():
    pathways = build_pathways(
        [BID_SMALL], budget_eur=10_000_000, team_value_eur=90_000_000,
        planned_intents=PLANNED, candidates=CANDIDATES,
    )
    lose = _by_kind(pathways)["lose"][0]
    assert lose["if"] == "Kein Zuschlag Hidden Gem"
    assert lose["then"][0] == "5,12 M frei"
    # geplante Kauf-Vorhaben zuerst, dann Kandidaten, dedupliziert
    assert lose["then"][1] == "Planner-Kandidaten: Kimmich, Vozar, Sané"
    assert lose["konto_eur"] == 10_000_000


def test_lose_path_without_candidates_is_honest():
    pathways = build_pathways([BID_SMALL], budget_eur=1, team_value_eur=0)
    lose = _by_kind(pathways)["lose"][0]
    assert lose["then"][1] == "Planner-Kandidaten: derzeit keine"


def test_extreme_paths_all_win_and_all_lose():
    pathways = build_pathways(
        [BID_BIG, BID_SMALL], budget_eur=10_000_000, team_value_eur=90_000_000,
        candidates=["Vozar"],
    )
    kinds = _by_kind(pathways)
    assert len(kinds["win"]) == 2 and len(kinds["lose"]) == 2
    all_win = kinds["all_win"][0]
    assert all_win["if"] == "Alle Gebote gewinnen (2)"
    assert all_win["konto_eur"] == 10_000_000 - 68_017_197 - 5_120_000
    assert any("Purge-Pfad" in s for s in all_win["then"])
    all_lose = kinds["all_lose"][0]
    assert all_lose["if"] == "Alle Gebote verlieren"
    assert all_lose["konto_eur"] == 10_000_000
    assert all_lose["then"][0] == "Konto bleibt 10,00 M"
    assert any("wieder frei" in s for s in all_lose["then"])
    assert any(s.startswith("Planner-Kandidaten:") for s in all_lose["then"])
    # Reihenfolge: erst die Gebots-Paare, dann die Extremfaelle
    assert [p["kind"] for p in pathways] == ["win", "lose", "win", "lose", "all_win", "all_lose"]


def test_no_bids_yields_single_idle_path():
    pathways = build_pathways([], budget_eur=12_000_000, team_value_eur=90_000_000,
                              candidates=["Vozar"])
    assert len(pathways) == 1
    idle = pathways[0]
    assert idle["kind"] == "idle"
    assert idle["if"] == "Keine offenen Gebote"
    assert idle["konto_eur"] == 12_000_000
    assert idle["then"][0] == "Konto 12,00 M"
    assert "42,00 M" in idle["then"][1]  # 12 M + 90/3 M Kaufkraft
    assert idle["then"][2] == "Planner-Kandidaten: Vozar"


def test_tolerates_mcp_style_keys_without_eur_suffix():
    bid = {"player_id": "7", "name": "Alt", "amount": 2_000_000, "listing_price": 1_900_000}
    pathways = build_pathways([bid], budget_eur=5_000_000, team_value_eur=30_000_000)
    win = _by_kind(pathways)["win"][0]
    assert win["if"] == "Zuschlag Alt (2,00 M)"
    assert win["konto_eur"] == 3_000_000
