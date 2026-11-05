"""Daten der Jev-Seite (cli/cockpit/jev_data.py, Spec 2026-10-01 §2/§3).

Rein lesend aus `jev_judgments`, `player_performance` und `player_history`. Geprüft wird,
was Emil auf der Seite sieht: Status (auch Pause und fehlender Schlüssel), Zahlen der
letzten 7 Tage, das Protokoll, die Trennschärfe der letzten 30 Tage und die Bilanz.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kickbase_agent.cli.cockpit.jev_data import FEED_LIMIT, TEXT_MAX, read_jev
from kickbase_agent.db.models import Base, PlayerHistory, PlayerPerformance
from kickbase_agent.intel.jev_journal import JevEntry, failure_entry, write_entries

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
OCT1 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _row(**over) -> JevEntry:
    base = dict(
        created_at=OCT1 + timedelta(minutes=5), kind="rolle", purpose="kaufpruefung",
        status="ok", model="jev-1.13.0", batch_id="b1", batch_size=2, latency_ms=480,
        player_id="9605", player_name="Albert Grønbæk", club="Hamburger SV",
        subject="Bitter für den HSV: Top-Scorer Grönbaek fällt vorerst aus",
        text="Der HSV-Profi fällt vorerst aus.", url="https://www.kicker.de/g",
        published_at=OCT1, source="kicker", answer="fehlt", probability=0.99,
        probabilities={"fehlt": 0.99, "kein_hinweis": 0.01, "startet": 0.0, "bank": 0.0},
        confidence=0.99, raw={"role_p0": {"choice": "fehlt"}},
        rule="„fehlt“ ≥ 0,95 sperrt einen neuen Kauf", outcome="sperrt",
    )
    base.update(over)
    return JevEntry(**base)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    write_entries([
        _row(),
        _row(player_id="1883", player_name="Moritz Nicolas", answer="fehlt", probability=0.56,
             probabilities={"fehlt": 0.56, "kein_hinweis": 0.44}, outcome="kein_einfluss",
             subject="Nächster Rückschlag für Gladbach: Kühn fehlt wochenlang", batch_id="b1"),
        _row(kind="rueckkehr", purpose="kaufhinweis", player_id="43",
             player_name="Mitchell Weiser", answer="rueckkehr", probability=0.99,
             probabilities={"rueckkehr": 0.99, "ausfall": 0.0, "kein_ereignis": 0.01},
             outcome="kaufhinweis", subject="Weiser unmittelbar vor der Rückkehr",
             created_at=datetime(2026, 10, 1, 6, 11, tzinfo=UTC), batch_id="b2",
             latency_ms=900, batch_size=1),
        _row(kind="schwere", purpose="ausfall", player_id=None, player_name="Max Muster",
             subject="Kreuzbandriss", text=None, url=None, published_at=None,
             source="LigaInsider", answer="monate", probability=1.0,
             probabilities={"tage": 0.0, "wochen_1_3": 0.0, "wochen_3_8": 0.0, "monate": 1.0,
                            "bestimmt": 0.99, "diagnose": 0.96},
             outcome="sofortverkauf_moeglich", created_at=NOW - timedelta(days=2),
             batch_id="b3", latency_ms=1200, batch_size=2),
        _row(kind="schwere", purpose="ausfall", player_id=None, subject="Achillessehnenverletzung",
             answer="monate", probability=0.96,
             probabilities={"monate": 0.96, "diagnose": 0.1, "bestimmt": 0.98},
             outcome="beobachten", created_at=NOW - timedelta(days=2), batch_id="b3",
             latency_ms=1200, batch_size=2),
        _row(created_at=NOW - timedelta(days=40), batch_id="old", text="x" * 2000),
        failure_entry(kind="rolle", purpose="kaufpruefung", batch_size=3, latency_ms=10_000,
                      error=TimeoutError(), at=NOW - timedelta(minutes=2), batch_id="b4"),
    ], session_factory=factory)
    with factory() as s:
        s.add_all([
            PlayerPerformance(player_id="9605", season="2026/2027", matchday=5, points=0,
                              minutes=0, match_date=datetime(2026, 10, 9, 18, 30),
                              minutes_observed=True),
            PlayerPerformance(player_id="43", season="2026/2027", matchday=5, points=80,
                              minutes=23, match_date=datetime(2026, 10, 10, 13, 30),
                              minutes_observed=True),
            # Hinweis 01.10. 08:11 Berlin: Es gilt der Wert vom 30.09. (Update 22:00).
            PlayerHistory(player_id="43", recorded_at=datetime(2026, 9, 30),
                          market_value=5_000_000),
            PlayerHistory(player_id="43", recorded_at=datetime(2026, 10, 3),
                          market_value=5_300_000),
            PlayerHistory(player_id="43", recorded_at=datetime(2026, 10, 7),
                          market_value=5_600_000),
        ])
        s.commit()
        yield s


def test_a_recent_failure_shows_the_pause(session):
    data = read_jev(session, NOW, key_set=True)
    status = data["status"]
    assert status["state"] == "pause"
    pause_until = NOW - timedelta(minutes=2) + timedelta(seconds=600)
    assert status["pause_until"] == pause_until.isoformat()
    assert status["failures_24h"] == 1
    assert status["last_ok_at"] == (NOW - timedelta(days=2)).isoformat()


def test_status_is_ok_once_the_pause_is_over(session):
    assert read_jev(session, NOW + timedelta(minutes=15), key_set=True)["status"]["state"] == "ok"


def test_without_a_key_the_page_says_so(session):
    assert read_jev(session, NOW, key_set=False)["status"]["state"] == "kein_schluessel"


def test_an_empty_journal_is_named_not_hidden():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as s:
        data = read_jev(s, NOW, key_set=True)
    assert data["status"]["state"] == "noch_nichts"
    assert data["feed"] == [] and data["seit"] is None
    assert data["bilanz"]["rolle"]["summe"]["entschieden"] == 0


def test_counts_cover_the_last_seven_days(session):
    zahlen = read_jev(session, NOW, key_set=True)["zahlen"]
    # Nur die beiden Schwere-Fragen liegen in den letzten 7 Tagen; der Ausfall zählt extra.
    assert (zahlen["urteile_7d"], zahlen["folgen_7d"], zahlen["ausfaelle_7d"]) == (2, 1, 1)
    assert zahlen["anfragen_7d"] == 1  # eine Anfrage (b3) mit zwei Fragen
    assert zahlen["median_ms"] == 1200


def test_the_feed_is_newest_first_with_the_text_jev_read(session):
    data = read_jev(session, NOW, key_set=True)
    feed = data["feed"]
    assert feed[0]["status"] == "ausfall" and feed[0]["error"] == "TimeoutError"
    gron = next(e for e in feed
                if e["player"] == "Albert Grønbæk" and e["at"].startswith("2026-10-01"))
    assert gron["probabilities"]["fehlt"] == 0.99 and gron["outcome"] == "sperrt"
    assert gron["subject"].startswith("Bitter") and gron["url"] == "https://www.kicker.de/g"
    assert gron["raw"] == {"role_p0": {"choice": "fehlt"}}
    assert len(feed) <= FEED_LIMIT
    assert all(len(e["text"] or "") <= TEXT_MAX for e in feed)
    assert data["seit"] == (NOW - timedelta(days=40)).isoformat()


def test_calibration_shows_thirty_days_of_answers_per_question(session):
    kal = read_jev(session, NOW, key_set=True)["kalibrierung"]
    assert sorted((p["p"], p["hit"]) for p in kal["rolle"]) == [(0.56, False), (0.99, True)]
    assert [(p["p"], p["hit"]) for p in kal["rueckkehr"]] == [(0.99, True)]
    # Über der Monate-Schwelle, aber ohne gesicherte Diagnose: eigener Ring.
    assert sorted((p["p"], p["hit"], p["ring"]) for p in kal["schwere"]) == [
        (0.96, False, True), (1.0, True, False)
    ]


def test_the_bilanz_measures_clear_verdicts_against_the_next_match(session):
    bilanz = read_jev(session, NOW, key_set=True)["bilanz"]
    [rolle] = bilanz["rolle"]["checks"]
    assert (rolle["player"], rolle["verdict"], rolle["result"], rolle["minutes"]) == (
        "Albert Grønbæk", "fehlt", "treffer", 0
    )
    assert bilanz["rolle"]["summe"] == {"treffer": 1, "daneben": 0, "offen": 0, "entschieden": 1}
    [ret] = bilanz["rueckkehr"]["checks"]
    assert (ret["result"], ret["minutes"], ret["d3_pct"], ret["d7_pct"]) == (
        "treffer", 23, 6.0, 12.0
    )


def test_thresholds_come_from_the_judges(session):
    from kickbase_agent.intel import news_signal, role_judge
    from kickbase_agent.scouts.ligainsider import severity_judge

    schwellen = read_jev(session, NOW, key_set=True)["schwellen"]
    assert schwellen["rolle"]["fehlt"] == role_judge.MIN_P_ROLE
    assert schwellen["rueckkehr"] == {"rueckkehr": news_signal.MIN_P_RETURN,
                                      "ausfall": news_signal.MIN_P_CANCEL}
    assert schwellen["schwere"]["monate"] == severity_judge.LONG_MIN_P_MONTHS
    assert schwellen["schwere"]["diagnose"] == severity_judge.LONG_MIN_P_DIAGNOSIS


def test_a_broken_database_never_breaks_the_cockpit_build(caplog):
    from kickbase_agent.cli.cockpit.jev_data import read_jev_safely

    class Broken:
        def scalars(self, *_a, **_k):
            raise RuntimeError("no such table: jev_judgments")

        scalar = execute = scalars

    assert read_jev_safely(Broken(), NOW) is None
    assert "Jev-Seite ohne Daten" in caplog.text


def test_collect_hands_the_jev_data_to_the_page():
    """Wie bei der Lern-Bilanz: Was die Seite zeigt, muss aus collect() kommen."""
    import inspect

    from kickbase_agent.cli.cockpit import data

    source = inspect.getsource(data.collect)
    assert "read_jev_safely(session)" in source
    assert '"jev": jev' in source


def test_every_question_keeps_its_own_newest_rows_in_the_feed():
    """Eine große Rollen-Anfrage darf Rückkehr und Verletzung nicht aus dem Protokoll
    drängen — sonst zeigte der Filter „Rückkehr" nichts."""
    from kickbase_agent.cli.cockpit.jev_data import FEED_PER_KIND

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    old_return = _row(kind="rueckkehr", purpose="kaufhinweis", outcome="kaufhinweis",
                      created_at=NOW - timedelta(days=3), batch_id="r")
    # Ohne Folge: Folgen der letzten 7 Tage stehen immer im Protokoll (eigener Test).
    many_roles = [_row(created_at=NOW - timedelta(minutes=i), batch_id="big", player_id=str(i),
                       outcome="kein_einfluss")
                  for i in range(FEED_PER_KIND + 20)]
    write_entries([old_return, *many_roles], session_factory=factory)
    with factory() as s:
        feed = read_jev(s, NOW, key_set=True)["feed"]
    kinds = [e["kind"] for e in feed]
    assert kinds.count("rolle") == FEED_PER_KIND and kinds.count("rueckkehr") == 1
    assert [e["at"] for e in feed] == sorted((e["at"] for e in feed), reverse=True)


