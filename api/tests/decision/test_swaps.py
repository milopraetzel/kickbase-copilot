"""Tausch-Paare für Wertverlierer MIT Startelf-Pfad (02.09.2026, Emils Auftrag).

Neue Datei (L-087). `propose_swaps` ist rein: Verlierer (LI nicht Bank, Trend
≤ sell_trend_score_max) bekommt den besten Ersatz derselben Position mit
Kauf-Score ≥ Schwelle und Elf-Score ≥ seinem; Preis wie propose_buys.
"""

from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS, Guardrails
from kickbase_agent.decision.policies import propose_swaps
from kickbase_agent.intel.models import ExplosionCandidate, Signal

G = DEFAULT_GUARDRAILS


def _cand(pid, *, pos=2, mv=8_000_000, score=0.6, elf=0.5, trend=0.5, injury=None, name=None):
    return ExplosionCandidate(
        player_id=pid,
        name=name or f"Spieler {pid}",
        team_id="T" + pid,
        position=pos,
        current_market_value=mv,
        total_score=score,
        lineup_score=elf,
        signals=[Signal(name="trend", score=trend, weight=0.25, reason=f"Trend {trend:.2f}.")],
        injury_severity=injury,
    )


FALLER = _cand("deman", mv=9_500_000, score=0.26, elf=0.34, trend=0.10, name="Deman")
ERSATZ = _cand("keidel", mv=8_000_000, score=0.61, elf=0.55, trend=0.7, name="Keidel")
BENCH_IDS: frozenset[str] = frozenset()  # niemand Bank ⇒ alle gelten als Elf


def test_ohne_li_daten_kein_tausch() -> None:
    assert propose_swaps([FALLER], [ERSATZ], G, bench_ids=None, taken_pids=set(),
                         budget=50_000_000, league_id="L") == []


def test_verlierer_in_der_elf_bekommt_ersatz_derselben_position() -> None:
    pairs = propose_swaps([FALLER], [ERSATZ], G, bench_ids=BENCH_IDS, taken_pids=set(),
                          budget=50_000_000, league_id="L")
    assert len(pairs) == 1
    sw = pairs[0]
    assert sw.faller.player_id == "deman" and sw.sell_amount == 9_500_000
    assert sw.bid.action.action_type == "bid" and sw.bid.action.player_id == "keidel"
    assert sw.bid.rule_id == "swap_bid"
    erwartet = int(8_000_000 * (1 + G.bid_overpay_need)) + G.bid_psych_offset
    assert sw.bid.action.amount == erwartet
    assert sw.bid.action.squad_need is True
    assert "Tausch für Deman" in sw.bid.rationale


def test_bankspieler_ist_kein_tauschfall_der_geht_ueber_pass_2() -> None:
    pairs = propose_swaps([FALLER], [ERSATZ], G, bench_ids=frozenset({"deman"}),
                          taken_pids=set(), budget=50_000_000, league_id="L")
    assert pairs == []


def test_kein_verlierer_ohne_fallenden_trend() -> None:
    stabil = _cand("stabil", score=0.4, elf=0.3, trend=0.5)
    assert propose_swaps([stabil], [ERSATZ], G, bench_ids=BENCH_IDS, taken_pids=set(),
                         budget=50_000_000, league_id="L") == []


def test_ersatz_muss_position_score_und_elf_score_erfuellen() -> None:
    falsche_pos = _cand("mf", pos=3, score=0.7, elf=0.9)
    zu_schwach = _cand("schwach", score=0.45, elf=0.9)
    schlechtere_elf = _cand("bank", score=0.7, elf=0.30)
    verletzt = _cand("out", score=0.7, elf=0.9, injury="long")
    pairs = propose_swaps([FALLER], [falsche_pos, zu_schwach, schlechtere_elf, verletzt], G,
                          bench_ids=BENCH_IDS, taken_pids=set(), budget=50_000_000, league_id="L")
    assert pairs == []


