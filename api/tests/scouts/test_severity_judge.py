"""Verletzungs-Schwere per TypeSafe — echte Antwort als Fixture, Wortsuche als Rückfall.

Das Fixture ist eine echte TypeSafe-Antwort (jev-1.13.0, 29.09.2026) auf die
LigaInsider-Ausfallgründe dieses Tages. Die Tests prüfen, dass der Code genau diese
Fragen stellt (gleiche IDs, gleiche Gründe im State) und die Antworten so übersetzt,
dass nur ein klarer Monate-Ausfall LONG (= Sofortverkauf) wird.
"""

import json
from pathlib import Path

import pytest
from typesafe_sdk import SystemOneResponse, TypeSafeAPIConnectionError

from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.scouts.ligainsider import severity_judge
from kickbase_agent.scouts.ligainsider.availability import apply_to_candidates
from kickbase_agent.scouts.ligainsider.models import Availability, Status
from kickbase_agent.scouts.ligainsider.severity import Severity
from kickbase_agent.scouts.ligainsider.severity_judge import judge_severities

_FIX = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "typesafe" / "severity_response.json")
    .read_text(encoding="utf-8")
)
_REASONS: list[str] = _FIX["reasons"]
_RESPONSE = SystemOneResponse.model_validate_json(json.dumps(_FIX["response"]))


@pytest.fixture(autouse=True)
def _empty_cache():
    severity_judge.clear_cache()
    yield
    severity_judge.clear_cache()


class _RecordingAsk:
    """Gibt die echte Antwort zurück und merkt sich, was gefragt wurde."""

    def __init__(self, response: SystemOneResponse = _RESPONSE) -> None:
        self.response = response
        self.calls: list[tuple[object, dict]] = []

    async def __call__(self, state, questions):
        self.calls.append((state, dict(questions)))
        return self.response


# So kommen die Gründe aus availability.py: mit „LI: "-Präfix.
_LI = [f"LI: {r}" for r in _REASONS]


async def test_asks_all_questions_for_all_reasons_in_one_request():
    ask = _RecordingAsk()
    await judge_severities(_LI, ask=ask)

    assert len(ask.calls) == 1  # alle Gründe in EINER Anfrage
    state, questions = ask.calls[0]
    # Der Code stellt genau die Fragen, auf die die echte Antwort passt.
    assert set(questions) == set(_RESPONSE.answers)
    # Im State stehen die Gründe ohne „LI: "-Präfix.
    assert list(state["labels"].values()) == _REASONS


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        # Klar Monate → Sofortverkauf bleibt.
        ("Kreuzbandriss", Severity.LONG),
        ("Schien- und Wadenbeinbruch", Severity.LONG),
        ("Sehnenriss Achillessehne", Severity.LONG),
        # Monate wahrscheinlich, aber keine gesicherte Diagnose (Riss/Bruch/OP) im Grund
        # → kein Sofortverkauf, bis LigaInsider die Diagnose nennt (Emil 29.09.2026).
        ("Achillessehnenverletzung", Severity.UNKNOWN),
        ("Kreuzbandverletzung", Severity.UNKNOWN),
        # Die Wortsuche machte daraus LONG („riss") — 3–8 Wochen sind kein Sofortverkauf.
        ("Muskelbündelriss", Severity.UNKNOWN),
        ("Muskelfaserriss", Severity.UNKNOWN),
        ("Kapselriss im Sprunggelenk", Severity.UNKNOWN),
        # Modell unsicher bzw. Grund unbestimmt → beobachten, nicht verkaufen.
        ("Stoffwechselstörung", Severity.UNKNOWN),
        ("Aufbautraining", Severity.UNKNOWN),
        ("Ausfall", Severity.UNKNOWN),
        ("Nicht berücksichtigt", Severity.UNKNOWN),
        # Kurz → halten.
        ("Oberschenkelzerrung", Severity.SHORT),
        ("Gelbsperre", Severity.SHORT),
        ("Erkältung", Severity.SHORT),
        ("Muskelverletzung", Severity.SHORT),
    ],
)
async def test_real_answers_map_to_severity(reason, expected):
    result = await judge_severities(_LI, ask=_RecordingAsk())
    assert result[f"LI: {reason}"] is expected


