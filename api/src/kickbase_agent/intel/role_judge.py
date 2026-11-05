"""Rolle aus Nachrichten per TypeSafe — ein klares „fehlt" sperrt einen NEUEN Kauf.

Der Mannschaftsplan kannte bisher Kickbase-Stufe, LigaInsider-Status und Minuten, aber
nicht, was kicker schreibt („fällt vorerst aus", „nur die Nummer 3"). Hier sucht der Code
zu jedem Kaufkandidaten die Artikel der letzten `NEWS_WINDOW`, die seinen Nachnamen
nennen, und TypeSafe (System One, Modell Jev) beurteilt jedes Paar (Spieler, Artikel) mit
EINER Choice-Frage — startet · bank · fehlt · kein_hinweis —, bezogen auf das nächste
Bundesligaspiel seines Vereins. Alle offenen Paare gehen in EINE Anfrage.

Der Code entscheidet, nicht das Modell (Emil 29.09.2026): Es zählt der jüngste Artikel mit
eindeutigem Urteil — Wahrscheinlichkeit des gewählten Labels ≥ `MIN_P_ROLE`, nicht
kein_hinweis. Bewusst die Label-Wahrscheinlichkeit, nicht die `confidence` der Choice: die
misst die Konzentration der ganzen Verteilung, nicht dieses Urteil. Nur „fehlt" sperrt
einen Kauf. „bank" und „startet" werden mitgefragt, damit ein jüngerer eindeutiger Artikel
(„zurück im Training, zunächst Joker") ein älteres „fehlt" aufhebt — sperren können sie
nicht: Die Bank-Wahrscheinlichkeit derselben Anreißer sprang in der Messung vom 29.09.2026
je nach Anfrage zwischen 0,69 und 0,89; die Bankrolle bleibt bei LigaInsider und der
Kickbase-Stufe. Welche Ziele beurteilt werden, entscheidet
`cli/plan.py`: neue Ziele und eigene, noch nicht ausgeführte Engine-Vorhaben — nie ein
Live-Gebot, nie ein manuelles oder angepasstes Vorhaben. Ein unklares oder ungültiges
Urteil sperrt nichts: Jev verschärft nur, er lockert keine Regel. Fällt TypeSafe aus oder
fehlt der Schlüssel, entsteht kein neues Urteil; ein bereits gespeichertes klares Urteil zu
einem älteren Artikel gilt weiter, bis ein jüngerer Artikel eindeutig beurteilt ist, der
Artikel aus dem Fenster fällt oder vor dem letzten Spiel des Vereins liegt, oder bis zum
Neustart des Prozesses (der Cache lebt nur im Speicher).

Zeitanker (Review 29.09.2026): Ein Artikel von vor dem letzten Bundesligaspiel des Vereins
handelt von diesem Spiel und zählt nicht mehr — sonst sperrte „fehlt gesperrt" vom Freitag
noch am Dienstag. Deshalb kein Datum im State: Die Zeit regelt der Code.

Betriebsschutz wie bei der Verletzungs-Schwere (`scouts/ligainsider/severity_judge.py`):
harte Obergrenze `TIMEOUT_S` für die ganze Anfrage, `FAILURE_PAUSE_S` Pause nach einem
Ausfall, nur endliche Wahrscheinlichkeiten in [0, 1] und bekannte Labels, Cache je
(Spieler, Artikel), höchstens `MAX_PAIRS` Paare je Anfrage (die jüngsten zuerst).

Grenzen: Der kicker-Feed liefert nur Anreißer; wer darin nicht genannt wird, bleibt
unsichtbar. Der Namensabgleich ist wörtlich (Nachname ohne Akzente, auch mit Genitiv-s)
und trifft auch Namensvettern und Alltagswörter („neuer", „stark"); ob wirklich dieser
Spieler gemeint ist, prüft Jev an vollem Namen und Verein. Die Schwelle ist ein
Startwert, geprüft an der echten Antwort vom 29.09.2026
(`tests/fixtures/typesafe/role_response.json`, mit Namensvettern), keine Naturkonstante.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

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
#: Der Engine-Zyklus wartet nicht auf TypeSafe — hart für die ganze Anfrage.
TIMEOUT_S = 10.0
#: Nach einem Ausfall so lange nicht fragen, statt jeden Planlauf zu bremsen.
FAILURE_PAUSE_S = 600.0
#: Nur ein klares Urteil zählt; darunter plant die Engine wie ohne Artikel. Messung
#: 29.09.2026 mit dieser Frage (je 10 Abfragen, gebündelt und einzeln): echte Ausfälle
#: 0,95–1,00, Länderspiel-Meldungen, Namensvettern und Fragen („Fällt X aus?") höchstens
#: 0,83. 0,95 statt 0,9 (Nach-Review): Abstand zum Rauschen (±0,05) auf der gefährlichen
#: Seite — eine Fehlsperre kann den einzigen Elf-Füller kosten, einen verpassten Ausfall
#: melden LigaInsider oder Kickbase meist später.
MIN_P_ROLE = 0.95
#: So alt darf ein Artikel sein; die nächste Aufstellungsfrist liegt meist Tage voraus.
NEWS_WINDOW = timedelta(hours=96)
#: Obergrenze je Anfrage; die jüngsten Paare zuerst, der Rest im nächsten Planlauf.
MAX_PAIRS = 40

#: Nur dieses Urteil sperrt einen neuen Kauf (siehe Moduldoku: „bank" ist zu unstet).
BLOCKING = frozenset({"fehlt"})
_NO_SIGN = "kein_hinweis"
_CRITERIA = {
    "startet": (
        "He is expected to start for his club: a regular starter, in or returning to the "
        "starting eleven, or too good for the bench."
    ),
    "bank": (
        "He is expected to be only a substitute or backup for his club: he lost his "
        "starting place, sits on the bench, is behind a competitor in the pecking order, "
        "or rarely plays."
    ),
    "fehlt": (
        "He will miss his club's next Bundesliga match: injury, illness, a Bundesliga "
        "suspension, or not in the squad. Missing national team, cup or European matches "
        "or being away with the national team does not count."
    ),
    _NO_SIGN: (
        "The teaser is not about this player: it means another person with the same name "
        "(for example a coach or a teammate whose first name is his surname), or the word is "
        "an ordinary German word. Or it gives no clear sign of his role in his club's next "
        "Bundesliga match, for example interviews, history, private matters, or national "
        "team news only."
    ),
}
_BERLIN = ZoneInfo("Europe/Berlin")

#: (Spieler-ID, Artikel-Hash) → (Label, Wahrscheinlichkeit). Nur gültige Antworten.
_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_CACHE_MAX = 5000
#: Monotone Uhrzeit, bis zu der nach einem Ausfall nicht gefragt wird.
_pause_until = 0.0


@dataclass(frozen=True)
class RoleVerdict:
    """Ein sperrendes Urteil mit dem Artikel, auf dem es beruht."""

    player_id: str
    role: str
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
    window: timedelta = NEWS_WINDOW,
) -> int:
    """Wie viele Paare im Fenster noch ohne gültiges Urteil sind (Ausfall, Obergrenze,
    ungültige Antwort) — damit ein Bericht keine ruhige Lage vortäuscht."""
    pairs = news_pairs(list(players), articles, now - window, last_kickoffs(fixtures, now))
    return sum(1 for player, article, _ in pairs if pair_key(player, article) not in _CACHE)


def role_note(verdict: RoleVerdict) -> str:
    """Quelle, Datum (Berlin), Titel und Urteil — für Beleg und Prognosegrund."""
    when = verdict.published_at.astimezone(_BERLIN).strftime("%d.%m.")
    return (
        f"{verdict.source} {when}: „{verdict.title}“ — Jev liest „{verdict.role}“ "
        f"({round(verdict.probability * 100)} %)"
    )


def role_rejection(verdict: RoleVerdict) -> str:
    """Ablehnungsgrund im Kaufbeleg."""
    return f"{role_note(verdict)}; kein neuer Kauf."


def _questions(count: int) -> dict[str, Choice]:
    return {
        f"role_p{i}": Choice(
            instructions=(
                f"`pairs.p{i}.title` and `pairs.p{i}.text` are a German football news "
                f"teaser. What does it say about the role of `pairs.p{i}.player` (club: "
                f"`pairs.p{i}.club`) in his club's next Bundesliga match? Only the club's "
                "Bundesliga matches count; national team, cup and European matches and "
                "international breaks do not."
            ),
            criteria=_CRITERIA,
        )
        for i in range(count)
    }


#: Name der Folge eines klaren „fehlt" je Aufrufer — nur fürs Jev-Protokoll; gesperrt bzw.
#: gewarnt wird weiter im Plan und im News-Radar.
_ABSENCE_OUTCOMES = {
    "kaufpruefung": ("sperrt", "„fehlt“ ≥ {t} sperrt einen neuen Kauf (der Artikel steht im "
                               "Kaufbeleg)."),
    "kader": ("warnung", "„fehlt“ ≥ {t} im eigenen Kader: Warnung im News-Radar."),
    "naechstes_spiel": ("fehlt_noch", "„fehlt“ ≥ {t}: Der Rückkehrer fehlt im nächsten Spiel "
                                      "noch."),
    "handel": ("sperrt", "„fehlt“ ≥ {t} nimmt den Spieler aus dem Handels-Radar (der Artikel "
                         "steht im Bericht)."),
}


def journal_outcome(answer: tuple[str, float] | None, purpose: str) -> tuple[str, str]:
    """Folge und Regeltext EINES Urteils fürs Jev-Protokoll — mit genau den Konstanten,
    nach denen `judge_roles` entscheidet. Welcher Artikel am Ende zählt (der jüngste
    eindeutige), entscheidet weiter `judge_roles`."""
    threshold = fmt_p(MIN_P_ROLE)
    if answer is None:
        return jev_journal.OUTCOME_INVALID, (
            "Ungültige Antwort zählt nicht; beim nächsten Lauf wird erneut gefragt."
        )
    role, probability = answer
    if role == _NO_SIGN or probability < MIN_P_ROLE:
        return "kein_einfluss", f"Unter {threshold} oder kein Hinweis: zählt nicht."
    if role in BLOCKING:
        outcome, rule = _ABSENCE_OUTCOMES.get(purpose, _ABSENCE_OUTCOMES["kaufpruefung"])
        return outcome, rule.format(t=threshold)
    return "eindeutig", f"„{role}“ ≥ {threshold} hebt ein älteres „fehlt“ auf, sperrt aber nie."


def _is_decisive(answer: tuple[str, float] | None) -> bool:
    """Ein eindeutiges Urteil — genau die Bedingung der Entscheidungsschleife."""
    return answer is not None and answer[0] != _NO_SIGN and answer[1] >= MIN_P_ROLE


def _journal_rows(
    pairs: list[Pair],
    response: SystemOneResponse,
    team_names: Mapping[str, str],
    *,
    purpose: str,
    at: datetime,
    batch_id: str,
    latency_ms: int,
    decisive: Mapping[str, tuple[str, str]],
) -> list[JevEntry]:
    """Eine Protokollzeile je gestellter Frage: was Jev las, was Jev sagte, was folgt.

    `decisive` nennt je Spieler das Paar, das `judge_roles` entscheiden ließ (jüngster
    eindeutiger Artikel). Ein anderes eindeutiges Urteil zum selben Spieler ist überholt
    (Review 01.10.: sonst stand „sperrt" da, wo ein jüngerer Artikel die Sperre aufhob)."""
    rows = []
    model = jev_journal.answering_model(response, MODEL)
    for i, (player, article, published) in enumerate(pairs):
        key = f"role_p{i}"
        given = response.choices.get(key)
        answer = valid_choice(given, _CRITERIA)
        probabilities, confidence, raw = jev_journal.answer_fields(given)
        outcome, rule = journal_outcome(answer, purpose)
        if _is_decisive(answer) and decisive.get(player.id) != pair_key(player, article):
            outcome, rule = jev_journal.OUTCOME_SUPERSEDED, jev_journal.RULE_SUPERSEDED
        chosen = getattr(given, "choice", None)
        rows.append(JevEntry(
            created_at=at, kind=jev_journal.KIND_ROLE, purpose=purpose,
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


async def judge_roles(
    players: Iterable[Player],
    articles: Iterable[NewsArticle],
    team_names: Mapping[str, str],
    *,
    now: datetime,
    fixtures: Iterable[Fixture] = (),
    window: timedelta = NEWS_WINDOW,
    ask: Ask | None = None,
    pause_on_failure: bool = True,
    purpose: str = "kaufpruefung",
) -> dict[str, RoleVerdict]:
    """Sperrende Urteile („fehlt") je Spieler-ID; wer fehlt, hat kein klares „fehlt".

    `pause_on_failure=False` für Nebenaufrufer im selben Prozess (News-Radar): Ihr Ausfall
    darf die Kaufsperre der Engine nicht zehn Minuten lang abschalten (Nach-Review
    30.09.2026). Die Engine ruft mit Pause. `purpose` benennt im Jev-Protokoll, wofür
    gefragt wurde (kaufpruefung · kader · naechstes_spiel · handel); am Urteil ändert es
    nichts."""
    global _pause_until
    pairs = news_pairs(list(players), articles, now - window, last_kickoffs(fixtures, now))
    open_pairs = [p for p in pairs if pair_key(p[0], p[1]) not in _CACHE][:MAX_PAIRS]

    #: (Antwort, Anfrage-ID, Dauer) fürs Protokoll — geschrieben NACH der Entscheidung,
    #: damit jede Zeile weiß, ob ihr Artikel entschied oder überholt ist.
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
                    answer = valid_choice(response.choices.get(f"role_p{i}"), _CRITERIA)
                    if answer is not None:  # sonst beim nächsten Planlauf erneut
                        _CACHE[pair_key(player, article)] = answer
                asked = (response, batch_id, latency_ms)
            except Exception as exc:  # fail-safe: ohne Urteil wird nichts gesperrt
                if pause_on_failure:
                    _pause_until = time.monotonic() + FAILURE_PAUSE_S
                log.warning("TypeSafe-Rolle nicht verfügbar (%s) — keine News-Sperre%s",
                            type(exc).__name__,
                            f", Pause {int(FAILURE_PAUSE_S)} s" if pause_on_failure else "")
                jev_journal.record(lambda error=exc: [jev_journal.failure_entry(
                    kind=jev_journal.KIND_ROLE, purpose=purpose, batch_size=len(open_pairs),
                    latency_ms=jev_journal.elapsed_ms(started), error=error,
                    at=datetime.now(UTC), batch_id=batch_id, model=MODEL,
                    pause_s=FAILURE_PAUSE_S if pause_on_failure else None,
                )])

    verdicts: dict[str, RoleVerdict] = {}
    decisive: dict[str, tuple[str, str]] = {}
    for player, article, published in pairs:  # jüngster Artikel zuerst
        answer = _CACHE.get(pair_key(player, article))
        if player.id in decisive or answer is None:
            continue
        role, probability = answer
        if role == _NO_SIGN or probability < MIN_P_ROLE:
            continue
        decisive[player.id] = pair_key(player, article)
        if role in BLOCKING:
            verdicts[player.id] = RoleVerdict(
                player.id, role, probability, article.title, article.url, published,
                article.source,
            )
    if asked is not None:
        response, batch_id, latency_ms = asked
        jev_journal.record(lambda: _journal_rows(
            open_pairs, response, team_names, purpose=purpose, at=datetime.now(UTC),
            batch_id=batch_id, latency_ms=latency_ms, decisive=decisive,
        ))
    return verdicts