# ------------------------------------------------------------------ Nach dem Review


def _fresh_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def test_every_consequence_of_the_last_week_stays_in_the_feed():
    """Review 01.10.: Bei 33 Rollen-Fragen in einer Anfrage fiel ein „sperrt" aus den 25
    jüngsten Zeilen — Zahl „mit Folge" und Filter liefen auseinander."""
    from kickbase_agent.cli.cockpit.jev_data import FEED_PER_KIND

    factory = _fresh_factory()
    block = _row(created_at=NOW - timedelta(hours=1, seconds=1), player_id="blocked",
                 batch_id="big")
    quiet = [_row(created_at=NOW - timedelta(minutes=i), batch_id="big", player_id=str(i),
                  outcome="kein_einfluss") for i in range(FEED_PER_KIND + 5)]
    write_entries([block, *quiet], session_factory=factory)
    with factory() as s:
        data = read_jev(s, NOW, key_set=True)
    effects = [e for e in data["feed"] if e["outcome"] == "sperrt"]
    assert [e["player_id"] for e in effects] == ["blocked"]
    assert data["zahlen"]["folgen_7d"] == len(effects)


def test_a_block_lifted_by_a_newer_clear_article_is_marked():
    factory = _fresh_factory()
    older = _row(published_at=OCT1, created_at=OCT1 + timedelta(minutes=5))
    newer = _row(published_at=OCT1 + timedelta(hours=20), answer="bank", probability=0.97,
                 probabilities={"bank": 0.97}, outcome="eindeutig",
                 subject="Grönbaek zurück, zunächst Joker",
                 created_at=OCT1 + timedelta(hours=21))
    write_entries([older, newer], session_factory=factory)
    with factory() as s:
        feed = read_jev(s, NOW, key_set=True)["feed"]
    by_outcome = {e["outcome"]: e for e in feed}
    assert by_outcome["sperrt"]["aufgehoben"] is True
    assert by_outcome["eindeutig"]["aufgehoben"] is False


