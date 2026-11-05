"""Rolle aus News per TypeSafe (Emil 29.09.2026: „bau die Rolle mit Jev").

Das Fixture ist eine echte TypeSafe-Antwort (jev-1.13.0, 29.09.2026) auf echte
kicker-Artikel vom 30.08.–28.09.2026 und echte Kickbase-Spieler, darunter Namensvettern
und Alltagswörter („neuer" ≠ Manuel Neuer, „Nicolas Kühn" ≠ Moritz Nicolas im selben
Verein). Die Tests prüfen Namensabgleich, die Politik (nur ein klares „fehlt" ab 95 %
sperrt, der jüngste eindeutige Artikel nach dem letzten Spiel des Vereins entscheidet, ein
klares „bank"/„startet" hebt ein älteres „fehlt" auf) und dass ein Fehler kein neues
Urteil erzeugt — Jev lockert nie etwas.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typesafe_sdk import SystemOneResponse, TypeSafeAPIConnectionError

from kickbase_agent.intel import role_judge
from kickbase_agent.intel.role_judge import judge_roles
from kickbase_agent.kickbase.models import Fixture, Player, Position
from kickbase_agent.news.models import NewsArticle

_FIX = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "typesafe" / "role_response.json")
    .read_text(encoding="utf-8")
)
_RESPONSE = SystemOneResponse.model_validate_json(json.dumps(_FIX["response"]))
_NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)
_WINDOW = timedelta(days=31)  # das Fixture deckt 30.08.–28.09. ab

#: Von Hand aus den Artikeln gelesen, BEVOR Jev gefragt wurde (Bezug: nächstes
#: Bundesligaspiel, Länderspiele zählen nicht). Saibari „gesperrt fehlte" betrifft
#: die Nationalelf, „Kühn fehlt wochenlang" nicht Torwart Moritz Nicolas — beide
#: dürfen nicht sperren. Trimmel und Muheim lesen sich als Bank; „bank" sperrt aber
#: nicht (Messung 29.09.2026: 0,69–0,89 je nach Anfrage, siehe role_judge).
EXPECTED_BLOCKS = {"Grønbæk": "fehlt", "Doué": "fehlt", "Kühn": "fehlt"}


def _player(pid: str, first: str, last: str, team: str) -> Player:
    return Player(id=pid, first_name=first, last_name=last, position=Position.MIDFIELDER,
                  market_value=5_000_000, team_id=team)


_PLAYERS = [_player(**p) for p in _FIX["players"]]
_TEAMS: dict[str, str] = _FIX["teams"]
_ARTICLES = [
    NewsArticle(source="kicker", url=a["url"], title=a["title"], body=a["text"],
                published_at=datetime.fromisoformat(a["published_at"]))
    for a in _FIX["articles"]
]


@pytest.fixture(autouse=True)
def _empty_cache():
    role_judge.clear_cache()
    yield
    role_judge.clear_cache()


class _RecordingAsk:
    def __init__(self, response=_RESPONSE) -> None:
        self.response = response
        self.calls: list[tuple[dict, dict]] = []

    async def __call__(self, state, questions):
        self.calls.append((state, dict(questions)))
        return self.response


def _answer_by_title(rules: dict[str, tuple[str, float]]):
    """Antwortet je Paar nach einem Stichwort im Titel — unabhängig von der Reihenfolge."""

    async def ask(state, questions):
        choices = {}
        for key in questions:
            title = state["pairs"][key.removeprefix("role_")]["title"]
            choice, p = next(answer for word, answer in rules.items() if word in title)
            choices[key] = SimpleNamespace(
                choice=choice, confidence=p, probabilities={choice: p, "kein_hinweis": 1 - p}
            )
        return SimpleNamespace(choices=choices)

    return ask


def _article(title: str, hours_ago: float, url: str = "u") -> NewsArticle:
    return NewsArticle(source="kicker", url=url, title=title, body="",
                       published_at=_NOW - timedelta(hours=hours_ago))


def _by_name(result: dict) -> dict[str, str]:
    names = {p.id: p.last_name for p in _PLAYERS}
    return {names[pid]: verdict.role for pid, verdict in result.items()}


_GRONBAEK = _player("9605", "Albert", "Grønbæk", "6")
_HSV = {"6": "Hamburg"}


# ------------------------------------------------------------------ echte Antwort


async def test_all_pairs_are_asked_in_one_request_with_the_recorded_questions():
    ask = _RecordingAsk()
    await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW, ask=ask)

    assert len(ask.calls) == 1
    state, questions = ask.calls[0]
    assert set(questions) == set(_RESPONSE.answers)
    assert state == _FIX["state"]
    # Auch die Fragen selbst: Wer Kriterien ändert, muss die Antwort neu aufnehmen.
    assert {k: q.model_dump(mode="json") for k, q in questions.items()} == _FIX["questions"]


async def test_real_answers_block_only_clear_absences():
    result = await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                               ask=_RecordingAsk())

    assert _by_name(result) == EXPECTED_BLOCKS
    verdict = next(v for v in result.values() if v.player_id == "9605")
    assert verdict.title.startswith("Bitter für den HSV")  # jüngster eindeutiger Artikel
    assert verdict.probability >= role_judge.MIN_P_ROLE


async def test_a_namesake_at_the_same_club_is_not_blocked():
    # „Nächster Rückschlag für Gladbach: Kühn fehlt wochenlang" nennt „Nicolas Kühn".
    result = await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                               ask=_RecordingAsk())

    assert result["2126"].role == "fehlt"  # Nicolas Kühn
    assert "1883" not in result  # Moritz Nicolas, Torwart desselben Vereins


# ------------------------------------------------------------------ Politik


async def test_the_newest_decisive_article_wins():
    old = _article("Grönbaek fällt vorerst aus", hours_ago=72, url="u1")
    new = _article("Grönbaek wieder im Mannschaftstraining", hours_ago=24, url="u2")
    ask = _answer_by_title({"Mannschaftstraining": ("startet", 0.97),
                            "fällt vorerst aus": ("fehlt", 0.95)})

    result = await judge_roles([_GRONBAEK], [old, new], _HSV, now=_NOW, ask=ask)

    assert result == {}


async def test_an_unclear_newer_article_does_not_lift_a_clear_absence():
    old = _article("Grönbaek fällt vorerst aus", hours_ago=72, url="u1")
    new = _article("Grönbaek und der HSV-Kader", hours_ago=24, url="u2")
    ask = _answer_by_title({"HSV-Kader": ("startet", 0.6), "fällt vorerst aus": ("fehlt", 0.95)})

    result = await judge_roles([_GRONBAEK], [old, new], _HSV, now=_NOW, ask=ask)

    assert _by_name(result) == {"Grønbæk": "fehlt"}
    assert result["9605"].url == "u1"


async def test_an_absence_below_the_threshold_never_blocks():
    # Höchster Fehlwert der Messung: 0,86 („Fällt Kade bei Augsburg aus?"); Schwelle 0,95.
    ask = _answer_by_title({"Grönbaek": ("fehlt", 0.94)})
    article = _article("Fällt Grönbaek aus?", hours_ago=24)

    assert await judge_roles([_GRONBAEK], [article], _HSV, now=_NOW, ask=ask) == {}


async def test_a_clear_bench_verdict_never_blocks():
    ask = _answer_by_title({"Grönbaek": ("bank", 0.99)})
    article = _article("Grönbaek nur noch Nummer 3", hours_ago=24)

    assert await judge_roles([_GRONBAEK], [article], _HSV, now=_NOW, ask=ask) == {}


async def test_a_clear_newer_bench_verdict_lifts_an_older_absence():
    old = _article("Grönbaek fällt vorerst aus", hours_ago=72, url="u1")
    new = _article("Grönbaek zurück, zunächst Joker", hours_ago=24, url="u2")
    ask = _answer_by_title({"Joker": ("bank", 0.95), "fällt vorerst aus": ("fehlt", 0.95)})

    assert await judge_roles([_GRONBAEK], [old, new], _HSV, now=_NOW, ask=ask) == {}


async def test_articles_outside_the_window_are_not_asked():
    ask = _RecordingAsk()
    old = _article("Grönbaek fällt vorerst aus", hours_ago=97)

    assert await judge_roles([_GRONBAEK], [old], _HSV, now=_NOW, ask=ask) == {}
    assert ask.calls == []


async def test_articles_before_the_clubs_last_kickoff_are_not_asked():
    # Vor dem letzten Spiel geschrieben = über dieses Spiel; danach gilt es nicht mehr.
    played = Fixture(matchday=4, home_team_id="6", away_team_id="2",
                     kickoff=_NOW - timedelta(hours=48))
    upcoming = Fixture(matchday=5, home_team_id="6", away_team_id="3",
                       kickoff=_NOW + timedelta(hours=72))
    before = _article("Grönbaek fällt gegen Bayern aus", hours_ago=60, url="u1")
    after = _article("Grönbaek fällt vorerst aus", hours_ago=24, url="u2")
    ask = _RecordingAsk(SimpleNamespace(choices={"role_p0": SimpleNamespace(
        choice="fehlt", confidence=0.95, probabilities={"fehlt": 0.95, "kein_hinweis": 0.05})}))

    result = await judge_roles([_GRONBAEK], [before, after], _HSV, now=_NOW,
                               fixtures=[played, upcoming], ask=ask)

    state, _ = ask.calls[0]
    assert [pair["title"] for pair in state["pairs"].values()] == ["Grönbaek fällt vorerst aus"]
    assert result["9605"].url == "u2"


async def test_stored_times_without_zone_count_as_utc():
    # news_items speichert die GMT-Zeit des kicker-Feeds ohne Zone (SQLite).
    inside = NewsArticle(source="kicker", url="u1", title="Grönbaek fällt vorerst aus",
                         body="", published_at=datetime(2026, 9, 25, 17, 0))  # 95 h
    outside = NewsArticle(source="kicker", url="u2", title="Grönbaek fällt länger aus",
                          body="", published_at=datetime(2026, 9, 25, 15, 0))  # 97 h
    ask = _RecordingAsk(SimpleNamespace(choices={"role_p0": SimpleNamespace(
        choice="fehlt", confidence=0.95, probabilities={"fehlt": 0.95, "kein_hinweis": 0.05})}))

    result = await judge_roles([_GRONBAEK], [inside, outside], _HSV, now=_NOW, ask=ask)

    state, _ = ask.calls[0]
    assert [pair["title"] for pair in state["pairs"].values()] == ["Grönbaek fällt vorerst aus"]
    assert result["9605"].published_at == datetime(2026, 9, 25, 17, 0, tzinfo=UTC)


# ------------------------------------------------------------------ Betriebsschutz


async def test_failure_means_no_verdict_and_a_pause():
    calls = []

    async def broken(state, questions):
        calls.append(1)
        raise TypeSafeAPIConnectionError("keine Verbindung")

    first = await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW, ask=broken)
    second = await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW, ask=broken)

    assert first == {} and second == {}
    assert len(calls) == 1  # während der Pause wird nicht erneut gefragt


async def test_a_hanging_answer_is_cut_off_hard(monkeypatch):
    monkeypatch.setattr(role_judge, "TIMEOUT_S", 0.05)

    async def hanging(state, questions):
        await asyncio.sleep(5)

    started = time.monotonic()
    result = await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                               ask=hanging)
    assert result == {} and time.monotonic() - started < 1.0


@pytest.mark.parametrize("p", [float("nan"), 1.5, -0.1])
async def test_invalid_numbers_are_ignored(p):
    ask = _answer_by_title({"Grönbaek": ("fehlt", p)})
    article = _article("Grönbaek fällt vorerst aus", hours_ago=24)

    assert await judge_roles([_GRONBAEK], [article], _HSV, now=_NOW, ask=ask) == {}


async def test_unknown_labels_are_ignored():
    # Ein unbekanntes Label darf weder sperren noch ein älteres klares Urteil verdecken.
    old = _article("Grönbaek fällt vorerst aus", hours_ago=72, url="u1")
    new = _article("Grönbaek und die Transferliste", hours_ago=24, url="u2")
    ask = _answer_by_title({"Transferliste": ("verkaufen", 0.99),
                            "fällt vorerst aus": ("fehlt", 0.95)})

    result = await judge_roles([_GRONBAEK], [old, new], _HSV, now=_NOW, ask=ask)

    assert _by_name(result) == {"Grønbæk": "fehlt"}


async def test_only_the_newest_pairs_are_asked_when_there_are_too_many(monkeypatch):
    monkeypatch.setattr(role_judge, "MAX_PAIRS", 2)
    articles = [_article(f"Grönbaek Meldung {n}", hours_ago=n, url=f"u{n}") for n in (3, 2, 1)]
    ask = _RecordingAsk(SimpleNamespace(choices={}))

    await judge_roles([_GRONBAEK], articles, _HSV, now=_NOW, ask=ask)
    first = [pair["title"] for pair in ask.calls[0][0]["pairs"].values()]

    assert first == ["Grönbaek Meldung 1", "Grönbaek Meldung 2"]


async def test_judged_pairs_are_not_asked_again():
    await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW, ask=_RecordingAsk())

    async def must_not_be_called(state, questions):
        raise AssertionError("bereits beurteiltes Paar erneut gefragt")

    again = await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                              ask=must_not_be_called)
    assert _by_name(again) == EXPECTED_BLOCKS


async def test_without_api_key_nothing_is_asked(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    assert await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW) == {}


def test_rejection_names_source_date_title_and_role():
    verdict = role_judge.RoleVerdict(
        player_id="9605", role="fehlt", probability=0.97,
        title="Bitter für den HSV: Top-Scorer Grönbaek fällt vorerst aus", url="u",
        published_at=datetime(2026, 9, 26, 9, 8, tzinfo=UTC),
    )
    text = role_judge.role_rejection(verdict)
    assert "kicker 26.09." in text and "Grönbaek fällt vorerst aus" in text
    assert "fehlt" in text and "97 %" in text


async def test_a_failure_without_pause_leaves_the_engine_untouched():
    """Nach-Review 30.09.2026: Der News-Radar fragt im selben Prozess; sein Ausfall darf
    die Kaufsperre der Engine nicht zehn Minuten lang abschalten."""
    calls = []

    async def broken(state, questions):
        calls.append(1)
        raise TypeSafeAPIConnectionError("keine Verbindung")

    await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW, ask=broken,
                      pause_on_failure=False)

    assert role_judge.is_paused() is False
    await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW, ask=broken)
    assert len(calls) == 2


async def test_unjudged_counts_role_pairs_without_an_answer():
    article = _article("Grönbaek fällt vorerst aus", hours_ago=24)
    assert role_judge.unjudged([_GRONBAEK], [article], now=_NOW) == 1

    await judge_roles([_GRONBAEK], [article], _HSV, now=_NOW,
                      ask=_answer_by_title({"Grönbaek": ("fehlt", 0.99)}))

    assert role_judge.unjudged([_GRONBAEK], [article], now=_NOW) == 0


# ------------------------------------------------------------------ Jev-Protokoll


async def test_every_asked_pair_is_journaled_with_rule_and_outcome(jev_rows):
    await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                      ask=_RecordingAsk())

    assert len(jev_rows) == len(_RESPONSE.answers)  # eine Zeile je gestellter Frage
    assert len({r.batch_id for r in jev_rows}) == 1
    assert {(r.kind, r.purpose, r.status, r.model, r.batch_size) for r in jev_rows} == {
        ("rolle", "kaufpruefung", "ok", role_judge.MODEL, len(_RESPONSE.answers))
    }
    gron = next(r for r in jev_rows if r.player_id == "9605" and r.subject.startswith("Bitter"))
    assert (gron.answer, gron.outcome) == ("fehlt", "sperrt")
    assert gron.probability == pytest.approx(0.99)
    assert gron.probabilities["fehlt"] == pytest.approx(0.99)
    (key, raw), = gron.raw.items()
    assert key.startswith("role_p") and raw["choice"] == "fehlt"
    assert "0,95" in gron.rule
    assert (gron.player_name, gron.club, gron.source) == (
        "Albert Grønbæk", "Hamburger SV", "kicker"
    )
    assert gron.text.startswith("Seit Donnerstagabend")
    assert gron.published_at == next(a.published_at for a in _ARTICLES if a.title == gron.subject)
    # Gestempelt wird die echte Fragezeit, nicht der Planstart (Review 01.10.).
    assert datetime.now(UTC) - gron.created_at < timedelta(minutes=1)
    assert gron.latency_ms >= 0
    # Namensvetter: „Kühn fehlt wochenlang" bei Torwart Moritz Nicolas, 0,56 — zählt nicht.
    nicolas = next(r for r in jev_rows if r.player_id == "1883" and "Kühn fehlt" in r.subject)
    assert (nicolas.answer, nicolas.outcome) == ("fehlt", "kein_einfluss")


@pytest.mark.parametrize(
    ("purpose", "outcome"),
    [("kaufpruefung", "sperrt"), ("kader", "warnung"), ("naechstes_spiel", "fehlt_noch")],
)
async def test_a_clear_absence_is_journaled_with_the_callers_purpose(jev_rows, purpose, outcome):
    ask = _answer_by_title({"Grönbaek": ("fehlt", 0.99)})
    await judge_roles([_GRONBAEK], [_article("Grönbaek fällt vorerst aus", 2)], _HSV, now=_NOW,
                      ask=ask, purpose=purpose)
    assert [(r.purpose, r.outcome) for r in jev_rows] == [(purpose, outcome)]


async def test_the_trade_radar_check_is_journaled_as_taking_the_player_out(jev_rows):
    """Handels-Radar (01.10.2026): dieselbe Sperre wie die Kaufprüfung, eigener Zweck und
    eigene Regel — auf der Jev-Seite heißt es „aus dem Handels-Radar“, nicht „neuer Kauf“."""
    ask = _answer_by_title({"Grönbaek": ("fehlt", 0.99)})
    await judge_roles([_GRONBAEK], [_article("Grönbaek fällt vorerst aus", 2)], _HSV, now=_NOW,
                      ask=ask, purpose="handel", pause_on_failure=False)
    [row] = jev_rows
    assert (row.purpose, row.outcome) == ("handel", "sperrt")
    assert "Handels-Radar" in row.rule


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (("fehlt", 0.95), "sperrt"),  # Grenze: ab 0,95
        (("fehlt", 0.94), "kein_einfluss"),
        (("bank", 0.97), "eindeutig"),  # hebt ein älteres „fehlt" auf, sperrt nie
        (("startet", 0.95), "eindeutig"),
        (("bank", 0.9), "kein_einfluss"),
    ],
)
async def test_the_journaled_outcome_follows_the_judges_rule(jev_rows, answer, outcome):
    ask = _answer_by_title({"Grönbaek": answer})
    await judge_roles([_GRONBAEK], [_article("Grönbaek im Fokus", 2)], _HSV, now=_NOW, ask=ask)
    assert [r.outcome for r in jev_rows] == [outcome]


async def test_cached_pairs_are_not_journaled_again(jev_rows):
    ask = _answer_by_title({"Grönbaek": ("fehlt", 0.99)})
    args = ([_GRONBAEK], [_article("Grönbaek fällt vorerst aus", 2)], _HSV)
    await judge_roles(*args, now=_NOW, ask=ask)
    await judge_roles(*args, now=_NOW, ask=ask)
    assert len(jev_rows) == 1


async def test_a_failed_request_is_journaled_once_without_a_verdict(jev_rows):
    async def broken(state, questions):
        raise TypeSafeAPIConnectionError("keine Verbindung")

    result = await judge_roles([_GRONBAEK], [_article("Grönbaek fällt vorerst aus", 2)], _HSV,
                               now=_NOW, ask=broken)

    assert result == {}
    assert [(r.status, r.outcome, r.error, r.batch_size, r.player_id) for r in jev_rows] == [
        ("ausfall", "ausfall", "TypeSafeAPIConnectionError", 1, None)
    ]


async def test_an_invalid_answer_is_journaled_as_invalid(jev_rows):
    ask = _answer_by_title({"Grönbaek": ("fehlt", float("nan"))})
    result = await judge_roles([_GRONBAEK], [_article("Grönbaek fällt vorerst aus", 2)], _HSV,
                               now=_NOW, ask=ask)

    assert result == {}
    assert [(r.status, r.outcome, r.answer, r.probability) for r in jev_rows] == [
        ("ungueltig", "ungueltig", "fehlt", None)
    ]


async def test_a_broken_journal_never_changes_the_verdict(monkeypatch):
    from kickbase_agent.intel import jev_journal

    def boom(rows):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(jev_journal, "_writer", boom)
    result = await judge_roles(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                               ask=_RecordingAsk())
    assert _by_name(result) == EXPECTED_BLOCKS



async def test_an_older_absence_overruled_in_the_same_request_is_marked(jev_rows):
    """Review 01.10.: Ein jüngerer klarer Artikel entscheidet — die ältere Zeile ist überholt,
    nicht „sperrt"."""
    ask = _answer_by_title({"fällt": ("fehlt", 0.99), "Joker": ("bank", 0.97)})
    articles = [_article("Grönbaek fällt vorerst aus", 30, url="alt"),
                _article("Grönbaek zurück, zunächst Joker", 3, url="neu")]
    result = await judge_roles([_GRONBAEK], articles, _HSV, now=_NOW, ask=ask)

    assert result == {}
    outcomes = {r.subject: r.outcome for r in jev_rows}
    assert outcomes == {"Grönbaek fällt vorerst aus": "ueberholt",
                        "Grönbaek zurück, zunächst Joker": "eindeutig"}


async def test_a_radar_failure_row_does_not_promise_a_pause(jev_rows):
    async def broken(state, questions):
        raise TypeSafeAPIConnectionError("keine Verbindung")

    await judge_roles([_GRONBAEK], [_article("Grönbaek fällt vorerst aus", 2)], _HSV,
                      now=_NOW, ask=broken, pause_on_failure=False, purpose="kader")
    [row] = jev_rows
    assert "ohne Pause" in row.rule and "10 Minuten" not in row.rule


async def test_the_answering_model_is_recorded(jev_rows):
    base = _answer_by_title({"Grönbaek": ("fehlt", 0.99)})

    async def ask(state, questions):
        response = await base(state, questions)
        response.model = "jev-1.14.0"
        return response

    await judge_roles([_GRONBAEK], [_article("Grönbaek fällt vorerst aus", 2)], _HSV,
                      now=_NOW, ask=ask)
    assert [r.model for r in jev_rows] == ["jev-1.14.0"]
