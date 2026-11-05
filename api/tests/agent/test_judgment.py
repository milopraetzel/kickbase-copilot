"""Urteil im Einzelfall: der Agent entscheidet, der Code erzwingt nur die Hürden.

Der teuerste Ausgang darf nie der bequemste sein. Deshalb prüfen die meisten Tests
hier nicht, ob eine Änderung durchkommt, sondern ob sie es NICHT tut.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.agent import judgment as jd
from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.knowledge import tuning
from kickbase_agent.worker import surprise_job as sj
from kickbase_agent.worker.surprise_job import Surprise

NOW = datetime(2026, 8, 26, 6, 40, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _surprise(ref: str = "action:1", kind: str = "kickbase") -> Surprise:
    return Surprise(
        art="gebot", ref=ref, kind=kind, richtung="zu_optimistisch",
        title="Gebot 4711: erwartet 90 % Zuschlag, verloren",
        detail="2,93 M = 108 % MW", at=NOW,
        evidence={"expectation": {"p": 0.9, "why": "einziger Bieter",
                                  "kind": kind, "ratio": 1.08},
                  "outcome": {"result": "lost", "at": NOW.isoformat()},
                  "player_id": "4711", "amount": 2_930_000},
    )


def _answer(**over) -> str:
    data = {
        "ausgang": "parameter",
        "erklaerung": "Die Formel war zu selbstsicher.",
        "mechanismus": None,
        "recherche": [{"quelle": "Activity-Feed", "datum": "2026-08-26",
                       "aussage": "Lukas M. zahlte 157 % MW"}],
        "stellschraube": "expectation.kickbase_thin_p",
        "neuer_wert": 0.40,
        "beleg": "surprise:action:1",
        "learning": "Einziger Bieter ist eine Momentaufnahme.",
    }
    data.update(over)
    return json.dumps(data, ensure_ascii=False)


# ----------------------------------------------------------------- Parsen


def test_a_clean_answer_is_parsed():
    v = jd.parse_verdict(_answer())
    assert v.ausgang == "parameter" and v.stellschraube == "expectation.kickbase_thin_p"
    assert v.neuer_wert == 0.40 and v.recherche


def test_a_fenced_answer_is_still_parsed():
    v = jd.parse_verdict("```json\n" + _answer(ausgang="code") + "\n```")
    assert v.ausgang == "code"


def test_broken_json_falls_back_to_einzelfall():
    """Der teure Ausgang ist nie der Standardausgang eines Parsing-Fehlers."""
    v = jd.parse_verdict("{kaputt")
    assert v.ausgang == "einzelfall" and v.herabgestuft


def test_an_unknown_outcome_falls_back_to_einzelfall():
    v = jd.parse_verdict(json.dumps({"ausgang": "deploy", "erklaerung": "x"}))
    assert v.ausgang == "einzelfall"


# ----------------------------------------------- Zwei-Treffer und Mechanismus


def test_a_single_case_without_a_mechanism_is_downgraded():
    v = jd.enforce(jd.parse_verdict(_answer()), similar_count=1)
    assert v.ausgang == "einzelfall"
    assert "zweiter Fall" in v.herabgestuft


def test_two_cases_of_the_same_fingerprint_allow_the_change():
    v = jd.enforce(jd.parse_verdict(_answer()), similar_count=2)
    assert v.ausgang == "parameter" and not v.herabgestuft


def test_a_mechanism_carries_a_single_case():
    """Ein Mechanismus gilt immer — eine Tendenz nur diese Woche."""
    answer = _answer(mechanismus="Das Feld ofc zeigt nur AKTUELLE Bieter; "
                                 "Spaeteinsteiger sind vorher unsichtbar.")
    v = jd.enforce(jd.parse_verdict(answer), similar_count=1)
    assert v.ausgang == "parameter" and v.einzelfall_ausnahme is True


def test_a_tendency_dressed_as_a_mechanism_is_too_short_to_count():
    v = jd.enforce(jd.parse_verdict(_answer(mechanismus="teuer")), similar_count=1)
    assert v.ausgang == "einzelfall"


def test_the_code_route_obeys_the_same_two_locks():
    v = jd.enforce(jd.parse_verdict(_answer(ausgang="code")), similar_count=1)
    assert v.ausgang == "einzelfall"


# ----------------------------------------------------------------- Recherche


def test_a_change_without_research_is_downgraded():
    v = jd.enforce(jd.parse_verdict(_answer(recherche=[])), similar_count=3)
    assert v.ausgang == "einzelfall" and "Recherche" in v.herabgestuft


def test_an_explicit_quiet_market_counts_as_research():
    answer = _answer(recherche=[], markt_unauffaellig="LigaInsider und Presse ohne "
                                                     "Meldung zu diesem Spieler.")
    v = jd.enforce(jd.parse_verdict(answer), similar_count=3)
    assert v.ausgang == "parameter"


def test_a_parameter_change_needs_a_known_screw():
    v = jd.enforce(
        jd.parse_verdict(_answer(stellschraube="erfundener_name")), similar_count=3
    )
    assert v.ausgang == "einzelfall" and "Stellschraube" in v.herabgestuft


def test_a_parameter_change_needs_a_value():
    v = jd.enforce(jd.parse_verdict(_answer(neuer_wert=None)), similar_count=3)
    assert v.ausgang == "einzelfall"


# ----------------------------------------------------------------- Kontext


def test_context_carries_expectation_reality_and_current_values(session):
    context = jd.build_context(session, _surprise(), now=NOW)
    assert context["ueberraschung"]["fingerprint"] == "gebot|kickbase|zu_optimistisch"
    assert context["erwartung"]["p"] == 0.9
    assert context["wirklichkeit"]["result"] == "lost"
    assert "expectation.kickbase_thin_p" in context["stellschrauben"]
    assert context["vergleichsfaelle"] == []
    assert "vorschlag" not in context  # Material, keine Meinung


def test_context_finds_comparable_old_cases(session):
    """Gleicher Fingerabdruck, letzte 21 Tage — das ist die Zwei-Treffer-Grundlage."""
    for i, days in enumerate((3, 10, 40)):
        session.add(TransferAction(
            league_id="L1", action_type="bid", player_id=f"p{i}", amount=1_000_000,
            status="executed", idempotency_key=f"k{i}", dry_run=False,
            created_at=NOW - timedelta(days=days),
            payload={"expectation": {"p": 0.9, "why": "w", "kind": "kickbase"},
                     "outcome": {"result": "lost",
                                 "at": (NOW - timedelta(days=days)).isoformat()}},
        ))
    session.commit()

    context = jd.build_context(session, _surprise(ref="action:99"), now=NOW)

    assert len(context["vergleichsfaelle"]) == 2  # der 40 Tage alte faellt raus
    assert context["gleichartige_faelle"] == 3    # inkl. des aktuellen Falls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "p,result,high,low,expected",
    [
        (0.55, "lost", 0.60, 0.20, "einzelfall"),
        (0.30, "won", 0.60, 0.20, "einzelfall"),
        (0.70, "lost", 0.80, 0.10, "einzelfall"),
        (0.15, "won", 0.80, 0.10, "einzelfall"),
        (0.60, "lost", 0.60, 0.20, "parameter"),
        (0.20, "won", 0.60, 0.20, "parameter"),
    ],
)
async def test_second_case_must_meet_the_real_surprise_thresholds(
    session, monkeypatch, p, result, high, low, expected,
):
    """Ein normales Ergebnis darf die Zwei-Treffer-Sicherung nicht öffnen."""
    monkeypatch.setattr(tuning, "_cache", {
        **tuning.DEFAULTS, "surprise.high_p": high, "surprise.low_p": low,
    })
    for i, probability in enumerate((0.99 if result == "lost" else 0.01, p)):
        session.add(TransferAction(
            league_id="L1", action_type="bid", player_id=f"p{i}", amount=1_000_000,
            status="executed", idempotency_key=f"threshold{i}", dry_run=False,
            created_at=NOW,
            payload={"expectation": {"p": probability, "kind": "kickbase"},
                     "outcome": {"result": result, "at": NOW.isoformat()}},
        ))
    session.commit()
    case = sj.find_surprises(session, now=NOW)[0]

    async def answer(prompt):
        return _answer()

    verdict = await jd.judge_surprise(session, case, now=NOW, ask=answer)
    assert verdict.ausgang == expected


def test_context_names_the_winner_from_the_feed(session):
    feed = [{"t": 15, "data": {"byr": "Lukas M.", "slr": "Kickbase",
                               "pi": "4711", "trp": 4_230_000}}]
    context = jd.build_context(session, _surprise(), now=NOW, feed=feed)
    assert context["wirklichkeit"]["kaeufer"] == "Lukas M."
    assert context["wirklichkeit"]["preis"] == 4_230_000


def test_a_missing_feed_does_not_invalidate_the_context(session):
    context = jd.build_context(session, _surprise(), now=NOW, feed=None)
    assert "kaeufer" not in context["wirklichkeit"]


def test_prompt_contains_the_case_and_asks_for_research(session):
    prompt = jd.format_prompt(jd.build_context(session, _surprise(), now=NOW))
    assert "gebot|kickbase|zu_optimistisch" in prompt
    assert "recherche" in prompt.lower()


# ----------------------------------------------------------------- Ende zu Ende


@pytest.mark.asyncio
async def test_judge_records_the_verdict_on_the_audit_row(session):
    row = TransferAction(
        league_id="L1", action_type="bid", player_id="4711", amount=2_930_000,
        status="executed", idempotency_key="k1", dry_run=False, created_at=NOW,
        payload={"expectation": {"p": 0.9, "why": "w", "kind": "kickbase"},
                 "outcome": {"result": "lost", "at": NOW.isoformat()}},
    )
    session.add(row)
    session.commit()

    async def fake_ask(prompt, **kw):
        return _answer()

    verdict = await jd.judge_surprise(
        session, _surprise(ref=f"action:{row.id}"), now=NOW, ask=fake_ask
    )

    assert verdict.ausgang == "einzelfall"  # nur ein Fall, kein Mechanismus
    session.refresh(row)
    assert row.payload["surprise"]["urteil"] == "einzelfall"


@pytest.mark.asyncio
async def test_an_unreachable_cli_leaves_the_case_open(session):
    """Ohne Urteil wird NICHT geraten — und der Fall darf nicht als erledigt gelten."""
    row = TransferAction(
        league_id="L1", action_type="bid", player_id="4711", amount=1_000_000,
        status="executed", idempotency_key="k1", dry_run=False, created_at=NOW,
        payload={"expectation": {"p": 0.9, "why": "w", "kind": "kickbase"},
                 "outcome": {"result": "lost", "at": NOW.isoformat()}},
    )
    session.add(row)
    session.commit()

    async def boom(prompt, **kw):
        raise jd.ClaudeCliError("nicht angemeldet")

    with pytest.raises(jd.ClaudeCliError):
        await jd.judge_surprise(
            session, _surprise(ref=f"action:{row.id}"), now=NOW, ask=boom
        )

    session.refresh(row)
    assert "surprise" not in (row.payload or {})


@pytest.mark.asyncio
async def test_missing_cli_has_an_actionable_runtime_error(monkeypatch):
    async def missing_cli(*args, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(jd.asyncio, "create_subprocess_exec", missing_cli)
    with pytest.raises(jd.ClaudeCliError, match="installiert"):
        await jd.ask_claude("Ein lokaler Testfall")


def test_verdict_line_states_the_outcome_and_the_reason():
    v = jd.enforce(jd.parse_verdict(_answer()), similar_count=1)
    line = v.as_line()
    assert "einzelfall" in line.lower() and "zweiter Fall" in line