def test_a_radar_failure_without_pause_is_no_pause_on_the_page():
    """Der News-Radar fragt die Rolle ohne Ausfall-Pause (pause_on_failure=False)."""
    factory = _fresh_factory()
    write_entries([
        _row(created_at=NOW - timedelta(hours=2)),
        failure_entry(kind="rolle", purpose="kader", batch_size=3, latency_ms=10_000,
                      error=TimeoutError(), at=NOW - timedelta(minutes=2), batch_id="r"),
    ], session_factory=factory)
    with factory() as s:
        status = read_jev(s, NOW, key_set=True)["status"]
    assert status["state"] == "ok" and status["pausen"] == {}
    assert status["failures_24h"] == 1


def test_the_pause_is_tracked_per_question():
    """Scheitert die Schwere-Frage, pausiert sie — auch wenn die Rolle danach antwortet."""
    factory = _fresh_factory()
    failed_at = NOW - timedelta(minutes=3)
    write_entries([
        failure_entry(kind="schwere", purpose="ausfall", batch_size=4, latency_ms=10_000,
                      error=TimeoutError(), at=failed_at, batch_id="s"),
        _row(created_at=NOW - timedelta(minutes=1)),
    ], session_factory=factory)
    with factory() as s:
        status = read_jev(s, NOW, key_set=True)["status"]
    assert status["state"] == "pause"
    assert status["pausen"] == {"schwere": (failed_at + timedelta(seconds=600)).isoformat()}


