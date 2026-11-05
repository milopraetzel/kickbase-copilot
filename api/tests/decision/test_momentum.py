"""Momentum-Doktrin (31.08.): Reserve, Gewinnmitnahme (Pass 3), Listen-Kontingent.

Bewusst EIGENE Datei neben der versiegelten tests/decision/test_policies.py
(L-087: versiegelte Dateien werden nie angefasst — neue Zusicherungen wohnen
in neuen Dateien).
"""

from kickbase_agent.decision.guardrails import Guardrails
from kickbase_agent.decision.policies import momentum_reserve, propose_sells
from kickbase_agent.intel.models import ExplosionCandidate, Signal

LEAGUE = "1234567"


def cand(
    pid: str,
    mv: int = 2_000_000,
    score: float = 0.8,
    lineup: float | None = None,
    injury: str | None = None,
    position: int = 3,
    trend: float | None = None,
    trend_1d: float | None = None,
) -> ExplosionCandidate:
    signals = []
    if trend is not None:
        signals.append(
            Signal(name="trend", score=trend, weight=0.15, reason="Marktwert-Verlauf.")
        )
    if trend_1d is not None:
        signals.append(
            Signal(name="trend_1d", score=trend_1d, weight=0.05, reason="Tagesschritt.")
        )
    return ExplosionCandidate(
        player_id=pid,
        name=f"Spieler {pid}",
        team_id="1",
        position=position,
        current_market_value=mv,
        total_score=score,
        lineup_score=score if lineup is None else lineup,
        signals=signals,
        injury_severity=injury,
    )


def viable_squad(strong: float = 0.8) -> list[ExplosionCandidate]:
    squad = [cand("gk1", position=1, score=strong)]
    squad += [cand(f"d{i}", position=2, score=strong) for i in range(5)]
    squad += [cand(f"m{i}", position=3, score=strong) for i in range(5)]
    squad += [cand(f"s{i}", position=4, score=strong) for i in range(3)]
    return squad


# --- momentum_reserve -------------------------------------------------------


def test_reserve_zaehlt_nur_li_bank_spieler_ohne_startelf_pfad():
    squad = [
        cand("bank1", mv=6_000_000, lineup=0.20),   # LI-Bank ⇒ zaehlt
        cand("bank2", mv=1_300_000, lineup=0.10),   # LI-Bank ⇒ zaehlt
        cand("starter", mv=60_000_000, lineup=0.80),  # Startelf-Pfad — nie Kasse
    ]
    g = Guardrails(momentum_reserve_haircut=1.0)  # volle Werte für krumme Summen
    assert momentum_reserve(squad, g, bench_ids={"bank1", "bank2"}) == 7_300_000


def test_reserve_zaehlt_li_starter_nie_auch_bei_niedrigem_score():
    # Nachtrag 31.08.: Nach SP1 liegen ECHTE Starter wegen dünner Historie
    # unter 0.45 (Schwäbe 0.34, Khedira 0.21) — der LI-Status entscheidet.
    squad = [
        cand("schwaebe", mv=15_000_000, lineup=0.34),
        cand("khedira", mv=8_700_000, lineup=0.21),
        cand("bank1", mv=6_000_000, lineup=0.22),
    ]
    g = Guardrails(momentum_reserve_haircut=1.0)
    assert momentum_reserve(squad, g, bench_ids={"bank1"}) == 6_000_000


def test_reserve_ohne_li_daten_ist_null():
    squad = [cand("bank1", mv=6_000_000, lineup=0.10)]
    assert momentum_reserve(squad, Guardrails(), bench_ids=None) == 0


