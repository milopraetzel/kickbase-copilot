"""Kaufsignal aus kicker-Anreißern per TypeSafe: Rückkehr nach Verletzung oder Sperre.

Schritt 4 des Jev-Umbaus (Emil 30.09.2026: „bau Schritt 4 mit den News"). Der News-Radar
(`worker/news_radar.py`) zeigt Emil jeden Morgen, welche Spieler am Transfermarkt laut
kicker gerade zurückkommen. Rückkehrer ziehen im Marktwert an — Weiser +2,01 M und
Karetsas +1,07 M in den sieben Tagen nach ihrer Rückkehr (Kickbase, 30.09.2026).

Warum nur Rückkehr (Messung 30.09.2026, je 10 Abfragen gebündelt und einzeln, L-115): Eine
Tendenzfrage „mehr oder weniger Einsatz" trennte nicht — „zu gut für die Bank" 0,23–0,66,
„empfiehlt sich" 0,48–0,77, „kommt in Form" 0,84–0,98, ein Namensvetter bis 0,60. Das
Ereignis „Rückkehr" trennt: klare Rückkehrer (Weiser, Machida, Karetsas, Schlotterbeck)
0,99–1,00, der Frage-Titel „Kehrt Musiala … zurück?" 0,96–0,99, Transfer-„Rückkehr" (Diaby,
Kühn, Skhiri, Vieira) und Besuche beim Ex-Verein (Prömel, Königsdörffer) höchstens 0,56, ein
Neuzugang in einer Ausfall-Meldung (Diaby) höchstens 0,94. Daher `MIN_P_RETURN` = 0,95 —
der Abstand zum knappsten Fehlwert ist klein, das Signal bleibt deshalb ein Hinweis für Emil.

Der Code entscheidet, nicht das Modell: Je Spieler zählt der jüngste Artikel mit
eindeutigem Urteil. Eine Rückkehr ab `MIN_P_RETURN` ist ein Signal; ein jüngerer
wahrscheinlicher Ausfall (ab `MIN_P_CANCEL`) hebt es auf — für einen Kaufhinweis genügt
der Zweifel (Nach-Review: „muss noch aussetzen" las Jev mit 0,93). Artikel von vor dem
letzten Bundesligaspiel des Vereins zählen nicht (Zeitanker wie bei der Rolle). Das Signal
ist rein lesend: Gekauft wird nur auf Emils namentlichen Auftrag je Spieler (CLAUDE.md
§5.12), die Engine plant weiter nur nach Punkten.

Betriebsschutz wie bei der Rolle: harte Obergrenze `TIMEOUT_S`, `FAILURE_PAUSE_S` Pause
nach einem Ausfall, nur bekannte Labels und endliche Wahrscheinlichkeiten in [0, 1], Cache
je (Spieler, Artikel), höchstens `MAX_PAIRS` Paare je Anfrage. Grenze: nur Anreißer,
Namensabgleich wörtlich (siehe `intel/news_text.py`); die Schwelle ist ein Startwert, geprüft
an der echten Antwort vom 30.09.2026 (`tests/fixtures/typesafe/return_response.json`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from typesafe_sdk import AsyncTypeSafeClient, Choice, RetryPolicy, SystemOneResponse

from kickbase_agent.config import get_settings
from kickbase_agent.intel import jev_journal
from kickbase_agent.intel.jev_journal import JevEntry, fmt_p
from kickbase_agent.intel.news_text import (
    Pair,
    club_name,
    last_kickoffs,
    news_pairs,
    pair_key,
    pairs_state,
    valid_choice,
)
from kickbase_agent.kickbase.models import Fixture, Player
from kickbase_agent.news.models import NewsArticle

log = logging.getLogger(__name__)

Ask = Callable[[dict, Mapping[str, Choice]], Awaitable[SystemOneResponse]]

#: Fest, damit die geprüfte Schwelle zum Modell passt (siehe severity_judge.MODEL).
MODEL = "jev-1.13.0"
#: Der Radar-Job wartet nicht länger auf TypeSafe — hart für die ganze Anfrage.
TIMEOUT_S = 10.0
#: Nach einem Ausfall so lange nicht fragen.
FAILURE_PAUSE_S = 600.0
#: Nur ein klares Urteil zählt (Messung siehe Moduldoku).
MIN_P_RETURN = 0.95
#: Ein jüngerer Ausfall hebt schon ab dieser Wahrscheinlichkeit auf (vorsichtiger Hinweis).
MIN_P_CANCEL = 0.5
#: So alt darf ein Artikel sein: Der Radar läuft täglich, eine Rückkehr wirkt Tage.
SIGNAL_WINDOW = timedelta(hours=48)
#: Obergrenze je Anfrage; die jüngsten Paare zuerst.
MAX_PAIRS = 40

RETURN = "rueckkehr"
ABSENCE = "ausfall"
_NO_EVENT = "kein_ereignis"
_CRITERIA = {
    RETURN: (
        "He has returned, or will return within days, to team training or matches after an "
        "injury, illness or suspension. Not a new signing's first matches and not a transfer "
        "back to a former club."
    ),
    ABSENCE: (
        "He is injured, ill or suspended and will miss one or more of his club's upcoming "
        "Bundesliga matches."
    ),
    _NO_EVENT: (
        "Neither of these. Also: a new signing or a transfer back to a former club (even if he "
        "can play now), a visit to a former club, a club's promotion, another person with the "
        "same name, an ordinary German word, or national team, cup or European matches only."
    ),
}

#: (Spieler-ID, Artikel-Hash) → (Label, Wahrscheinlichkeit). Nur gültige Antworten.
_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_CACHE_MAX = 5000
#: Monotone Uhrzeit, bis zu der nach einem Ausfall nicht gefragt wird.
_pause_until = 0.0


@dataclass(frozen=True)
class ReturnSignal:
    """Ein Kaufsignal mit dem Artikel, auf dem es beruht."""

    player_id: str
    probability: float
    title: str
    url: str
    published_at: datetime
    source: str = "kicker"


def clear_cache() -> None:
    """Cache und Ausfall-Pause zurücksetzen (Tests, Neustart-Verhalten)."""
    global _pause_until
    _CACHE.clear()
    _pause_until = 0.0


def is_paused() -> bool:
    """Wartet der Judge gerade nach einem Ausfall (dann gibt es kein neues Urteil)?"""
    return time.monotonic() < _pause_until


def unjudged(
    players: Iterable[Player],
    articles: Iterable[NewsArticle],
    *,
    now: datetime,
    fixtures: Iterable[Fixture] = (),
    window: timedelta = SIGNAL_WINDOW,
) -> int:
    """Wie viele Paare im Fenster noch ohne gültiges Urteil sind (Ausfall, Obergrenze,
    ungültige Antwort) — damit der Radar keine ruhige Lage vortäuscht."""
    pairs = news_pairs(list(players), articles, now - window, last_kickoffs(fixtures, now))
    return sum(1 for player, article, _ in pairs if pair_key(player, article) not in _CACHE)


def _questions(count: int) -> dict[str, Choice]:
    return {
        f"ev_p{i}": Choice(
            instructions=(
                f"`pairs.p{i}.title` and `pairs.p{i}.text` are a German football news "
                f"teaser. Which event does it report for `pairs.p{i}.player` (club: "
                f"`pairs.p{i}.club`)?"
            ),
            criteria=_CRITERIA,
        )
        for i in range(count)
    }


#: Wofür der Radar Rückkehr fragt — nur fürs Jev-Protokoll.
PURPOSE = "kaufhinweis"


def journal_outcome(answer: tuple[str, float] | None) -> tuple[str, str]:
    """Folge und Regeltext EINES Urteils fürs Jev-Protokoll — mit genau den Konstanten,
    nach denen `judge_returns` entscheidet (ein Ausfall wird zuerst geprüft, wie dort)."""
    if answer is None:
        return jev_journal.OUTCOME_INVALID, (
            "Ungültige Antwort zählt nicht; beim nächsten Lauf wird erneut gefragt."
        )
    label, probability = answer
    if label == ABSENCE and probability >= MIN_P_CANCEL:
        return "hebt_auf", (
            f"„Ausfall“ ≥ {fmt_p(MIN_P_CANCEL)} hebt einen älteren Rückkehr-Hinweis auf."
        )
    if label == RETURN and probability >= MIN_P_RETURN:
        return "kaufhinweis", (
            f"„Rückkehr“ ≥ {fmt_p(MIN_P_RETURN)}: Kaufhinweis im News-Radar; gekauft wird nur "
            "auf Emils Auftrag."
        )
    return "kein_signal", f"Unter der Schwelle ({fmt_p(MIN_P_RETURN)}): kein Signal."


def _is_decisive(answer: tuple[str, float] | None) -> bool:
    """Ein eindeutiges Urteil — genau die Bedingungen der Entscheidungsschleife."""
    if answer is None:
        return False
    label, probability = answer
    return ((label == ABSENCE and probability >= MIN_P_CANCEL)
            or (label == RETURN and probability >= MIN_P_RETURN))


def _journal_rows(
    pairs: list[Pair],
    response: SystemOneResponse,
    team_names: Mapping[str, str],
    *,
    at: datetime,
    batch_id: str,
    latency_ms: int,
    decisive: Mapping[str, tuple[str, str]],
) -> list[JevEntry]:
    """Eine Protokollzeile je gestellter Frage: was Jev las, was Jev sagte, was folgt.

    `decisive` nennt je Spieler das Paar, das `judge_returns` entscheiden ließ; ein anderes
    eindeutiges Urteil zum selben Spieler ist überholt (Review 01.10.: sonst stand ein
    „Kaufhinweis" da, den ein jüngerer Ausfall in derselben Anfrage aufgehoben hatte)."""
    rows = []
    model = jev_journal.answering_model(response, MODEL)
    for i, (player, article, published) in enumerate(pairs):
        key = f"ev_p{i}"
        given = response.choices.get(key)
        answer = valid_choice(given, _CRITERIA)
        probabilities, confidence, raw = jev_journal.answer_fields(given)
        outcome, rule = journal_outcome(answer)
        if _is_decisive(answer) and decisive.get(player.id) != pair_key(player, article):
            outcome, rule = jev_journal.OUTCOME_SUPERSEDED, jev_journal.RULE_SUPERSEDED
        chosen = getattr(given, "choice", None)
        rows.append(JevEntry(
            created_at=at, kind=jev_journal.KIND_RETURN, purpose=PURPOSE,
            status=jev_journal.STATUS_OK if answer else jev_journal.STATUS_INVALID,
            model=model, batch_id=batch_id, batch_size=len(pairs), latency_ms=latency_ms,
            player_id=player.id,
            player_name=f"{player.first_name} {player.last_name}".strip(),
            club=club_name(player.team_id, team_names), subject=article.title,
            text=article.body, url=article.url, published_at=published, source=article.source,
            answer=chosen if isinstance(chosen, str) else None,
            probability=answer[1] if answer else None, probabilities=probabilities,
            confidence=confidence, raw={key: raw} if raw else None, rule=rule, outcome=outcome,
        ))
    return rows


def _default_ask() -> Ask | None:
    key = get_settings().typesafe_api_key
    if not key:
        return None

    async def ask(state: dict, questions: Mapping[str, Choice]) -> SystemOneResponse:
        async with AsyncTypeSafeClient(
            api_key=key, model=MODEL, timeout=TIMEOUT_S,
            retry=RetryPolicy(max_retries=1, timeout=TIMEOUT_S),
        ) as client:
            return await client.system_one(state, questions)

    return ask


async def judge_returns(
    players: Iterable[Player],
    articles: Iterable[NewsArticle],
    team_names: Mapping[str, str],
    *,
    now: datetime,
    fixtures: Iterable[Fixture] = (),
    window: timedelta = SIGNAL_WINDOW,
    ask: Ask | None = None,
) -> dict[str, ReturnSignal]:
    """Rückkehr-Signale je Spieler-ID; wer fehlt, hat kein klares, aktuelles „rueckkehr"."""
    global _pause_until
    pairs = news_pairs(list(players), articles, now - window, last_kickoffs(fixtures, now))
    open_pairs = [p for p in pairs if pair_key(p[0], p[1]) not in _CACHE][:MAX_PAIRS]

    #: (Antwort, Anfrage-ID, Dauer) fürs Protokoll — geschrieben NACH der Entscheidung.
    asked: tuple[SystemOneResponse, str, int] | None = None
    if open_pairs and time.monotonic() >= _pause_until:
        ask = ask or _default_ask()
        if ask is not None:
            batch_id, started = jev_journal.new_batch_id(), time.monotonic()
            try:
                async with asyncio.timeout(TIMEOUT_S):
                    response = await ask(
                        pairs_state(open_pairs, team_names), _questions(len(open_pairs))
                    )
                latency_ms = jev_journal.elapsed_ms(started)
                if len(_CACHE) > _CACHE_MAX:
                    _CACHE.clear()
                for i, (player, article, _) in enumerate(open_pairs):
                    answer = valid_choice(response.choices.get(f"ev_p{i}"), _CRITERIA)
                    if answer is not None:  # sonst beim nächsten Lauf erneut
                        _CACHE[pair_key(player, article)] = answer
                asked = (response, batch_id, latency_ms)
            except Exception as exc:  # fail-safe: ohne Urteil kein Signal
                _pause_until = time.monotonic() + FAILURE_PAUSE_S
                log.warning("TypeSafe-Rückkehr nicht verfügbar (%s) — kein Signal, Pause %d s",
                            type(exc).__name__, int(FAILURE_PAUSE_S))
                jev_journal.record(lambda error=exc: [jev_journal.failure_entry(
                    kind=jev_journal.KIND_RETURN, purpose=PURPOSE, batch_size=len(open_pairs),
                    latency_ms=jev_journal.elapsed_ms(started), error=error,
                    at=datetime.now(UTC), batch_id=batch_id, model=MODEL,
                    pause_s=FAILURE_PAUSE_S,
                )])

    signals: dict[str, ReturnSignal] = {}
    decisive: dict[str, tuple[str, str]] = {}
    for player, article, published in pairs:  # jüngster Artikel zuerst
        answer = _CACHE.get(pair_key(player, article))
        if player.id in decisive or answer is None:
            continue
        label, probability = answer
        if label == ABSENCE and probability >= MIN_P_CANCEL:
            decisive[player.id] = pair_key(player, article)  # jüngerer Ausfall hebt auf
        elif label == RETURN and probability >= MIN_P_RETURN:
            decisive[player.id] = pair_key(player, article)
            signals[player.id] = ReturnSignal(
                player.id, probability, article.title, article.url, published, article.source,
            )
    if asked is not None:
        response, batch_id, latency_ms = asked
        jev_journal.record(lambda: _journal_rows(
            open_pairs, response, team_names, at=datetime.now(UTC), batch_id=batch_id,
            latency_ms=latency_ms, decisive=decisive,
        ))
    return signals