def test_only_invalid_answers_are_not_called_working():
    factory = _fresh_factory()
    write_entries([_row(created_at=NOW - timedelta(minutes=5), status="ungueltig",
                        outcome="ungueltig", probability=None)], session_factory=factory)
    with factory() as s:
        data = read_jev(s, NOW, key_set=True)
    assert data["status"]["state"] == "ungueltig"
    assert data["status"]["last_ok_at"] is None
    assert data["zahlen"]["ungueltig_7d"] == 1


def test_minutes_without_an_observation_flag_count_like_in_the_plan():
    """L-111: Ältere Zeilen tragen NULL — nur ein ausdrückliches False ist „unbeobachtet"."""
    factory = _fresh_factory()
    write_entries([_row()], session_factory=factory)
    with factory() as s:
        s.add(PlayerPerformance(player_id="9605", season="2026/2027", matchday=5, points=0,
                                minutes=0, match_date=datetime(2026, 10, 9, 18, 30),
                                minutes_observed=None))
        s.commit()
        [check] = read_jev(s, NOW, key_set=True)["bilanz"]["rolle"]["checks"]
    assert check["result"] == "treffer"


def test_a_re_asked_pair_counts_only_with_its_newest_answer():
    """Review 01.10.: Nach einem Neustart erneut gefragt, nur noch 0,94 — die alte
    „sperrt"-Zeile darf nicht weiter in der Bilanz stehen."""
    factory = _fresh_factory()
    write_entries([
        _row(created_at=OCT1 + timedelta(minutes=5), probability=0.96),
        _row(created_at=OCT1 + timedelta(hours=2), probability=0.94, outcome="kein_einfluss"),
    ], session_factory=factory)
    with factory() as s:
        assert read_jev(s, NOW, key_set=True)["bilanz"]["rolle"]["checks"] == []