def test_reserve_respektiert_schutzliste_und_halten_regel():
    g = Guardrails(
        protected_player_ids=frozenset({"heilig"}), momentum_reserve_haircut=1.0
    )
    squad = [
        cand("heilig", mv=64_000_000, lineup=0.10),          # Verfassung
        cand("grippe", mv=5_000_000, lineup=0.10, injury="short"),  # Halten-Regel
        cand("kreuzband", mv=4_000_000, lineup=0.10, injury="long"),  # liquide (Pass 1)
    ]
    bench = {"heilig", "grippe", "kreuzband"}
    assert momentum_reserve(squad, g, bench_ids=bench) == 4_000_000


def test_reserve_haircut_daempft():
    g = Guardrails(momentum_reserve_haircut=0.5)
    # MF-Gesellschaft ÜBER dem Formations-Minimum (2): das Positions-Reserve-
    # Gate (01.09., 9c-Fund) zählt nur Positionen mit echter Reserve als Kasse.
    squad = [
        cand("b", mv=6_000_000, lineup=0.1),
        cand("kollege1", mv=1, lineup=0.9),
        cand("kollege2", mv=1, lineup=0.9),
    ]
    assert momentum_reserve(squad, g, bench_ids={"b"}) == 3_000_000


def test_reserve_positions_gate_einziger_stuermer_zaehlt_nie():
    # 9c-Fund „Vogt-Fiktion": Bank-Stürmer, aber einziger STU im Kader — sein
    # Verkauf käme nie frei, sein MW wäre Schein-Deckung.
    g = Guardrails(momentum_reserve_haircut=1.0)
    squad = viable_squad()[:12]  # 1 TW / 5 ABW / 5 MF / 1 ST
    squad = [c for c in squad if c.position != 4] + [
        cand("einziger_st", mv=1_340_000, lineup=0.10, position=4)
    ]
    assert momentum_reserve(squad, g, bench_ids={"einziger_st"}) == 0


# --- Pass 3: Gewinnmitnahme am Wendepunkt -----------------------------------


def _steiger(pid: str = "hot", trend_1d: float = 0.40, lineup: float = 0.20, **kw):
    """Starker 7-Tage-Steiger ohne Startelf-Pfad, Tagesschritt kippt (Default)."""
    return cand(pid, mv=8_000_000, score=0.60, lineup=lineup,
                trend=0.95, trend_1d=trend_1d, **kw)


def test_take_profit_verkauft_steiger_am_wendepunkt():
    props = propose_sells(
        viable_squad() + [_steiger()], LEAGUE, Guardrails(), bench_ids={"hot"}
    )
    mine = [p for p in props if p.action.player_id == "hot"]
    assert len(mine) == 1
    assert mine[0].rule_id == "take_profit"
    assert mine[0].action.action_type == "sell"  # Sofortverkauf, nicht listen (L-081)


def test_take_profit_ohne_li_daten_ist_aus():
    """Review 31.08. (L-090): Ohne LI-Bank-Status ist niemand "pfadlos" —
    der Sofortverkaufs-Pass bleibt fail-safe komplett stumm (wie die Reserve)."""
    props = propose_sells(viable_squad() + [_steiger()], LEAGUE, Guardrails())
    assert [p for p in props if p.action.player_id == "hot"] == []


def test_take_profit_verschont_li_starter_trotz_duennem_elf_score():
    """Review 31.08.: Ein echter LI-Starter (nicht in bench_ids) mit Elf-Score
    0.20 (Khedira-Fall) wird NIE zur Kasse gemacht — der Score-Proxy allein
    reicht nicht mehr (Neuzugangs-Fenster vor dem 18:40-Refresh)."""
    props = propose_sells(
        viable_squad() + [_steiger()], LEAGUE, Guardrails(), bench_ids={"jemand_anderes"}
    )
    assert [p for p in props if p.action.player_id == "hot"] == []


def test_take_profit_haelt_solange_der_tagesschritt_steigt():
    props = propose_sells(
        viable_squad() + [_steiger(trend_1d=0.62)], LEAGUE, Guardrails(),
        bench_ids={"hot"},
    )
    assert [p for p in props if p.action.player_id == "hot"] == []