async def test_api_failure_falls_back_to_keyword_rule():
    async def broken(state, questions):
        raise TypeSafeAPIConnectionError("keine Verbindung")

    result = await judge_severities(["LI: Muskelbündelriss", "LI: Erkältung"], ask=broken)

    # Genau das Altverhalten der Wortsuche — kein Ausfall, kein Raten.
    assert result == {"LI: Muskelbündelriss": Severity.LONG, "LI: Erkältung": Severity.SHORT}


async def test_without_api_key_uses_keyword_rule(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    result = await judge_severities(["LI: Muskelbündelriss"])
    assert result == {"LI: Muskelbündelriss": Severity.LONG}


async def test_answered_reasons_are_not_asked_again():
    await judge_severities(_LI, ask=_RecordingAsk())

    async def must_not_be_called(state, questions):
        raise AssertionError("bereits beurteilter Grund wurde erneut gefragt")

    result = await judge_severities(["LI: Muskelbündelriss"], ask=must_not_be_called)
    assert result == {"LI: Muskelbündelriss": Severity.UNKNOWN}


async def test_keyword_fallback_is_not_cached(monkeypatch):
    async def broken(state, questions):
        raise TypeSafeAPIConnectionError("keine Verbindung")

    await judge_severities(["LI: Muskelbündelriss"], ask=broken)
    # Review 29.09.2026: Nach einem Ausfall pausiert TypeSafe kurz (kein 40-s-Warten
    # je Planlauf). Nach der Pause wird wieder gefragt — der Rückfall war nie im Cache.
    monkeypatch.setattr(severity_judge, "_pause_until", 0.0)
    ask = _RecordingAsk()
    await judge_severities(_LI, ask=ask)
    assert len(ask.calls) == 1  # nach dem Ausfall wird wieder gefragt


async def test_a_hanging_answer_is_cut_off_hard(monkeypatch):
    # Review 29.09.2026: Der SDK-Timeout gilt je HTTP-Schritt; Retry und tröpfelnde
    # Antworten dehnten einen Ausfall auf bis zu 40 s je Planlauf.
    import asyncio
    import time

    monkeypatch.setattr(severity_judge, "TIMEOUT_S", 0.05)

    async def hanging(state, questions):
        await asyncio.sleep(5)

    started = time.monotonic()
    result = await judge_severities(["LI: Muskelbündelriss"], ask=hanging)

    assert time.monotonic() - started < 1.0
    assert result == {"LI: Muskelbündelriss": Severity.LONG}  # Wortsuche wie bisher


async def test_after_an_outage_typesafe_pauses_before_asking_again():
    calls = []

    async def broken(state, questions):
        calls.append(1)
        raise TypeSafeAPIConnectionError("keine Verbindung")

    await judge_severities(["LI: Muskelbündelriss"], ask=broken)
    result = await judge_severities(["LI: Erkältung"], ask=broken)

    assert len(calls) == 1  # zweiter Lauf fragt während der Pause nicht
    assert result == {"LI: Erkältung": Severity.SHORT}


async def test_invalid_numbers_fall_back_for_that_reason_only():
    # Review 29.09.2026: NaN öffnete die Schwellen (NaN < 0.5 ist falsch), p > 1 hätte
    # LONG ergeben können — und wäre gecacht worden.
    from types import SimpleNamespace

    good_months = SimpleNamespace(probabilities={0: 0.0, 1: 0.0, 2: 0.0, 3: 1.0},
                                  confidence=0.99)
    broken_months = SimpleNamespace(probabilities={0: 0.0, 1: 0.0, 2: 0.0, 3: 1.5},
                                    confidence=float("nan"))
    sure = SimpleNamespace(noul=0.98)
    response = SimpleNamespace(
        scores={"dur_r0": good_months, "dur_r1": broken_months},
        nouls={"spec_r0": sure, "diag_r0": sure,
               "spec_r1": SimpleNamespace(noul=float("nan")), "diag_r1": sure},
    )

    async def ask(state, questions):
        return response

    result = await judge_severities(["LI: Knieprobleme", "LI: Muskelbündelriss"], ask=ask)

    assert result["LI: Knieprobleme"] is Severity.LONG  # gültiges Urteil zählt
    assert result["LI: Muskelbündelriss"] is Severity.LONG  # Wortsuche, kein Urteil
    assert severity_judge.cached_severity("LI: Muskelbündelriss") is None


async def test_squad_alarm_follows_the_typesafe_verdict():
    # Review 29.09.2026: Der Kader-Alarm riet mit der Wortsuche „SOFORT verkaufen",
    # während die Engine nach dem TypeSafe-Urteil hielt.
    from kickbase_agent.scouts.ligainsider.models import InjuryEntry
    from kickbase_agent.worker.newsfeed import NewsBundle, build_conclusions

    bundle = NewsBundle(
        injuries=[InjuryEntry("2", "Minjae Kim", "Muskelbündelriss")],
        squad_names=["Minjae"],
    )
    assert "SOFORT verkaufen" in build_conclusions(bundle)  # ohne Urteil: Wortsuche

    await judge_severities(_LI, ask=_RecordingAsk())

    text = build_conclusions(bundle)
    assert "Kader-Alarm" in text and "SOFORT verkaufen" not in text
    assert "beobachten" in text


async def test_empty_input_makes_no_request():
    ask = _RecordingAsk()
    assert await judge_severities([], ask=ask) == {}
    assert ask.calls == []


def test_apply_to_candidates_prefers_judged_severity():
    cands = [ExplosionCandidate(player_id="p1", name="X", team_id="3", position=2,
                                current_market_value=1, total_score=0.8, signals=[])]
    avails = [Availability("p1", Status.OUT, 0.0, "LI: Muskelbündelriss", 1.0)]

    out = apply_to_candidates(cands, avails, {"LI: Muskelbündelriss": Severity.UNKNOWN})

    assert out[0].injury_severity == "unknown"  # Wortsuche hätte "long" gesagt


def test_apply_to_candidates_without_judgment_keeps_keyword_rule():
    cands = [ExplosionCandidate(player_id="p1", name="X", team_id="3", position=2,
                                current_market_value=1, total_score=0.8, signals=[])]
    avails = [Availability("p1", Status.OUT, 0.0, "LI: Muskelbündelriss", 1.0)]

    assert apply_to_candidates(cands, avails)[0].injury_severity == "long"


async def test_enrich_candidates_uses_typesafe_judgment(monkeypatch):
    from kickbase_agent.scouts.ligainsider import service
    from kickbase_agent.scouts.ligainsider.models import InjuryEntry
    from kickbase_agent.scouts.ligainsider.scraper import ScoutSnapshot

    snap = ScoutSnapshot(injuries=[InjuryEntry("3", "Emre Can", "Muskelbündelriss")])

    async def fake_snapshot():
        return snap

    monkeypatch.setattr(service, "fetch_snapshot", fake_snapshot)
    # Erst die echte Antwort für alle Fixture-Gründe holen (füllt den Cache) …
    await judge_severities(_LI, ask=_RecordingAsk())
    cands = [ExplosionCandidate(player_id="p1", name="Emre Can", team_id="3", position=2,
                                current_market_value=1, total_score=0.8, signals=[])]

    result = await service.enrich_candidates(cands)

    # … dann nutzt der Service-Pfad dieses Urteil statt der Wortsuche („long").
    assert result.candidates[0].injury_severity == "unknown"


# ------------------------------------------------------------------ Jev-Protokoll


async def test_every_asked_reason_is_journaled_with_rule_and_outcome(jev_rows):
    from datetime import UTC, datetime, timedelta

    await judge_severities(_LI, ask=_RecordingAsk(), purpose="kader")

    assert len(jev_rows) == len(_REASONS)  # eine Zeile je Grund (drei Fragen)
    assert {(r.kind, r.purpose, r.status, r.source, r.batch_size) for r in jev_rows} == {
        ("schwere", "kader", "ok", "LigaInsider", len(_REASONS))
    }
    by_reason = {r.subject: r for r in jev_rows}
    long = by_reason["Kreuzbandriss"]
    assert (long.answer, long.outcome) == ("monate", "sofortverkauf_moeglich")
    assert long.probability == pytest.approx(1.0)
    assert set(long.probabilities) == {
        "tage", "wochen_1_3", "wochen_3_8", "monate", "bestimmt", "diagnose"
    }
    assert long.probabilities["diagnose"] == pytest.approx(0.96)
    assert "0,80" in long.rule
    assert {k[:5] for k in long.raw} == {"dur_r", "spec_", "diag_"}
    assert (by_reason["Muskelbündelriss"].answer, by_reason["Muskelbündelriss"].outcome) == (
        "wochen_3_8", "beobachten"
    )
    assert by_reason["Erkältung"].outcome == "halten"
    assert datetime.now(UTC) - long.created_at < timedelta(minutes=1)


async def test_the_journal_names_the_players_behind_a_reason(jev_rows):
    await judge_severities(
        ["LI: Kreuzbandriss", "LI: Erkältung"], ask=_RecordingAsk(),
        subjects={"LI: Kreuzbandriss": [("p1", "Max Muster")],
                  "LI: Erkältung": [("p2", "Ann A"), ("p3", "Bob B")]},
    )
    names = {r.subject: (r.player_id, r.player_name) for r in jev_rows}
    assert names["Kreuzbandriss"] == ("p1", "Max Muster")
    assert names["Erkältung"] == (None, "Ann A, Bob B")  # ein Grund, zwei Spieler


async def test_a_failed_severity_request_is_journaled_once(jev_rows):
    async def broken(state, questions):
        raise TypeSafeAPIConnectionError("keine Verbindung")

    await judge_severities(["LI: Muskelbündelriss", "LI: Erkältung"], ask=broken)
    assert [(r.kind, r.status, r.error, r.batch_size) for r in jev_rows] == [
        ("schwere", "ausfall", "TypeSafeAPIConnectionError", 2)
    ]


async def test_an_invalid_severity_answer_is_journaled_as_invalid(jev_rows):
    from types import SimpleNamespace

    broken_months = SimpleNamespace(probabilities={0: 0.0, 1: 0.0, 2: 0.0, 3: 1.5},
                                    confidence=float("nan"))
    sure = SimpleNamespace(noul=0.98)
    response = SimpleNamespace(scores={"dur_r0": broken_months},
                               nouls={"spec_r0": sure, "diag_r0": sure})

    async def ask(state, questions):
        return response

    await judge_severities(["LI: Knieprobleme"], ask=ask)
    assert [(r.status, r.outcome) for r in jev_rows] == [("ungueltig", "ungueltig")]


async def test_cached_reasons_are_not_journaled_again(jev_rows):
    await judge_severities(["LI: Kreuzbandriss"], ask=_RecordingAsk())
    await judge_severities(["LI: Kreuzbandriss"], ask=_RecordingAsk())
    assert len(jev_rows) == 1


async def test_enrich_candidates_journals_the_candidate_name(monkeypatch, jev_rows):
    from kickbase_agent.scouts.ligainsider import service
    from kickbase_agent.scouts.ligainsider.models import InjuryEntry
    from kickbase_agent.scouts.ligainsider.scraper import ScoutSnapshot

    snap = ScoutSnapshot(injuries=[InjuryEntry("3", "Emre Can", "Muskelbündelriss")])

    async def fake_snapshot():
        return snap

    async def fixture_ask(state, questions):
        return _RESPONSE

    monkeypatch.setattr(service, "fetch_snapshot", fake_snapshot)
    monkeypatch.setattr(severity_judge, "_default_ask", lambda: fixture_ask)
    cands = [ExplosionCandidate(player_id="p1", name="Emre Can", team_id="3", position=2,
                                current_market_value=1, total_score=0.8, signals=[])]

    await service.enrich_candidates(cands)

    assert [(r.player_id, r.player_name) for r in jev_rows] == [("p1", "Emre Can")]



@pytest.mark.parametrize(
    ("purpose", "outcome"),
    [("kader", "sofortverkauf_moeglich"), ("kaufpruefung", "monate_gesichert"),
     ("ausfall", "monate_gesichert")],
)
async def test_only_own_players_can_be_sold_on_a_long_verdict(jev_rows, purpose, outcome):
    """Review 01.10.: „Sofortverkauf möglich" stand auch bei Gründen, die nur Marktspieler
    betreffen — verkaufen kann die Engine aber nur eigene Spieler."""
    await judge_severities(_LI, ask=_RecordingAsk(), purpose=purpose)
    [row] = [r for r in jev_rows if r.subject == "Kreuzbandriss"]
    assert (row.purpose, row.outcome) == (purpose, outcome)


async def test_a_partial_answer_still_journals_what_was_judged(jev_rows):
    """Fehlt einer Antwort ein Schlüssel, bricht der Judge ab (Altverhalten) — das Protokoll
    zeigt trotzdem die schon beurteilten Gründe und den Ausfall."""
    from types import SimpleNamespace

    months = SimpleNamespace(probabilities={0: 0.0, 1: 0.0, 2: 0.0, 3: 1.0}, confidence=0.99)
    sure = SimpleNamespace(noul=0.98)
    response = SimpleNamespace(scores={"dur_r0": months, "dur_r1": months},
                               nouls={"spec_r0": sure, "diag_r0": sure, "spec_r1": sure})

    async def ask(state, questions):
        return response

    result = await judge_severities(["LI: Knieprobleme", "LI: Muskelbündelriss"], ask=ask)

    assert severity_judge.cached_severity("LI: Knieprobleme") is Severity.LONG
    assert result["LI: Muskelbündelriss"] is Severity.LONG  # Wortsuche wie bisher
    assert sorted((r.subject or "", r.status) for r in jev_rows) == [
        ("", "ausfall"), ("Knieprobleme", "ok"), ("Muskelbündelriss", "ungueltig"),
    ]


async def test_enrich_candidates_passes_its_purpose(monkeypatch, jev_rows):
    from kickbase_agent.scouts.ligainsider import service
    from kickbase_agent.scouts.ligainsider.models import InjuryEntry
    from kickbase_agent.scouts.ligainsider.scraper import ScoutSnapshot

    snap = ScoutSnapshot(injuries=[InjuryEntry("3", "Emre Can", "Kreuzbandriss")])

    async def fake_snapshot():
        return snap

    async def kreuzband_ask(state, questions):
        # Die echte Antwort für „Kreuzbandriss", umnummeriert auf die einzige Frage r0.
        from types import SimpleNamespace

        i = _REASONS.index("Kreuzbandriss")
        return SimpleNamespace(scores={"dur_r0": _RESPONSE.scores[f"dur_r{i}"]},
                               nouls={"spec_r0": _RESPONSE.nouls[f"spec_r{i}"],
                                      "diag_r0": _RESPONSE.nouls[f"diag_r{i}"]})

    monkeypatch.setattr(service, "fetch_snapshot", fake_snapshot)
    monkeypatch.setattr(severity_judge, "_default_ask", lambda: kreuzband_ask)
    cands = [ExplosionCandidate(player_id="p1", name="Emre Can", team_id="3", position=2,
                                current_market_value=1, total_score=0.8, signals=[])]

    await service.enrich_candidates(cands, purpose="kader")

    assert [(r.purpose, r.outcome) for r in jev_rows] == [("kader", "sofortverkauf_moeglich")]


async def test_a_broken_first_answer_never_journals_verdicts_that_did_not_count(jev_rows):
    """Nach-Review: Bricht die Teilantwort beim ersten Grund ab, gilt für ALLE Gründe die
    Wortsuche — das Protokoll darf kein Jev-Urteil zeigen, das nie galt."""
    from types import SimpleNamespace

    months = SimpleNamespace(probabilities={0: 0.0, 1: 0.0, 2: 0.98, 3: 0.0}, confidence=0.97)
    sure = SimpleNamespace(noul=0.98)
    response = SimpleNamespace(scores={"dur_r0": months, "dur_r1": months},
                               nouls={"spec_r0": sure, "spec_r1": sure, "diag_r1": sure})

    async def ask(state, questions):
        return response

    await judge_severities(["LI: Knieprobleme", "LI: Muskelbündelriss"], ask=ask)

    assert severity_judge.cached_severity("LI: Muskelbündelriss") is None
    judged = {r.subject: (r.status, r.outcome) for r in jev_rows if r.subject}
    assert judged == {"Knieprobleme": ("ungueltig", "ungueltig"),
                      "Muskelbündelriss": ("ungueltig", "ungueltig")}
