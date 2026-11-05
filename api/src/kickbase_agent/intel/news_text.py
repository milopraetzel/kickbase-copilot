"""Gemeinsame Bausteine für Jev-Urteile über kicker-Anreißer (Rolle, Rückkehr).

Rein und ohne Zustand: Namensabgleich, Paare (Spieler, Artikel) im Zeitfenster nach dem
letzten Bundesligaspiel des Vereins, der State für TypeSafe und die Zahlenprüfung. Fragen,
Schwellen, Cache und Betriebsschutz stehen in den Judges selbst (`intel/role_judge.py`,
`intel/news_signal.py`) — jeder mit eigener Messung und eigenem Decision-Record.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from functools import lru_cache

from kickbase_agent.kickbase.models import Fixture, Player
from kickbase_agent.news.models import NewsArticle
from kickbase_agent.scouts.ligainsider.teams import TEAMS

#: Kürzere Nachnamen treffen zu oft fremde Wörter.
MIN_NAME_LEN = 3
#: Beschreibung des States; beide Judges wurden mit genau diesem Text gemessen.
TEASER_SOURCE = (
    "Teasers of German football news from kicker.de. Each pair names one Bundesliga "
    "player with his full name and club, and one teaser that contains a word matching his "
    "surname. That word may refer to another person or be an ordinary German word."
)
_TRANSLIT = str.maketrans({"ø": "o", "æ": "ae", "å": "a", "đ": "d", "ł": "l", "ı": "i",
                           "œ": "oe", "’": "'", "‘": "'", "‐": "-", "‑": "-"})

Pair = tuple[Player, NewsArticle, datetime]


def normalize(text: str) -> str:
    """Kleinschreibung ohne Akzente: „Grønbæk" und „Grönbaek" werden gleich."""
    folded = text.casefold().translate(_TRANSLIT)
    return "".join(
        c for c in unicodedata.normalize("NFKD", folded) if not unicodedata.combining(c)
    )


@lru_cache(maxsize=4096)
def name_pattern(last_name: str) -> re.Pattern[str] | None:
    """Ganzes Wort, optional mit Genitiv-s („Kanes Serie"); None bei zu kurzen Namen."""
    name = normalize(last_name).strip()
    if len(name) < MIN_NAME_LEN:
        return None
    return re.compile(rf"(?<!\w){re.escape(name)}s?(?!\w)")


def mentions(last_name: str, text: str) -> bool:
    """Nennt der Text diesen Nachnamen als ganzes Wort?"""
    pattern = name_pattern(last_name)
    return pattern is not None and pattern.search(normalize(text)) is not None


def as_utc(moment: datetime) -> datetime:
    """Zonenlos gilt als UTC (news_items speichert UTC)."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def last_kickoffs(fixtures: Iterable[Fixture], now: datetime) -> dict[str, datetime]:
    """Je Verein der Anpfiff seines letzten Bundesligaspiels bis `now`."""
    last: dict[str, datetime] = {}
    for fixture in fixtures:
        if fixture.kickoff is None or fixture.kickoff > now:
            continue
        for team_id in (fixture.home_team_id, fixture.away_team_id):
            if team_id not in last or fixture.kickoff > last[team_id]:
                last[team_id] = fixture.kickoff
    return last


def news_pairs(
    players: list[Player],
    articles: Iterable[NewsArticle],
    since: datetime,
    kickoffs: Mapping[str, datetime],
) -> list[Pair]:
    """Alle (Spieler, Artikel)-Paare im Fenster und nach dem letzten Spiel des Vereins,
    jüngster Artikel zuerst."""
    recent = sorted(
        ((a, as_utc(a.published_at)) for a in articles if as_utc(a.published_at) >= since),
        key=lambda item: item[1],
        reverse=True,
    )
    out = []
    for article, published in recent:
        text = normalize(f"{article.title}\n{article.body}")
        for player in players:
            kickoff = kickoffs.get(player.team_id)
            if kickoff is not None and published < kickoff:
                continue
            pattern = name_pattern(player.last_name)
            if pattern is not None and pattern.search(text):
                out.append((player, article, published))
    return out


def pair_key(player: Player, article: NewsArticle) -> tuple[str, str]:
    """Cache-Schlüssel eines Paars: Spieler-ID und Artikel-Hash."""
    return player.id, article.content_hash()


def club_name(team_id: str, team_names: Mapping[str, str]) -> str:
    """Voller LigaInsider-Vereinsname, sonst der Kickbase-Name."""
    team = TEAMS.get(team_id)
    return team.li_name if team else team_names.get(team_id, "")


def pairs_state(pairs: list[Pair], team_names: Mapping[str, str]) -> dict:
    """State für TypeSafe: je Paar Spieler, Verein, Titel und Anreißer."""
    return {
        "source": TEASER_SOURCE,
        "pairs": {
            f"p{i}": {
                "player": f"{player.first_name} {player.last_name}".strip(),
                "club": club_name(player.team_id, team_names),
                "title": article.title,
                "text": article.body,
            }
            for i, (player, article, _) in enumerate(pairs)
        },
    }


def is_probability(value: object) -> bool:
    """Endliche Zahl in [0, 1], kein bool."""
    return (
        isinstance(value, int | float) and not isinstance(value, bool)
        and math.isfinite(value) and 0.0 <= value <= 1.0
    )


def valid_choice(answer: object, labels: Iterable[str]) -> tuple[str, float] | None:
    """Label und seine Wahrscheinlichkeit — nur bekannte Labels, nur Zahlen in [0, 1]."""
    choice = getattr(answer, "choice", None)
    probabilities = getattr(answer, "probabilities", None)
    if choice not in set(labels) or not isinstance(probabilities, Mapping) or not probabilities:
        return None
    if not all(is_probability(p) for p in probabilities.values()):
        return None
    return choice, float(probabilities.get(choice, 0.0))
