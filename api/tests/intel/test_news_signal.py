"""Kaufsignal Rückkehr aus kicker-Anreißern per TypeSafe (Emil 30.09.2026: „bau Schritt 4
mit den News").

Das Fixture ist eine echte TypeSafe-Antwort (jev-1.13.0, 30.09.2026) auf echte
kicker-Anreißer vom 01.–21.09.2026 und echte Kickbase-Spieler — mit Fallen, in denen
„Rückkehr" etwas anderes heißt (Transfer zurück zum Ex-Verein, Besuch beim Ex-Verein), und
dem Namensvetter Moritz Nicolas neben Nicolas Kühn. Die Tests prüfen die Politik (nur ein
klares „rueckkehr" ab 95 % ist ein Signal, der jüngste eindeutige Artikel entscheidet, ein
jüngerer Ausfall hebt es auf) und dass jeder Fehler ohne Signal endet.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typesafe_sdk import SystemOneResponse, TypeSafeAPIConnectionError

from kickbase_agent.intel import news_signal
from kickbase_agent.intel.news_signal import judge_returns
from kickbase_agent.kickbase.models import Fixture, Player, Position
from kickbase_agent.news.models import NewsArticle

_FIX = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "typesafe" / "return_response.json")
    .read_text(encoding="utf-8")
)
_RESPONSE = SystemOneResponse.model_validate_json(json.dumps(_FIX["response"]))
_NOW = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
_WINDOW = timedelta(days=31)  # das Fixture deckt 01.–21.09. ab

#: Von Hand gelesen, BEVOR Jev gefragt wurde: klare Rückkehr nach Verletzung.
CLEAR_RETURNS = {"Weiser", "Machida", "Karetsas", "Schlotterbeck"}
#: Bekannte Abweichung: „Kehrt Musiala am Samstag in den Kader zurück?" war von Hand unklar;
#: Jev liest die Frage als Rückkehr (0,96–0,99). Als Hinweis vertretbar, weil der Titel mit
#: Fragezeichen in der Zeile steht — aber kein Beleg, dass Frage-Titel sicher sind.
KNOWN_DEVIATIONS = {"Musiala"}
EXPECTED_RETURNS = CLEAR_RETURNS | KNOWN_DEVIATIONS


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
_WEISER = _player("43", "Mitchell", "Weiser", "10")
_BREMEN = {"10": "Bremen"}


@pytest.fixture(autouse=True)
def _empty_cache():
    news_signal.clear_cache()
    yield
    news_signal.clear_cache()


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
            title = state["pairs"][key.removeprefix("ev_")]["title"]
            choice, p = next(answer for word, answer in rules.items() if word in title)
            choices[key] = SimpleNamespace(
                choice=choice, confidence=p, probabilities={choice: p, "kein_ereignis": 1 - p}
            )
        return SimpleNamespace(choices=choices)

    return ask


def _article(title: str, hours_ago: float, url: str = "u") -> NewsArticle:
    return NewsArticle(source="kicker", url=url, title=title, body="",
                       published_at=_NOW - timedelta(hours=hours_ago))


def _names(result: dict) -> set[str]:
    names = {p.id: p.last_name for p in _PLAYERS}
    return {names[pid] for pid in result}


# ------------------------------------------------------------------ echte Antwort


async def test_all_pairs_are_asked_in_one_request_with_the_recorded_questions():
    ask = _RecordingAsk()
    await judge_returns(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW, ask=ask)

    assert len(ask.calls) == 1
    state, questions = ask.calls[0]
    assert set(questions) == set(_RESPONSE.answers)
    assert state == _FIX["state"]
    assert {k: q.model_dump(mode="json") for k, q in questions.items()} == _FIX["questions"]


async def test_real_answers_signal_only_returns_after_injury():
    result = await judge_returns(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                                 ask=_RecordingAsk())

    assert _names(result) == EXPECTED_RETURNS
    # Transfer-Rückkehr (Diaby, Kühn), Besuch beim Ex-Verein (Prömel, Königsdörffer) und
    # der Namensvetter Moritz Nicolas sind kein Kaufsignal.
    assert all(signal.probability >= news_signal.MIN_P_RETURN for signal in result.values())


# ------------------------------------------------------------------ Politik


async def test_a_newer_clear_absence_cancels_an_older_return():
    old = _article("Weiser zurück im Mannschaftstraining", hours_ago=40, url="u1")
    new = _article("Weiser fällt wieder aus", hours_ago=10, url="u2")
    ask = _answer_by_title({"Mannschaftstraining": ("rueckkehr", 0.99),
                            "fällt wieder aus": ("ausfall", 0.97)})

    assert await judge_returns([_WEISER], [old, new], _BREMEN, now=_NOW, ask=ask) == {}


async def test_a_newer_likely_absence_cancels_even_below_the_signal_threshold():
    # Nach-Review 30.09.2026: Das Fixture selbst liest Doués „muss noch aussetzen" mit 0,93.
    old = _article("Weiser zurück im Mannschaftstraining", hours_ago=40, url="u1")
    new = _article("Weiser muss noch aussetzen", hours_ago=10, url="u2")
    ask = _answer_by_title({"Mannschaftstraining": ("rueckkehr", 0.99),
                            "aussetzen": ("ausfall", 0.93)})

    assert await judge_returns([_WEISER], [old, new], _BREMEN, now=_NOW, ask=ask) == {}


async def test_a_newer_confident_no_event_does_not_cancel_a_return():
    old = _article("Weiser zurück im Mannschaftstraining", hours_ago=40, url="u1")
    new = _article("Weiser über seine Kindheit", hours_ago=10, url="u2")
    ask = _answer_by_title({"Mannschaftstraining": ("rueckkehr", 0.99),
                            "Kindheit": ("kein_ereignis", 0.99)})

    result = await judge_returns([_WEISER], [old, new], _BREMEN, now=_NOW, ask=ask)

    assert set(result) == {"43"} and result["43"].url == "u1"


async def test_exactly_the_threshold_counts():
    ask = _answer_by_title({"Weiser": ("rueckkehr", 0.95)})
    article = _article("Weiser zurück im Mannschaftstraining", hours_ago=10)

    assert set(await judge_returns([_WEISER], [article], _BREMEN, now=_NOW, ask=ask)) == {"43"}


async def test_unjudged_counts_pairs_without_an_answer():
    article = _article("Weiser zurück im Mannschaftstraining", hours_ago=10)
    kwargs = {"now": _NOW}
    assert news_signal.unjudged([_WEISER], [article], **kwargs) == 1

    await judge_returns([_WEISER], [article], _BREMEN, now=_NOW,
                        ask=_answer_by_title({"Weiser": ("rueckkehr", 0.99)}))

    assert news_signal.unjudged([_WEISER], [article], **kwargs) == 0


async def test_an_unclear_newer_article_does_not_cancel_a_clear_return():
    old = _article("Weiser zurück im Mannschaftstraining", hours_ago=40, url="u1")
    new = _article("Weiser und die Bremer Abwehr", hours_ago=10, url="u2")
    ask = _answer_by_title({"Mannschaftstraining": ("rueckkehr", 0.99),
                            "Abwehr": ("rueckkehr", 0.6)})

    result = await judge_returns([_WEISER], [old, new], _BREMEN, now=_NOW, ask=ask)

    assert set(result) == {"43"} and result["43"].url == "u1"


async def test_a_return_below_the_threshold_is_no_signal():
    # Höchster Fehlwert der Messung: 0,94 (Neuzugang Diaby in einer Doué-Meldung).
    ask = _answer_by_title({"Weiser": ("rueckkehr", 0.94)})
    article = _article("Weiser vor dem Comeback?", hours_ago=10)

    assert await judge_returns([_WEISER], [article], _BREMEN, now=_NOW, ask=ask) == {}


async def test_an_absence_is_never_a_buy_signal():
    ask = _answer_by_title({"Weiser": ("ausfall", 0.99)})
    article = _article("Weiser fällt aus", hours_ago=10)

    assert await judge_returns([_WEISER], [article], _BREMEN, now=_NOW, ask=ask) == {}


async def test_only_articles_of_the_last_two_days_after_the_last_kickoff_are_asked():
    played = Fixture(matchday=5, home_team_id="10", away_team_id="3",
                     kickoff=_NOW - timedelta(hours=20))
    before_match = _article("Weiser zurück im Training", hours_ago=30, url="u1")
    too_old = _article("Weiser Comeback", hours_ago=49, url="u2")
    fresh = _article("Weiser wieder im Mannschaftstraining", hours_ago=5, url="u3")
    ask = _RecordingAsk(SimpleNamespace(choices={"ev_p0": SimpleNamespace(
        choice="rueckkehr", confidence=0.99,
        probabilities={"rueckkehr": 0.99, "kein_ereignis": 0.01})}))

    result = await judge_returns([_WEISER], [before_match, too_old, fresh], _BREMEN, now=_NOW,
                                 fixtures=[played], ask=ask)

    state, _ = ask.calls[0]
    assert [pair["title"] for pair in state["pairs"].values()] == [
        "Weiser wieder im Mannschaftstraining"
    ]
    assert result["43"].url == "u3"


async def test_the_window_is_two_days_even_without_fixtures():
    inside = _article("Weiser zurück im Mannschaftstraining", hours_ago=47, url="u1")
    outside = _article("Weiser Comeback nach Verletzung", hours_ago=49, url="u2")
    ask = _RecordingAsk(SimpleNamespace(choices={}))

    await judge_returns([_WEISER], [inside, outside], _BREMEN, now=_NOW, ask=ask)

    state, _ = ask.calls[0]
    assert [pair["title"] for pair in state["pairs"].values()] == [
        "Weiser zurück im Mannschaftstraining"
    ]


# ------------------------------------------------------------------ Betriebsschutz


async def test_failure_means_no_signal_and_a_pause():
    calls = []

    async def broken(state, questions):
        calls.append(1)
        raise TypeSafeAPIConnectionError("keine Verbindung")

    first = await judge_returns(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                                ask=broken)
    second = await judge_returns(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                                 ask=broken)

    assert first == {} and second == {}
    assert len(calls) == 1


async def test_a_hanging_answer_is_cut_off_hard(monkeypatch):
    monkeypatch.setattr(news_signal, "TIMEOUT_S", 0.05)

    async def hanging(state, questions):
        await asyncio.sleep(5)

    started = time.monotonic()
    result = await judge_returns(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                                 ask=hanging)
    assert result == {} and time.monotonic() - started < 1.0


@pytest.mark.parametrize("p", [float("nan"), 1.5, -0.1])
async def test_invalid_numbers_are_ignored(p):
    ask = _answer_by_title({"Weiser": ("rueckkehr", p)})
    article = _article("Weiser zurück im Mannschaftstraining", hours_ago=10)

    assert await judge_returns([_WEISER], [article], _BREMEN, now=_NOW, ask=ask) == {}


async def test_unknown_labels_are_ignored():
    # Ein unbekanntes Label darf weder signalisieren noch ein älteres klares Urteil verdecken.
    old = _article("Weiser zurück im Mannschaftstraining", hours_ago=40, url="u1")
    new = _article("Weiser und der Transfermarkt", hours_ago=10, url="u2")
    ask = _answer_by_title({"Transfermarkt": ("kaufen", 0.99),
                            "Mannschaftstraining": ("rueckkehr", 0.99)})

    result = await judge_returns([_WEISER], [old, new], _BREMEN, now=_NOW, ask=ask)

    assert set(result) == {"43"} and result["43"].url == "u1"


async def test_only_the_newest_pairs_are_asked_when_there_are_too_many(monkeypatch):
    monkeypatch.setattr(news_signal, "MAX_PAIRS", 2)
    articles = [_article(f"Weiser Meldung {n}", hours_ago=n, url=f"u{n}") for n in (3, 2, 1)]
    ask = _RecordingAsk(SimpleNamespace(choices={}))

    await judge_returns([_WEISER], articles, _BREMEN, now=_NOW, ask=ask)

    first = [pair["title"] for pair in ask.calls[0][0]["pairs"].values()]
    assert first == ["Weiser Meldung 1", "Weiser Meldung 2"]


async def test_judged_pairs_are_not_asked_again():
    await judge_returns(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                        ask=_RecordingAsk())

    async def must_not_be_called(state, questions):
        raise AssertionError("bereits beurteiltes Paar erneut gefragt")

    again = await judge_returns(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                                ask=must_not_be_called)
    assert _names(again) == EXPECTED_RETURNS


async def test_without_api_key_nothing_is_asked(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    assert await judge_returns(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW) == {}



async def test_an_unlikely_newer_absence_does_not_cancel():
    old = _article("Weiser zurück im Mannschaftstraining", hours_ago=40, url="u1")
    new = _article("Weiser und die Bremer Abwehr", hours_ago=10, url="u2")
    ask = _answer_by_title({"Mannschaftstraining": ("rueckkehr", 0.99),
                            "Abwehr": ("ausfall", 0.4)})

    result = await judge_returns([_WEISER], [old, new], _BREMEN, now=_NOW, ask=ask)

    assert set(result) == {"43"}


# ------------------------------------------------------------------ Jev-Protokoll


async def test_every_asked_pair_is_journaled_with_rule_and_outcome(jev_rows):
    await judge_returns(_PLAYERS, _ARTICLES, _TEAMS, now=_NOW, window=_WINDOW,
                        ask=_RecordingAsk())

    assert len(jev_rows) == len(_RESPONSE.answers)
    assert {(r.kind, r.purpose, r.status, r.model) for r in jev_rows} == {
        ("rueckkehr", "kaufhinweis", "ok", news_signal.MODEL)
    }

    def row(pid: str, word: str):
        return next(r for r in jev_rows if r.player_id == pid and word in r.subject)

    # Zwei klare Weiser-Artikel: der jüngere entscheidet, der ältere ist überholt.
    weiser = sorted((r for r in jev_rows if r.player_id == "43"), key=lambda r: r.published_at)
    assert [(r.outcome, r.answer) for r in weiser] == [
        ("ueberholt", "rueckkehr"), ("kaufhinweis", "rueckkehr")
    ]
    assert "0,95" in weiser[-1].rule
    assert row("2661", "Nervenspiel").outcome == "kein_signal"  # Transfer, keine Rückkehr
    assert row("2069", "erneut operiert").outcome == "hebt_auf"  # Ausfall 0,95
    assert row("11957", "Ben Seghir").outcome == "kein_signal"  # Rückkehr nur 0,60


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (("rueckkehr", 0.95), "kaufhinweis"),  # Grenze: ab 0,95
        (("rueckkehr", 0.94), "kein_signal"),
        (("ausfall", 0.5), "hebt_auf"),  # Grenze: ab 0,50
        (("ausfall", 0.49), "kein_signal"),
    ],
)
async def test_the_journaled_outcome_follows_the_signal_rule(jev_rows, answer, outcome):
    ask = _answer_by_title({"Weiser": answer})
    await judge_returns([_WEISER], [_article("Weiser im Fokus", 2)], _BREMEN, now=_NOW, ask=ask)
    assert [r.outcome for r in jev_rows] == [outcome]


async def test_a_failed_return_request_is_journaled_once(jev_rows):
    async def broken(state, questions):
        raise TypeSafeAPIConnectionError("keine Verbindung")

    await judge_returns([_WEISER], [_article("Weiser zurück im Training", 2)], _BREMEN,
                        now=_NOW, ask=broken)
    assert [(r.kind, r.status, r.error) for r in jev_rows] == [
        ("rueckkehr", "ausfall", "TypeSafeAPIConnectionError")
    ]


async def test_cached_return_pairs_are_not_journaled_again(jev_rows):
    ask = _answer_by_title({"Weiser": ("rueckkehr", 0.99)})
    args = ([_WEISER], [_article("Weiser zurück im Training", 2)], _BREMEN)
    await judge_returns(*args, now=_NOW, ask=ask)
    await judge_returns(*args, now=_NOW, ask=ask)
    assert len(jev_rows) == 1



async def test_a_return_cancelled_by_a_newer_absence_in_the_same_request_is_marked(jev_rows):
    ask = _answer_by_title({"Training": ("rueckkehr", 0.99), "erneut": ("ausfall", 0.9)})
    articles = [_article("Weiser zurück im Training", 30, url="alt"),
                _article("Weiser verletzt sich erneut", 3, url="neu")]
    result = await judge_returns([_WEISER], articles, _BREMEN, now=_NOW, ask=ask)

    assert result == {}
    assert {r.subject: r.outcome for r in jev_rows} == {
        "Weiser zurück im Training": "ueberholt", "Weiser verletzt sich erneut": "hebt_auf",
    }