def test_besetzte_und_gebotene_spieler_sind_kein_ersatz() -> None:
    pairs = propose_swaps([FALLER], [ERSATZ], G, bench_ids=BENCH_IDS,
                          taken_pids={"keidel"}, budget=50_000_000, league_id="L")
    assert pairs == []


def test_jeder_ersatz_nur_einmal_und_schlimmster_verlierer_zuerst() -> None:
    f2 = _cand("ullrich", mv=8_200_000, score=0.5, elf=0.35, trend=0.25, name="Ullrich")
    pairs = propose_swaps([f2, FALLER], [ERSATZ], G, bench_ids=BENCH_IDS, taken_pids=set(),
                          budget=50_000_000, league_id="L")
    assert [p.faller.player_id for p in pairs] == ["deman"]  # Trend 0.10 vor 0.25


def test_manager_listing_ankert_am_marktwert_und_kappe_haelt() -> None:
    pairs = propose_swaps([FALLER], [ERSATZ], G, bench_ids=BENCH_IDS, taken_pids=set(),
                          budget=50_000_000, league_id="L",
                          manager_listing_prices={"keidel": 7_600_000})
    # Verifizierter Starterersatz nutzt direkt die Bedarfsklasse.
    staffel = int(8_000_000 * (1 + G.bid_overpay_need)) + G.bid_psych_offset
    assert pairs[0].bid.action.amount == max(staffel, 8_000_000 + G.bid_psych_offset)
    teuer = propose_swaps([FALLER], [ERSATZ], G, bench_ids=BENCH_IDS, taken_pids=set(),
                          budget=50_000_000, league_id="L",
                          manager_listing_prices={"keidel": 12_000_000})
    assert teuer[0].bid.action.amount == int(8_000_000 * G.max_bid_premium_need)


def test_tausch_finanziert_sich_aus_dem_eigenen_verkauf() -> None:
    """Verkaufserlös plus echte Restkaufkraft decken das 120-%-Ersatzgebot."""
    pairs = propose_swaps([FALLER], [ERSATZ], G, bench_ids=BENCH_IDS, taken_pids=set(),
                          budget=200_000, league_id="L")
    assert len(pairs) == 1
    teuer = _cand("star", mv=12_000_000, score=0.7, elf=0.9)
    assert propose_swaps([FALLER], [teuer], G, bench_ids=BENCH_IDS, taken_pids=set(),
                         budget=0, league_id="L") == []  # 12,25 M > 0 + 9,5 M


def test_plan_basis_kappe_zaehlt_zusaetzlich_zum_erloes() -> None:
    teuer = _cand("star", mv=12_000_000, score=0.7, elf=0.9)
    pairs = propose_swaps([FALLER], [teuer], G, bench_ids=BENCH_IDS, taken_pids=set(),
                          budget=7_100_000, league_id="L")  # 4,97 M + 9,5 M ≥ 14,41 M
    assert len(pairs) == 1


def test_geschuetzte_und_verletzte_verlierer_bleiben() -> None:
    protected = next(iter(G.all_protected_ids))
    olise = _cand(protected, pos=3, score=0.3, elf=0.8, trend=0.1)
    verletzt = _cand("inj", score=0.2, elf=0.3, trend=0.1, injury="short")
    ersatz_mf = _cand("mf2", pos=3, score=0.7, elf=0.9)
    pairs = propose_swaps([olise, verletzt], [ersatz_mf, ERSATZ], G, bench_ids=BENCH_IDS,
                          taken_pids=set(), budget=50_000_000, league_id="L")
    assert pairs == []


def test_max_pairs_deckelt() -> None:
    fallers = [_cand(f"f{i}", score=0.2, elf=0.3, trend=0.1) for i in range(4)]
    ersatz = [_cand(f"e{i}", score=0.7, elf=0.6) for i in range(4)]
    pairs = propose_swaps(fallers, ersatz, Guardrails(), bench_ids=BENCH_IDS, taken_pids=set(),
                          budget=50_000_000, league_id="L", max_pairs=2)
    assert len(pairs) == 2
