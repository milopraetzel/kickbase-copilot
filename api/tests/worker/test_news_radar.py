"""News-Radar (Schritt 4 des Jev-Umbaus, Emil 30.09.2026: „bau Schritt 4 mit den News").

Rein lesender Morgenbericht: Kaufsignale (Kickbase-Listings mit klarer Rückkehr nach
Verletzung oder Sperre) und Kader-Warnungen (eigene Spieler mit klarem „fehlt"). Die
Urteile selbst prüfen tests/intel/test_news_signal.py und test_role_judge.py an echten
Jev-Antworten; hier geht es um Auswahl, Bericht und dass der Job nur liest.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import kickbase_agent.config as cfg
from kickbase_agent.db.models import Base, NewsItem, Report
from kickbase_agent.intel.news_signal import ReturnSignal
from kickbase_agent.intel.role_judge import RoleVerdict
from kickbase_agent.kickbase.models import MarketPlayer, Player, SquadPlayer
from kickbase_agent.news.models import NewsArticle
from kickbase_agent.news.store import persist_articles
from kickbase_agent.worker import news_radar as nr

NOW = datetime(2026, 9, 30, 6, 11, tzinfo=UTC)  # 08:11 Berlin
_STORED = NewsArticle(source="kicker", url="https://www.kicker.de/alt", title="Alt gespeichert",
                      body="", published_at=NOW - timedelta(hours=20))
#: 60 h alt: für Rückkehr-Signale zu alt (48 h), für Kader-Warnungen noch im Fenster (96 h).
_STORED_60H = NewsArticle(source="kicker", url="https://www.kicker.de/vor60",
                          title="Vor 60 Stunden", body="",
                          published_at=NOW - timedelta(hours=60))


def _player(pid: str, last: str, *, team: str = "3", pos: int = 3, mv: int = 5_000_000,
            prob: int | None = 2) -> Player:
    return Player(id=pid, first_name="Vor", last_name=last, team_id=team, position=pos,
                  market_value=mv, lineup_prob=prob, fitness_status=0)


class _ReadOnlyClient:
    """Nur die vier Lesemethoden; jedes andere Attribut ist ein Testfehler."""

    def __init__(self, *, squad=(), market=()):
        self._squad = list(squad)
        self._market = list(market)

    async def get_squad(self, _lg):
        return self._squad

    async def get_market(self, _lg):
        return self._market

    async def get_team_names(self):
        return {"3": "Dortmund", "10": "Bremen"}

    async def get_matchdays(self):
        return []

    def __getattr__(self, name: str):
        raise AssertionError(f"News-Radar darf {name!r} nicht aufrufen — er liest nur.")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(nr, "SessionLocal", factory)
    with factory() as session:
        persist_articles(session, [_STORED, _STORED_60H])
    return factory


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    # Die Judges sind in diesen Tests Attrappen; ein Schlüssel ist „geladen".
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-schluessel")
    cfg._settings = None
    yield
    cfg._settings = None


def _world(monkeypatch, *, fresh=(), rss_error: bool = False, returns=None, absences=None,
           extra_market=(), extra_squad=()):
    own = _player("mein", "Weiser", team="10", pos=2)
    auction = _player("auk", "Karetsas")
    manager = _player("mgr", "Brandt")
    client = _ReadOnlyClient(
        squad=[SquadPlayer(player=own), *(SquadPlayer(player=p) for p in extra_squad)],
        market=[
            MarketPlayer(player=auction, price=5_100_000, expires_at=NOW + timedelta(hours=9)),
            MarketPlayer(player=manager, price=6_000_000, expires_at=NOW - timedelta(days=1),
                         seller_id="rival-7"),
            # Eigene gelistete Spieler stehen bei Kickbase in Kader UND Markt.
            MarketPlayer(player=own, price=4_000_000, expires_at=NOW + timedelta(hours=5)),
            *(MarketPlayer(player=p, price=p.market_value, expires_at=NOW + timedelta(hours=6))
              for p in extra_market),
        ],
    )

    async def factory():
        return client, SimpleNamespace(league_id="L1", user_id="U1")

    monkeypatch.setattr(nr, "make_kickbase_client", factory)

    class _Rss:
        async def fetch(self):
            if rss_error:
                raise RuntimeError("kicker nicht erreichbar")
            return list(fresh)

    monkeypatch.setattr(nr, "KickerRSSClient", _Rss)
    seen: dict = {"role_calls": []}

    async def fake_returns(players, articles, team_names, *, now, fixtures):
        players, articles = list(players), list(articles)
        seen["returns"] = ([p.id for p in players], [a.title for a in articles])
        return {pid: signal for pid, signal in (returns or {}).items()
                if pid in {p.id for p in players}}

    async def fake_roles(players, articles, team_names, *, now, fixtures, **kwargs):
        players, articles = list(players), list(articles)
        seen["role_calls"].append(([p.id for p in players], [a.title for a in articles], kwargs))
        seen.setdefault("roles", ([p.id for p in players], [a.title for a in articles]))
        return {pid: verdict for pid, verdict in (absences or {}).items()
                if pid in {p.id for p in players}}

    monkeypatch.setattr(nr, "judge_returns", fake_returns)
    monkeypatch.setattr(nr, "judge_roles", fake_roles)
    return seen


_RETURN = ReturnSignal(
    player_id="auk", probability=0.996,
    title="Comeback in Stuttgart? Karetsas zurück im Mannschaftstraining",
    url="https://www.kicker.de/karetsas", published_at=NOW - timedelta(hours=14),
)
_ABSENT = RoleVerdict(
    player_id="mein", role="fehlt", probability=0.99,
    title="Bitter für Werder: Weiser fällt vorerst aus", url="https://www.kicker.de/weiser",
    published_at=NOW - timedelta(hours=10),
)


def _report(db) -> Report:
    with db() as session:
        return session.scalars(select(Report)).one()


async def test_the_radar_reports_returns_on_the_market_and_absences_in_the_squad(db, monkeypatch):
    seen = _world(monkeypatch, returns={"auk": _RETURN, "mgr": _RETURN},
                  absences={"mein": _ABSENT})

    assert await nr.run_news_radar(None, now=NOW) == "ok:1/1"

    # Kaufsignale nur für Kickbase-Listings, nie für eigene oder Manager-Angebote.
    assert seen["returns"][0] == ["auk"]
    assert seen["roles"][0] == ["mein"]
    report = _report(db)
    assert report.kind == "recherche" and report.source == nr.SOURCE
    assert report.title == "News-Radar 30.09.2026"
    assert report.created_at.replace(tzinfo=UTC) == NOW
    [kauf] = report.meta["kauf"]
    assert kauf["player_id"] == "auk" and kauf["mv"] == 5_000_000
    assert kauf["listing"]["price"] == 5_100_000
    [kader] = report.meta["kader"]
    assert kader["player_id"] == "mein" and kader["title"] == _ABSENT.title
    assert "Karetsas zurück im Mannschaftstraining" in report.body
    assert "Weiser fällt vorerst aus" in report.body
    assert nr.CLOSING_LINE in report.body
    assert report.meta.get("hinweis") is None


async def test_fresh_kicker_teasers_are_judged_but_not_stored(db, monkeypatch):
    """Nach-Review 30.09.2026: Speicherte der Radar, sähe der News-Bericht 10:33 die
    Meldungen der Nacht nicht mehr als neu, und die Engine plante wegen ihm neu."""
    fresh = NewsArticle(source="kicker", url="https://www.kicker.de/neu", title="Neu am Morgen",
                        body="", published_at=NOW - timedelta(hours=1))
    seen = _world(monkeypatch, fresh=[fresh, _STORED])  # der Feed wiederholt Bekanntes

    await nr.run_news_radar(None, now=NOW)

    with db() as session:
        titles = {row.title for row in session.scalars(select(NewsItem))}
    assert titles == {"Alt gespeichert", "Vor 60 Stunden"}  # nichts gespeichert
    assert sorted(seen["returns"][1]) == ["Alt gespeichert", "Neu am Morgen", "Vor 60 Stunden"]


async def test_a_kicker_failure_does_not_stop_the_radar_and_is_named(db, monkeypatch):
    seen = _world(monkeypatch, rss_error=True)

    assert await nr.run_news_radar(None, now=NOW) == "ok:0/0"
    assert "Alt gespeichert" in seen["returns"][1]  # mit dem Bestand weitergearbeitet
    assert "kicker nicht erreichbar" in _report(db).meta["hinweis"]


async def test_squad_warnings_see_ninety_six_hours(db, monkeypatch):
    seen = _world(monkeypatch)

    await nr.run_news_radar(None, now=NOW)

    assert "Vor 60 Stunden" in seen["roles"][1]


async def test_the_radar_never_pauses_the_engine_judge(db, monkeypatch):
    seen = _world(monkeypatch, returns={"auk": _RETURN})

    await nr.run_news_radar(None, now=NOW)

    assert seen["role_calls"] and all(
        kwargs.get("pause_on_failure") is False for _, _, kwargs in seen["role_calls"]
    )


async def test_the_radar_names_its_purpose_for_the_jev_journal(db, monkeypatch):
    seen = _world(monkeypatch, returns={"auk": _RETURN})

    await nr.run_news_radar(None, now=NOW)

    # Erst der eigene Kader (Warnung), dann das nächste Spiel der Rückkehrer.
    assert [kwargs.get("purpose") for _, _, kwargs in seen["role_calls"]] == [
        "kader", "naechstes_spiel"
    ]


async def test_a_returner_who_still_misses_the_next_match_is_marked(db, monkeypatch):
    still_out = RoleVerdict(player_id="auk", role="fehlt", probability=0.97,
                            title="Karetsas noch nicht im Kader", url="u",
                            published_at=NOW - timedelta(hours=3))
    _world(monkeypatch, returns={"auk": _RETURN}, absences={"auk": still_out})

    await nr.run_news_radar(None, now=NOW)

    [kauf] = _report(db).meta["kauf"]
    assert kauf["naechstes_spiel"] == "fehlt"
    assert "nächstes Spiel: fehlt noch" in nr.buy_line(kauf)


async def test_all_signals_stay_in_meta_and_squad_warnings_come_first(db, monkeypatch):
    market = [_player(f"m{i}", f"Markt{i}", team="3") for i in range(6)]
    squad = [_player(f"k{i}", f"Kader{i}", team="10") for i in range(5)]
    returns = {p.id: ReturnSignal(p.id, 0.99, f"{p.last_name} zurück im Training", "u",
                                  NOW - timedelta(hours=2 + n)) for n, p in enumerate(market)}
    absences = {p.id: RoleVerdict(p.id, "fehlt", 0.99, f"{p.last_name} fällt aus", "u",
                                  NOW - timedelta(hours=2 + n)) for n, p in enumerate(squad)}
    _world(monkeypatch, returns=returns, absences=absences, extra_market=market,
           extra_squad=squad)

    assert await nr.run_news_radar(None, now=NOW) == "ok:6/5"

    report = _report(db)
    # Meta behält alle Signale (Grundlage der Nachmessung), der Cockpit-Bericht zeigt alle;
    # gekürzt wird nur in der Tageszusammenfassung.
    assert len(report.meta["kauf"]) == 6 and len(report.meta["kader"]) == 5
    assert report.meta["kauf"][0]["player_id"] == "m0"  # jüngste Meldung zuerst
    assert report.meta["kader"][0]["player_id"] == "k0"
    assert report.body.index("Kader-Warnungen") < report.body.index("Kaufsignale")
    assert report.body.count("• Kauf?") == 6 and report.body.count("• Kader:") == 5


async def test_unjudged_pairs_are_named(db, monkeypatch):
    _world(monkeypatch)
    monkeypatch.setattr(nr, "count_unjudged", lambda *args, **kwargs: 3)

    await nr.run_news_radar(None, now=NOW)

    assert "3 Meldungen nicht beurteilt" in _report(db).meta["hinweis"]


async def test_zero_articles_are_named(db, monkeypatch):
    _world(monkeypatch, rss_error=True)
    monkeypatch.setattr(nr, "load_recent_articles", lambda session, since: [])

    await nr.run_news_radar(None, now=NOW)

    assert "keine kicker-Anreißer" in _report(db).meta["hinweis"]


async def test_a_missing_key_is_named_instead_of_pretending_quiet_news(db, monkeypatch):
    _world(monkeypatch)
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    cfg._settings = None

    await nr.run_news_radar(None, now=NOW)

    report = _report(db)
    assert "Schlüssel" in report.meta["hinweis"]
    assert report.meta["hinweis"] in report.body


async def test_a_paused_judge_is_named(db, monkeypatch):
    _world(monkeypatch)
    monkeypatch.setattr(nr, "jev_paused", lambda: True)

    await nr.run_news_radar(None, now=NOW)

    assert "nicht erreichbar" in _report(db).meta["hinweis"]


def test_lines_name_player_money_listing_article_and_probability():
    buy = nr.buy_row(_RETURN, MarketPlayer(player=_player("auk", "Karetsas"), price=5_100_000,
                                           expires_at=NOW + timedelta(hours=9)),
                     {"3": "Dortmund"})
    squad = nr.squad_row(_ABSENT, _player("mein", "Weiser", team="10", pos=2), {"10": "Bremen"})

    buy_text = nr.buy_line(buy)
    assert "Karetsas" in buy_text and "Dortmund" in buy_text and "MW 5,00 M" in buy_text
    assert "Auktion" in buy_text and "Mi 17:11" in buy_text  # Ablauf in Berliner Zeit
    assert "Rückkehr" in buy_text and "kicker 29.09." in buy_text and "100 %" in buy_text
    squad_text = nr.squad_line(squad)
    assert "Weiser" in squad_text and "Bremen" in squad_text and "fehlt" in squad_text
    assert "Weiser fällt vorerst aus" in squad_text and "99 %" in squad_text


def test_count_unjudged_covers_market_squad_and_the_next_match_check():
    """Nach-Review: Scheitert die Frage „nächstes Spiel" für einen Rückkehrer, darf der
    Bericht nicht still bleiben — auch diese Paare zählen."""
    market = _player("auk", "Karetsas")
    own = _player("mein", "Weiser", team="10", pos=2)
    articles = [
        NewsArticle(source="kicker", url="u1", title="Karetsas zurück im Training", body="",
                    published_at=NOW - timedelta(hours=5)),
        NewsArticle(source="kicker", url="u2", title="Weiser fällt aus", body="",
                    published_at=NOW - timedelta(hours=5)),
    ]
    nr.news_signal.clear_cache()
    nr.role_judge.clear_cache()

    # Rückkehr-Frage (Markt) + Rollenfrage (Kader) + Rollenfrage (Rückkehrer) = 3 Paare.
    assert nr.count_unjudged([market], [own], [market], articles, now=NOW, fixtures=[]) == 3
