"""Doktrin-Satz 3: gescheiterte eigene Listings → Sofortverkauf (01.09.2026).

Neue Datei (L-087: versiegelte Testdateien nie anfassen). Getestet wird die
reine Policy `propose_failed_listing_sells` — Guards deckungsgleich mit
propose_accepts/Pass 3: bench_ids-Pflicht (fail-safe), Schutzliste,
Kader-Deckung, MV-Reihenfolge.
"""

from datetime import UTC, datetime

from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.decision.policies import FailedListing, propose_failed_listing_sells


def _failed(pid: str, mv: int = 900_000, pos: int = 3, name: str | None = None) -> FailedListing:
    return FailedListing(
        player_id=pid,
        player_name=name or f"Spieler {pid}",
        team_id="7",
        position=pos,
        market_value=mv,
        listed_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        age_hours=96.0,
    )


def _squad_12() -> dict[str, int]:
    """12er-Kader: 1 TW, 4 ABW, 4 MF, 3 ST — genau EIN Abgang bleibt gedeckt."""
    squad = {"tw1": 1}
    squad.update({f"abw{i}": 2 for i in range(4)})
    squad.update({f"mf{i}": 3 for i in range(4)})
    squad.update({f"st{i}": 4 for i in range(3)})
    return squad


def test_ohne_li_daten_keine_vorschlaege() -> None:
    """bench_ids=None (LI-Scout aus/Fehler) → Pfad komplett AUS (L-090 fail-safe)."""
    props = propose_failed_listing_sells(
        [_failed("mf0")], _squad_12(), "L1", DEFAULT_GUARDRAILS, bench_ids=None
    )
    assert props == []


def test_elf_spieler_wird_weitergehalten() -> None:
    """Gelisteter Spieler OHNE LI-Bank-Status (also Elf/unbekannt) wird nie verkauft."""
    props = propose_failed_listing_sells(
        [_failed("mf0")],
        _squad_12(),
        "L1",
        DEFAULT_GUARDRAILS,
        bench_ids=frozenset({"mf1"}),  # mf0 ist NICHT Bank
    )
    assert props == []


def test_schutzliste_gilt() -> None:
    """Verfassung: geschützte Spieler werden auch als gescheitertes Listing nie verkauft."""
    squad = _squad_12()
    protected = next(iter(DEFAULT_GUARDRAILS.all_protected_ids))
    squad[protected] = 3
    props = propose_failed_listing_sells(
        [_failed(protected)], squad, "L1", DEFAULT_GUARDRAILS, bench_ids=frozenset({protected})
    )
    assert props == []


def test_letzter_stuermer_faellt_nie() -> None:
    """Vogt-Fall: 12er-Kader, aber einziger Stürmer — Formation risse, Listing bleibt."""
    squad = {"tw1": 1, **{f"abw{i}": 2 for i in range(4)}, **{f"mf{i}": 3 for i in range(6)}}
    squad["st0"] = 4
    assert len(squad) == 12
    props = propose_failed_listing_sells(
        [_failed("st0", pos=4)], squad, "L1", DEFAULT_GUARDRAILS, bench_ids=frozenset({"st0"})
    )
    assert props == []


def test_kader_floor_11_haelt() -> None:
    """Aus einem 11er-Kader wird nie verkauft (Kader-Gesetz)."""
    squad = {"tw1": 1, **{f"abw{i}": 2 for i in range(4)}, **{f"mf{i}": 3 for i in range(3)}}
    squad.update({f"st{i}": 4 for i in range(3)})
    assert len(squad) == 11
    props = propose_failed_listing_sells(
        [_failed("mf0")], squad, "L1", DEFAULT_GUARDRAILS, bench_ids=frozenset({"mf0"})
    )
    assert props == []


def test_happy_path_sofortverkauf_zum_marktwert() -> None:
    props = propose_failed_listing_sells(
        [_failed("mf0", mv=1_200_000)],
        _squad_12(),
        "L1",
        DEFAULT_GUARDRAILS,
        bench_ids=frozenset({"mf0"}),
    )
    assert len(props) == 1
    p = props[0]
    assert p.action.action_type == "sell"
    assert p.rule_id == "listing_failed"
    assert p.action.amount == 1_200_000
    assert p.requires_approval is False
    assert "ohne ein einziges Angebot" in p.rationale


def test_teuerster_zuerst_zweiter_scheitert_am_floor() -> None:
    """Reihenfolge nach MV; die Deckung zählt kumulativ: 12 − 2 = 10 < 11 ⇒ nur einer."""
    props = propose_failed_listing_sells(
        [_failed("mf0", mv=800_000), _failed("mf1", mv=2_000_000)],
        _squad_12(),
        "L1",
        DEFAULT_GUARDRAILS,
        bench_ids=frozenset({"mf0", "mf1"}),
    )
    assert [p.action.player_id for p in props] == ["mf1"]


def test_ohne_marktwert_kein_vorschlag() -> None:
    """MV 0 → kein Vorhaben (der Executor würde MV-los ohnehin fail-closed verweigern)."""
    props = propose_failed_listing_sells(
        [_failed("mf0", mv=0)], _squad_12(), "L1", DEFAULT_GUARDRAILS, bench_ids=frozenset({"mf0"})
    )
    assert props == []


def test_grosser_verkauf_traegt_approval_flagge() -> None:
    """> 5 M markiert requires_approval (Anzeige + 2-h-Fenster, keine Freigabepflicht)."""
    props = propose_failed_listing_sells(
        [_failed("mf0", mv=6_000_000)],
        _squad_12(),
        "L1",
        DEFAULT_GUARDRAILS,
        bench_ids=frozenset({"mf0"}),
    )
    assert len(props) == 1 and props[0].requires_approval is True


def test_nicht_mehr_im_kader_wird_uebersprungen() -> None:
    """Listing-Spieler, der (Race) nicht mehr im Kader ist, erzeugt nichts."""
    props = propose_failed_listing_sells(
        [_failed("fremd")], _squad_12(), "L1", DEFAULT_GUARDRAILS, bench_ids=frozenset({"fremd"})
    )
    assert props == []