def test_take_profit_verschont_startelf_pfad():
    props = propose_sells(
        viable_squad() + [_steiger(lineup=0.70)], LEAGUE, Guardrails(),
        bench_ids={"hot"},
    )
    assert [p for p in props if p.action.player_id == "hot"] == []


def test_take_profit_braucht_den_tagesmarker():
    ohne_marker = cand("hot", mv=8_000_000, score=0.60, lineup=0.20, trend=0.95)
    props = propose_sells(
        viable_squad() + [ohne_marker], LEAGUE, Guardrails(), bench_ids={"hot"}
    )
    assert [p for p in props if p.action.player_id == "hot"] == []


def test_take_profit_respektiert_schutz_und_verletzte():
    g = Guardrails(protected_player_ids=frozenset({"hot"}))
    assert [
        p for p in propose_sells(
            viable_squad() + [_steiger()], LEAGUE, g, bench_ids={"hot"}
        )
        if p.action.player_id == "hot"
    ] == []
    props = propose_sells(
        viable_squad() + [_steiger(pid="krank", injury="short")], LEAGUE, Guardrails(),
        bench_ids={"krank"},
    )
    assert [p for p in props if p.action.player_id == "krank"] == []


def test_pass2_rotiert_fallende_starter_trotz_li_daten():
    """Fable 5.1: LI-Status schützt nur Olise, nicht andere fallende Starter."""
    faller = cand("fall", mv=5_000_000, score=0.50, lineup=0.20, trend=0.10)
    mit_li = propose_sells(
        viable_squad() + [faller], LEAGUE, Guardrails(), bench_ids={"andere"}
    )
    assert [p.rule_id for p in mit_li if p.action.player_id == "fall"] == ["sell_trend"]
    alt = propose_sells(viable_squad() + [faller], LEAGUE, Guardrails())
    assert [p.rule_id for p in alt if p.action.player_id == "fall"] == ["sell_trend"]


# --- Listen-Kontingent (L-077 vollendet) ------------------------------------


def _schwache(n: int) -> list[ExplosionCandidate]:
    # Schwache, gesunde Mittelfeld-Spieler unter der Verkaufsschwelle → "list".
    return [cand(f"w{i}", score=0.10, lineup=0.10) for i in range(n)]


def test_legacy_ohne_list_slots_zaehlt_listings_gegen_den_verkaufs_cap():
    squad = viable_squad() + _schwache(8)
    props = propose_sells(squad, LEAGUE, Guardrails())
    assert len(props) == Guardrails().daily_sell_cap  # Alt-Verhalten unveraendert


def test_list_slots_trennt_die_toepfe():
    squad = viable_squad() + _schwache(8)
    props = propose_sells(squad, LEAGUE, Guardrails(), list_slots=12)
    lists = [p for p in props if p.action.action_type == "list"]
    assert len(lists) == 8  # alle acht Listings passen ins eigene Kontingent


def test_list_slots_deckelt_auch_das_eigene_kontingent():
    squad = viable_squad() + _schwache(8)
    props = propose_sells(squad, LEAGUE, Guardrails(), list_slots=3)
    assert len([p for p in props if p.action.action_type == "list"]) == 3


def test_list_slots_laesst_sofortverkaeufe_im_verkaufs_cap():
    # Ein Langzeit-Verletzter (sell) und viele Listings: der Verkauf braucht
    # keinen Listen-Slot, die Listings keinen Verkaufs-Slot.
    squad = viable_squad() + _schwache(6) + [
        cand("op", score=0.05, lineup=0.05, injury="long")
    ]
    props = propose_sells(squad, LEAGUE, Guardrails(), list_slots=6)
    sells = [p for p in props if p.action.action_type == "sell"]
    lists = [p for p in props if p.action.action_type == "list"]
    assert [p.action.player_id for p in sells] == ["op"]
    assert len(lists) == 6