def test_a_lifted_block_is_not_measured():
    factory = _fresh_factory()
    write_entries([
        _row(),
        _row(published_at=OCT1 + timedelta(hours=20), answer="bank", probability=0.97,
             probabilities={"bank": 0.97}, outcome="eindeutig", subject="zunächst Joker",
             created_at=OCT1 + timedelta(hours=21)),
    ], session_factory=factory)
    with factory() as s:
        checks = read_jev(s, NOW, key_set=True)["bilanz"]["rolle"]["checks"]
    assert [c["verdict"] for c in checks] == ["bank"]


# ------------------------------------------------------------------ Nach-Review


def test_a_failed_squad_severity_request_pauses():
    """Nach-Review: Ohne Pause fragt nur der News-Radar die ROLLE; die Schwere mit Zweck
    „kader" (Plan) pausiert sehr wohl."""
    factory = _fresh_factory()
    failed_at = NOW - timedelta(minutes=2)
    write_entries([
        failure_entry(kind="schwere", purpose="kader", batch_size=3, latency_ms=10_000,
                      error=TimeoutError(), at=failed_at, batch_id="s"),
    ], session_factory=factory)
    with factory() as s:
        status = read_jev(s, NOW, key_set=True)["status"]
    assert status["pausen"] == {"schwere": (failed_at + timedelta(seconds=600)).isoformat()}


def test_the_effect_filter_uses_the_same_seven_days_as_the_count():
    factory = _fresh_factory()
    write_entries([
        _row(kind="rueckkehr", purpose="kaufhinweis", outcome="kaufhinweis", player_id="old",
             created_at=NOW - timedelta(days=10), batch_id="a"),
        _row(kind="rueckkehr", purpose="kaufhinweis", outcome="kaufhinweis", player_id="new",
             created_at=NOW - timedelta(days=1), batch_id="b"),
    ], session_factory=factory)
    with factory() as s:
        data = read_jev(s, NOW, key_set=True)
    wirkung = {e["player_id"]: e["wirkung"] for e in data["feed"]}
    assert wirkung == {"old": False, "new": True}
    assert data["zahlen"]["folgen_7d"] == sum(wirkung.values())


def test_a_secured_long_absence_of_a_market_player_is_a_ring_not_an_effect():
    factory = _fresh_factory()
    write_entries([_row(kind="schwere", purpose="kaufpruefung", outcome="monate_gesichert",
                        answer="monate", probability=0.97,
                        probabilities={"monate": 0.97, "diagnose": 0.95},
                        created_at=NOW - timedelta(days=1))], session_factory=factory)
    with factory() as s:
        [point] = read_jev(s, NOW, key_set=True)["kalibrierung"]["schwere"]
    assert (point["hit"], point["ring"]) == (False, True)


# ------------------------------------------------------------------ Nach-Review 2: je Spiel


def _perf(session, pid: str, kickoff: datetime, minutes: int) -> None:
    session.add(PlayerPerformance(
        player_id=pid, season="2026/2027", matchday=kickoff.day, points=0, minutes=minutes,
        match_date=kickoff.replace(tzinfo=None), minutes_observed=True,
        captured_at=(kickoff + timedelta(hours=26)).replace(tzinfo=None),
    ))


def test_a_verdict_about_a_later_match_never_lifts_an_earlier_one():
    """Nach-Review: „startet" fürs NÄCHSTE Spiel ließ den Treffer „fehlt" vom Spiel davor
    verschwinden. Je Spieler und Spiel zählt das jüngste klare Urteil vor dem Anpfiff."""
    factory = _fresh_factory()
    write_entries([
        _row(published_at=OCT1, created_at=OCT1 + timedelta(minutes=5)),
        _row(published_at=OCT1 + timedelta(days=7), answer="startet", probability=0.97,
             probabilities={"startet": 0.97}, outcome="eindeutig", subject="startet wieder",
             created_at=OCT1 + timedelta(days=7, minutes=5)),
    ], session_factory=factory)
    with factory() as s:
        _perf(s, "9605", datetime(2026, 10, 4, 18, 30, tzinfo=UTC), 0)
        s.commit()
        data = read_jev(s, NOW, key_set=True)
    checks = {c["verdict"]: c["result"] for c in data["bilanz"]["rolle"]["checks"]}
    assert checks == {"fehlt": "treffer", "startet": "offen"}
    assert not any(e["aufgehoben"] for e in data["feed"])


