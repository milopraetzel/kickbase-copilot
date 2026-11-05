"""Erwartung je Gebot + Kalibrierung (Emils Auftrag 25.08.).

Anlass: Vier von fünf Manager-Deals scheiterten, und aufgefallen ist es erst, als
Emil fragte — jede Einzelentscheidung sah für sich vernünftig aus. Seit jetzt trägt
jedes Gebot eine überprüfbare Erwartung („Zuschlag-Chance 15 %, weil Manager-Listing
zum Marktwert"); der stündliche Abgleich hält sie gegen die Realität.

Die Schätzformel IST die Hypothese — die Kalibrierung prüft sie. Deshalb stehen die
Zahlen hier als Testfälle: Ändert jemand die Formel, muss er die Empirie erklären.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.trading import expectation as ex

NOW = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _bid(
    session: Session,
    pid: str,
    *,
    p: float | None = 0.9,
    outcome: str | None = None,
    days_ago: float = 1,
    amount: int = 5_000_000,
) -> TransferAction:
    payload: dict = {}
    if p is not None:
        payload["expectation"] = {"p": p, "why": "Test", "kind": "kickbase"}
    if outcome is not None:
        payload["outcome"] = {"result": outcome, "at": NOW.isoformat(),
                              "evidence_version": 2, "training_eligible": True}
    row = TransferAction(
        league_id="L1", action_type="bid", player_id=pid, amount=amount,
        status="executed", idempotency_key=f"k{pid}{days_ago}{outcome}",
        rule_id="test", created_at=NOW - timedelta(days=days_ago),
        payload=payload or None,
    )
    session.add(row)
    session.commit()
    return row


# ------------------------------------------------------------------ Schätzung


def test_kickbase_thin_bid_is_no_safe_bet_even_without_rivals():
    """Ljubičić-Lehre 26.08.: Wir boten 108 % und waren laut API einziger Bieter —
    Lukas M. holte ihn beim Ablauf mit 157 %. „Allein" heißt nur „bisher allein".
    """
    est = ex.estimate_bid_success(kind="kickbase", amount=8_600_000, market_value=8_000_000,
                                  listing_price=8_000_000)
    assert 0.35 <= est.p <= 0.55 and "Späteinsteiger" in est.why


def test_kickbase_strong_bid_is_likely():
    """Erst deutlich über Marktwert wird ein Kickbase-Listing verlässlich."""
    est = ex.estimate_bid_success(kind="kickbase", amount=11_000_000, market_value=8_000_000,
                                  listing_price=8_000_000)
    assert est.p >= 0.75


def test_expectation_never_claims_to_know_about_other_bidders():
    """Emils Einwand 26.08.: „einziger Bieter" stand in jeder Meldung — auslesen
    kann man das nicht. Die Messung gab ihm recht: `ofc` zählt NUR eigene Gebote
    (49 von 51 Listings 0; die zwei mit 1 waren genau unsere beiden). Seitdem
    kennt die Formel keine Mitbieter mehr — weder als Parameter noch im Text."""
    import inspect
    assert "rival_bids" not in inspect.signature(ex.estimate_bid_success).parameters
    for amount in (7_000_000, 8_600_000, 9_800_000, 11_000_000):
        est = ex.estimate_bid_success(kind="kickbase", amount=amount,
                                      market_value=8_000_000, listing_price=8_000_000)
        for wort in ("Mitbieter", "einziger", "Einziger", "allein", "Konkurrenz"):
            assert wort not in est.why, f"{est.why!r} behauptet Wissen über fremde Gebote"


def test_kickbase_underpay_is_unlikely():
    """Friedrich-Empirie 22.08.: 81 % des Marktwerts verlor ersatzlos."""
    est = ex.estimate_bid_success(kind="kickbase", amount=1_220_000, market_value=1_500_000,
                                  listing_price=1_500_000)
    assert est.p <= 0.4


def test_manager_listing_at_market_value_is_nearly_hopeless():
    """Bilanz 25.08.: Ulrich 2x, Gyamerah, Otávio — alle zum MW abgelehnt.
    Der Manager bekommt von Kickbase dasselbe, also hat er keinen Grund."""
    est = ex.estimate_bid_success(kind="manager", amount=6_340_000, market_value=6_300_000,
                                  listing_price=5_000_000)
    assert est.p <= 0.2 and "Marktwert" in est.why


def test_manager_listing_below_market_value_is_blocked():
    """Unter Marktwert lehnt die API sogar mit err 5080 ab — das Gebot existiert nie."""
    est = ex.estimate_bid_success(kind="manager", amount=4_800_000, market_value=9_300_000,
                                  listing_price=4_800_000)
    assert est.p <= 0.05


def test_manager_listing_with_real_overpay_is_realistic():
    """Hoffmeier-Muster 21.08.: +19 % über Marktwert wurde angenommen."""
    est = ex.estimate_bid_success(kind="manager", amount=8_700_000, market_value=7_300_000,
                                  listing_price=7_280_000)
    assert est.p >= 0.45


def test_estimate_is_serializable_for_payload():
    est = ex.estimate_bid_success(kind="kickbase", amount=1_000, market_value=900,
                                  listing_price=900)
    d = est.as_dict()
    assert set(d) >= {"p", "why", "kind"} and isinstance(d["p"], float)


# ------------------------------------------------------------------ Auswertung


def test_settle_marks_won_when_player_is_in_squad(session):
    row = _bid(session, "99")
    settled = ex.settle_bid_expectations(session, squad_pids={"99"}, open_bid_pids=set(), now=NOW)
    assert [s.result for s in settled] == ["won"]
    session.refresh(row)
    assert row.payload["outcome"]["result"] == "won"


def test_settle_keeps_disappeared_bid_unresolved_without_receipt(session):
    row = _bid(session, "77")
    settled = ex.settle_bid_expectations(session, squad_pids=set(), open_bid_pids=set(), now=NOW)
    assert settled == []
    session.refresh(row)
    assert row.payload["outcome"]["result"] == "unresolved"


def test_settle_skips_still_open_bids(session):
    """Ein laufendes Gebot ist weder gewonnen noch verloren — nichts schreiben."""
    row = _bid(session, "55")
    open_now = ex.settle_bid_expectations(
        session, squad_pids=set(), open_bid_pids={"55"}, now=NOW
    )
    assert open_now == []
    session.refresh(row)
    assert "outcome" not in (row.payload or {})


def test_settle_is_idempotent(session):
    _bid(session, "99")
    ex.settle_bid_expectations(session, squad_pids={"99"}, open_bid_pids=set(), now=NOW)
    again = ex.settle_bid_expectations(session, squad_pids={"99"}, open_bid_pids=set(), now=NOW)
    assert again == []


def test_settle_ignores_bids_without_expectation(session):
    """Altbestand ohne Erwartung bleibt unangetastet (kein erfundenes Ergebnis)."""
    _bid(session, "42", p=None)
    untouched = ex.settle_bid_expectations(
        session, squad_pids={"42"}, open_bid_pids=set(), now=NOW
    )
    assert untouched == []


# ------------------------------------------------------------------ Kalibrierung


def test_calibration_stays_silent_below_minimum_sample(session):
    """Vier Gebote sind keine Statistik — lieber nichts sagen als Scheingenauigkeit."""
    for i in range(4):
        _bid(session, f"a{i}", p=0.9, outcome="lost")
    report = ex.calibration(session, now=NOW, days=30)
    assert report.total == 4
    assert ex.format_calibration(report) is None


def test_calibration_flags_systematic_overconfidence(session):
    """Sechs Gebote mit 90 % Erwartung, alle verloren ⇒ die Formel ist zu optimistisch."""
    for i in range(6):
        _bid(session, f"b{i}", p=0.9, outcome="lost")
    report = ex.calibration(session, now=NOW, days=30)
    assert report.total == 6 and report.won == 0
    assert report.expected_rate > report.actual_rate
    text = ex.format_calibration(report)
    assert text is not None and "zu optimistisch" in text


def test_calibration_is_quiet_when_expectation_matches(session):
    """Neun von zehn gewonnen bei 90 % Erwartung — das passt, kein Alarm."""
    for i in range(9):
        _bid(session, f"c{i}", p=0.9, outcome="won")
    _bid(session, "c9", p=0.9, outcome="lost")
    report = ex.calibration(session, now=NOW, days=30)
    assert abs(report.expected_rate - report.actual_rate) < 0.15
    assert ex.format_calibration(report) is None


def test_calibration_groups_by_listing_kind(session):
    """Getrennt nach Kickbase/Manager — sonst mischen sich zwei völlig
    verschiedene Wetten zu einer nichtssagenden Zahl."""
    # Achtung: JSON-Spalten NICHT in-place mutieren — SQLAlchemy erkennt das nicht
    # und committet stillschweigend nichts (dieselbe Falle wie im Produktionscode,
    # wo settle_bid_expectations das payload deshalb komplett neu zuweist).
    for i in range(3):
        r = _bid(session, f"k{i}", p=0.9, outcome="won")
        r.payload = {**r.payload, "expectation": {**r.payload["expectation"], "kind": "kickbase"}}
    for i in range(3):
        r = _bid(session, f"m{i}", p=0.5, outcome="lost")
        r.payload = {**r.payload, "expectation": {**r.payload["expectation"], "kind": "manager"}}
    session.commit()
    report = ex.calibration(session, now=NOW, days=30)
    kinds = {g.kind: g for g in report.groups}
    assert kinds["kickbase"].won == 3 and kinds["manager"].won == 0


def test_backfilled_expectations_never_enter_the_rate(session):
    """Nachgetragene Erwartungen zaehlen nicht mit (28.08.2026).

    Vier der ersten sieben Zahlen wurden nicht zur Tatzeit geschrieben, sondern
    spaeter rekonstruiert — teils als der Ausgang schon feststand, und alle mit
    p = 0.9 samt der Begruendung „bisher einziger Bieter": genau der Annahme, die
    sich als haltlos erwies. Eine Kalibrierung, die so etwas mitzaehlt, misst
    nicht die Formel, sondern das eigene Gedaechtnis.
    """
    for i in range(6):
        _bid(session, f"echt{i}", p=0.5, outcome="won")
    for i in range(4):
        row = _bid(session, f"spaet{i}", p=0.9, outcome="lost")
        row.payload = {**row.payload,
                       "expectation": {**row.payload["expectation"], "backfilled": True}}
    session.commit()

    report = ex.calibration(session, now=NOW, days=30)
    assert report.total == 6, "nachgetragene Zeilen sind in die Stichprobe geraten"
    assert report.won == 6 and report.backfilled == 4
    # Ohne den Ausschluss waere die erwartete Quote durch die 0.9er verzerrt.
    assert abs(report.expected_rate - 0.5) < 0.01


def test_the_message_names_the_excluded_bids(session):
    """Ihr Fehlen muss erklaerbar sein, sonst wirkt die Stichprobe willkuerlich klein."""
    for i in range(8):
        _bid(session, f"echt{i}", p=0.9, outcome="lost")
    row = _bid(session, "spaet", p=0.9, outcome="won")
    row.payload = {**row.payload,
                   "expectation": {**row.payload["expectation"], "backfilled": True}}
    session.commit()

    text = ex.format_calibration(ex.calibration(session, now=NOW, days=30))
    assert text is not None
    assert "nachgetragener Erwartung" in text and "keine Vorhersage" in text