def test_only_the_newest_clear_verdict_before_a_match_is_measured():
    factory = _fresh_factory()
    write_entries([
        _row(published_at=OCT1, answer="startet", probability=0.97,
             probabilities={"startet": 0.97}, outcome="eindeutig", subject="startet",
             created_at=OCT1 + timedelta(minutes=1)),
        _row(published_at=OCT1 + timedelta(days=1), subject="fällt doch aus",
             created_at=OCT1 + timedelta(days=1, minutes=1)),
        _row(published_at=OCT1 + timedelta(days=2), subject="fällt sicher aus",
             created_at=OCT1 + timedelta(days=2, minutes=1)),
    ], session_factory=factory)
    with factory() as s:
        _perf(s, "9605", datetime(2026, 10, 4, 18, 30, tzinfo=UTC), 0)
        s.commit()
        data = read_jev(s, NOW, key_set=True)
    checks = data["bilanz"]["rolle"]["checks"]
    assert [(c["subject"], c["result"]) for c in checks] == [("fällt sicher aus", "treffer")]
    # Ein jüngeres „fehlt" bestätigt das ältere — aufgehoben ist da nichts.
    assert not any(e["aufgehoben"] for e in data["feed"])


def test_a_new_injury_weeks_later_does_not_cancel_a_return_that_happened():
    factory = _fresh_factory()
    later = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    write_entries([
        _row(kind="rueckkehr", purpose="kaufhinweis", outcome="kaufhinweis", answer="rueckkehr",
             player_id="43", subject="zurück im Training", published_at=OCT1,
             created_at=OCT1 + timedelta(minutes=5), batch_id="a"),
        _row(kind="rueckkehr", purpose="kaufhinweis", outcome="hebt_auf", answer="ausfall",
             player_id="43", subject="verletzt sich erneut",
             published_at=datetime(2026, 10, 20, 9, 0, tzinfo=UTC),
             created_at=datetime(2026, 10, 20, 9, 5, tzinfo=UTC), batch_id="b"),
    ], session_factory=factory)
    with factory() as s:
        _perf(s, "43", datetime(2026, 10, 4, 13, 30, tzinfo=UTC), 23)
        s.commit()
        data = read_jev(s, later, key_set=True)
    assert [(c["subject"], c["result"]) for c in data["bilanz"]["rueckkehr"]["checks"]] == [
        ("zurück im Training", "treffer")
    ]
    assert not any(e["aufgehoben"] for e in data["feed"])


def test_the_same_question_asked_again_is_one_feed_entry():
    """Live 01.10.: Jeder neue Prozess (Deploy, manueller Build, API) fragt mit leerem Cache
    erneut — im Protokoll stand dieselbe Frage viermal."""
    factory = _fresh_factory()
    write_entries([_row(created_at=NOW - timedelta(minutes=m), batch_id=f"b{m}",
                        outcome="kein_einfluss", answer="kein_hinweis") for m in (1, 9, 20)],
                  session_factory=factory)
    with factory() as s:
        feed = read_jev(s, NOW, key_set=True)["feed"]
    assert len(feed) == 1 and feed[0]["wiederholt"] == 3
    assert feed[0]["at"] == (NOW - timedelta(minutes=1)).isoformat()


def test_player_index_maps_latest_judgments_by_id_and_name(session):
    """Das Spieler-Dossier im Haupt-Cockpit liest `jev['spieler']` nach ID oder Name."""
    data = read_jev(session, NOW, key_set=True)
    spieler = data["spieler"]
    assert "9605" in spieler
    assert spieler["9605"][0]["answer"] == "fehlt"
    assert spieler["9605"][0]["url"] == "https://www.kicker.de/g"
    assert "43" in spieler
    assert spieler["43"][0]["kind"] == "rueckkehr"
    assert "name:max muster" in spieler
    assert spieler["name:max muster"][0]["kind"] == "schwere"

