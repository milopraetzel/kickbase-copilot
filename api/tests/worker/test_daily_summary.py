"""Eine Discord-Nachricht am Tag (Emil, 23.09.2026): „Discord soll wieder senden. Aber
nur einmal am Tag eine Zusammenfassung".

Der Tagestest fährt die echten Jobs gegen einen echten `DiscordChannel` im Digest-Modus
(Produktionsdefault) und zählt jedes `channel.send` des Fake-Kanals: Steiger-Radar 08:10,
Ergebnis-Wächter mit zwei Befunden, Tageszusammenfassung 08:15, eine Worker-
Fehlermeldung und das Abend-Briefing 22:15 ergeben genau EINE Nachricht. Ausnahme
(Sicherheit): 24 h vor einer bekannten Spieltags-Frist stellt der Wächter Deckung und
Gleitpfad sofort zu.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import kickbase_agent.notify.report_store as report_store
import kickbase_agent.worker.daily_summary as ds
import kickbase_agent.worker.news_radar as nr
import kickbase_agent.worker.outcome_guard as og
import kickbase_agent.worker.steiger_radar as sr
from kickbase_agent.cli.plan import Plan
from kickbase_agent.db.models import Base, Intent, PlayerHistory, Report
from kickbase_agent.db.models import Player as DbPlayer
from kickbase_agent.kickbase.models import Fixture, MarketPlayer, Player, SquadPlayer
from kickbase_agent.notify.discord_channel import DiscordChannel
from kickbase_agent.worker.briefing import build_briefing
from kickbase_agent.worker.error_digest import FehlerBuendel

#: Donnerstag 24.09.2026, 08:15:30 Berlin (CEST = UTC+2) — Slot des Morgen-Briefings.
MORNING = datetime(2026, 9, 24, 6, 15, 30, tzinfo=UTC)
EVENING = datetime(2026, 9, 24, 20, 15, 30, tzinfo=UTC)
GUARD_EARLY = datetime(2026, 9, 24, 5, 9, tzinfo=UTC)  # 07:09 Berlin
RADAR_AT = datetime(2026, 9, 24, 6, 10, tzinfo=UTC)  # 08:10 Berlin
GUARD_NOON = datetime(2026, 9, 24, 10, 9, tzinfo=UTC)
#: Spieltags-Frist 60 h nach dem ersten Wächterlauf — ein normaler Tag, weit vor T-24 h.
KICKOFF = GUARD_EARLY + timedelta(hours=60)


# --- Fakes ------------------------------------------------------------------------


class FakeMessageable:
    """Discord-Kanal-Ersatz: zählt JEDES send()."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})


class _Bot:
    def __init__(self, messageable) -> None:
        self.messageable = messageable

    def get_channel(self, _id):
        return self.messageable


def _digest_channel(box: FakeMessageable) -> DiscordChannel:
    async def approve(_action_id):
        return SimpleNamespace(status="executed", message="")

    async def reject(_action_id):
        return SimpleNamespace(status="rejected", message="")

    return DiscordChannel(
        bot=_Bot(box), channel_id="42", allowed_user_id="111",
        on_approve=approve, on_reject=reject, digest_only=True,
    )


#: Positionen der Reihe nach (3-5-2); zehn Spieler lassen einen Elf-Platz offen.
_POSITIONS = (1, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4)


def _sp(pid: str, mv: int, position: int, *, fitness: int = 0) -> SquadPlayer:
    return SquadPlayer(
        player=Player(id=pid, first_name="T", last_name=pid, team_id="T1", position=position,
                      market_value=mv, lineup_prob=1, fitness_status=fitness),
        fitness_status=fitness,
    )


def _ten(mv: int = 3_000_000) -> list[SquadPlayer]:
    return [_sp(f"p{i}", mv, _POSITIONS[i]) for i in range(10)]


class _GuardClient:
    def __init__(self, *, squad, budget, kickoff):
        self.squad = squad
        self.budget = budget
        self.kickoff = kickoff

    async def get_matchdays(self):
        return [Fixture(matchday=5, home_team_id="1", away_team_id="2", kickoff=self.kickoff)]

    async def get_budget(self, _lg):
        return self.budget

    async def get_own_bids(self, _lg, _uid):
        return []

    async def get_squad(self, _lg):
        return self.squad

    async def get_market(self, _lg):
        return []


class _RadarClient:
    """Nur Lesemethoden — der Radar schreibt nie."""

    def __init__(self, market):
        self._market = market

    async def get_squad(self, _lg):
        return []

    async def get_market(self, _lg):
        return self._market

    async def get_budget(self, _lg):
        return 7_410_000

    async def get_player(self, _lg, pid):  # pragma: no cover — alle Kandidaten am Markt
        raise AssertionError(pid)


@pytest.fixture
def db(monkeypatch):
    """EINE In-Memory-DB hinter allen beteiligten Modulen."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (report_store, og, sr, ds):
        monkeypatch.setattr(module, "SessionLocal", factory)
    return factory


@pytest.fixture(autouse=True)
def _kein_netz(monkeypatch):
    async def _leer(_players):
        return {}

    monkeypatch.setattr(og, "fetch_li_statuses", _leer)


def _patch_guard(monkeypatch, client):
    async def fake_factory():
        return client, SimpleNamespace(league_id="L1", user_id="U1")

    monkeypatch.setattr(og, "make_kickbase_client", fake_factory)


def _patch_radar(monkeypatch, db_factory):
    with db_factory() as session:
        session.add(DbPlayer(id="auk", kickbase_player_id="auk", name="Radar Steiger",
                             position=3, team="2", current_market_value=3_300_000))
        session.add(PlayerHistory(player_id="auk", recorded_at=RADAR_AT - timedelta(days=3),
                                  market_value=3_000_000))
        session.add(PlayerHistory(player_id="auk", recorded_at=RADAR_AT,
                                  market_value=3_300_000))
        session.commit()
    market = [MarketPlayer(
        player=Player(id="auk", first_name="Radar", last_name="Steiger", team_id="2",
                      position=3, market_value=3_300_000, lineup_prob=2, fitness_status=0),
        price=3_350_000, expires_at=RADAR_AT + timedelta(hours=7),
    )]

    async def fake_factory():
        return _RadarClient(market), SimpleNamespace(league_id="L1", user_id="U1")

    monkeypatch.setattr(sr, "make_kickbase_client", fake_factory)


def _briefing():
    squad = [SquadPlayer(player=Player(id="1", first_name="", last_name="P1", position=3,
                                       market_value=30_000_000, team_id="1"))]
    plan = Plan(league_id="L1", budget=-1_000_000, buys=[], sells=[])
    return build_briefing("BASE", "u", [], squad, -1_000_000, 0, plan)


def _planned_bid(db_factory, *, execute_at: datetime) -> None:
    with db_factory() as session:
        session.add(Intent(created_at=GUARD_EARLY, updated_at=GUARD_EARLY, kind="bid",
                           player_id="v1", player_name="Veto Kandidat", amount=4_200_000,
                           execute_at=execute_at, status="planned"))
        session.commit()


# --- Der Tagestest ------------------------------------------------------------------


async def test_normaler_tag_genau_eine_discord_nachricht(db, monkeypatch):
    box = FakeMessageable()
    kanal = _digest_channel(box)
    _patch_guard(monkeypatch, _GuardClient(squad=_ten(), budget=-1_000_000, kickoff=KICKOFF))
    _patch_radar(monkeypatch, db)
    _planned_bid(db, execute_at=MORNING + timedelta(hours=5))

    # 07:09 Wächter: zwei Befunde (Gleitpfad + Deckung), 60 h vor der Frist.
    ergebnis = await og.run_outcome_guard(kanal, now=GUARD_EARLY)
    assert og.SLO_COVERAGE in ergebnis and og.SLO_SQUAD_PATH in ergebnis
    assert "alert" not in ergebnis
    # 08:10 Steiger-Radar speichert nur.
    assert await sr.run_steiger_radar(kanal, now=RADAR_AT) == "ok:1"
    # Eine Worker-Fehlermeldung (_safe) geht ebenfalls nur ins Archiv.
    text = FehlerBuendel().melden("Briefing", RuntimeError("Kickbase 503"), now=MORNING)
    await kanal.send_info(text)
    assert box.sent == []

    # 08:15 Tageszusammenfassung.
    await ds.publish_briefing(kanal, _briefing(), period="morning", now=MORNING)
    # Mittags derselbe rote Zustand, abends das Abend-Briefing.
    await og.run_outcome_guard(kanal, now=GUARD_NOON)
    await ds.publish_briefing(kanal, _briefing(), period="evening", now=EVENING)

    assert len(box.sent) == 1
    embed = box.sent[0]["embed"]
    beschreibung = embed.description
    assert "Tageszusammenfassung" in embed.title
    assert len(beschreibung) <= ds.SUMMARY_LIMIT
    assert "Radar Steiger" in beschreibung  # Radar-Zeile
    assert "Konto unter dem Anpfiff-Puffer" in beschreibung  # Wächterbefund Deckung
    assert "Kader unter dem Gleitpfad" in beschreibung  # Wächterbefund Gleitpfad
    assert "Veto Kandidat" in beschreibung and "Veto bis" in beschreibung

    with db() as session:
        befunde = session.scalars(
            select(Report).where(Report.source == og.FINDINGS_SOURCE)
        ).all()
        abend = session.scalars(
            select(Report).where(Report.kind == "briefing", Report.title.like("%Abendbericht%"))
        ).all()
    assert sorted(r.meta["slo"] for r in befunde) == sorted([og.SLO_COVERAGE, og.SLO_SQUAD_PATH])
    assert all(r.kind == "blocker" and r.meta["delivered"] is False for r in befunde)
    (abendbericht,) = abend
    assert abendbericht.meta["delivered"] is False


async def test_24h_vor_der_frist_kommen_deckung_und_gleitpfad_sofort(db, monkeypatch):
    """Gemerkt, aber nicht zugestellt (T-30 h) → ab T-24 h sofort; Statuswechsel nie."""
    box = FakeMessageable()
    kanal = _digest_channel(box)
    t0 = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
    kickoff = t0 + timedelta(hours=30)
    star = [_sp("star", 65_000_000, 3)]
    rest = [_sp(f"p{i}", 3_000_000, _POSITIONS[i]) for i in range(9)]
    client = _GuardClient(squad=star + rest, budget=-1_000_000, kickoff=kickoff)
    _patch_guard(monkeypatch, client)

    erst = await og.run_outcome_guard(kanal, now=t0)
    assert box.sent == [] and og.SLO_COVERAGE in erst

    client.squad = [_sp("star", 65_000_000, 3, fitness=2)] + rest
    spaeter = await og.run_outcome_guard(kanal, now=t0 + timedelta(hours=10))  # T-20 h
    assert len(box.sent) == 2
    texte = " ".join(m["content"] for m in box.sent)
    assert "Deckung" in texte or "Konto unter dem Anpfiff-Puffer" in texte
    assert "Kader unter dem Gleitpfad" in texte
    assert "Statuswechsel" not in texte
    assert og.SLO_STATUS_CHANGE in spaeter

    await og.run_outcome_guard(kanal, now=t0 + timedelta(hours=11))
    assert len(box.sent) == 2  # dieselben Zustände nicht noch einmal


def test_sofortzustellung_nur_fuer_deckung_und_gleitpfad_binnen_24h():
    base = og.GuardState(now=MORNING, squad=(), budget=0, deadline=MORNING + timedelta(hours=24))
    deckung = og.Finding(og.SLO_COVERAGE, "k", "t")
    assert og.deliver_now(deckung, base) is True
    assert og.deliver_now(og.Finding(og.SLO_SQUAD_PATH, "k", "t"), base) is True
    for slo in (og.SLO_STATUS_CHANGE, og.SLO_BALANCE_STALE, og.SLO_DISPO_SMOOTHING):
        assert og.deliver_now(og.Finding(slo, "k", "t"), base) is False
    weit = og.GuardState(now=MORNING, squad=(), budget=0,
                         deadline=MORNING + timedelta(hours=24, seconds=1))
    vorbei = og.GuardState(now=MORNING, squad=(), budget=0, deadline=MORNING)
    unbekannt = og.GuardState(now=MORNING, squad=(), budget=0, deadline=None)
    for state in (weit, vorbei, unbekannt):
        assert og.deliver_now(deckung, state) is False


# --- Zusammenfassung: Inhalt und Kürzung ------------------------------------------


def test_zusammenfassung_ohne_radarbericht_sagt_das_ehrlich(db):
    with db() as session:
        quellen = ds.load_summary_sources(session, MORNING)
    text = ds.compose_daily_summary("**Kopf**\n\n__✅ Was ich gemacht habe__\n• nichts", quellen)
    assert "kein Radar-Bericht" in text
    assert "keine offenen Befunde" in text
    assert "Kopf" in text and "Was ich gemacht habe" in text


def test_radar_von_gestern_zaehlt_nicht(db):
    with db() as session:
        report_store.save_report(
            "recherche", "Steiger-Radar 23.09.2026", "alt", sr.SOURCE,
            meta={"kandidaten": [{"name": "Gestern Steiger", "team": "X", "position": 3,
                                  "mv_now": 2_000_000, "rise_pct": 12.0, "listing": None}]},
            session=session, created_at=MORNING - timedelta(days=1),
        )
        quellen = ds.load_summary_sources(session, MORNING)
    assert quellen.radar is None


def test_lange_zusammenfassung_wird_sauber_gekuerzt():
    befunde = tuple(f"• Do 07:{i:02d} **Befund {i}** — " + "x" * 150 for i in range(12))
    quellen = ds.SummarySources(findings=befunde, intents=("• keine",), radar=("• keiner",))
    text = ds.compose_daily_summary("**Kopf**\n\n• Rest", quellen)
    assert len(text) <= ds.SUMMARY_LIMIT
    assert text.endswith(ds.MORE_MARKER)
    assert text.startswith("**Kopf**")
    assert "Befund 0" in text and "Befund 11" not in text


def test_kuerzung_schneidet_nie_in_einen_code_block():
    quellen = ds.SummarySources(findings=("• Do 07:09 **Deckung gerissen**",),
                                intents=("• keine",), radar=("• keiner",))
    tabelle = "```\n" + "\n".join(f"ZEILE {i:02d} " + "y" * 30 for i in range(60)) + "\n```"
    body = "**Kopf**\n\n__✅ Was ich gemacht habe__\n• a\n__🔥 Käufe__\n" + tabelle + "\n• z"
    text = ds.compose_daily_summary(body, quellen)
    assert len(text) <= ds.SUMMARY_LIMIT and text.endswith(ds.MORE_MARKER)
    assert text.count("```") % 2 == 0  # nie ein offener Code-Block
    assert "__🔥 Käufe__" not in text  # keine hängende Überschrift ohne Tabelle
    assert "• a" in text and "Deckung gerissen" in text


def test_quellen_konstanten_zeigen_auf_waechter_und_radar():
    assert ds.FINDINGS_SOURCE == og.FINDINGS_SOURCE
    assert ds.RADAR_SOURCE == sr.SOURCE
    assert ds.NEWS_SOURCE == nr.SOURCE


# --- News-Radar (Schritt 4 des Jev-Umbaus, Emil 30.09.2026) --------------------------

_KAUF = {
    "player_id": "16378", "name": "Konstantinos Karetsas", "last_name": "Karetsas",
    "team": "Borussia Dortmund", "position": 3, "mv": 25_145_346, "lineup_prob": 2,
    "listing": {"price": 25_400_000, "expires_at": "2026-09-24T16:40:00+00:00"},
    "title": "Comeback in Stuttgart? Karetsas zurück im Mannschaftstraining",
    "url": "https://www.kicker.de/k", "published_at": "2026-09-23T10:19:00+00:00",
    "probability": 0.996, "source": "kicker",
}
_KADER = {
    "player_id": "43", "name": "Mitchell Weiser", "last_name": "Weiser",
    "team": "SV Werder Bremen", "position": 2,
    "title": "Bitter für Werder: Weiser fällt vorerst aus", "url": "https://www.kicker.de/w",
    "published_at": "2026-09-23T18:00:00+00:00", "probability": 0.99, "source": "kicker",
}


def test_news_radar_von_heute_steht_in_der_zusammenfassung(db):
    with db() as session:
        report_store.save_report(
            "recherche", "News-Radar 24.09.2026", "voll", nr.SOURCE,
            meta={"kauf": [_KAUF], "kader": [_KADER], "hinweis": None, "artikel": 12},
            session=session, created_at=MORNING - timedelta(minutes=4),
        )
        quellen = ds.load_summary_sources(session, MORNING)
    text = ds.compose_daily_summary("**Kopf**\n\n• Rest", quellen)
    assert "News-Radar" in text
    assert nr.buy_line(_KAUF, title_len=nr.SUMMARY_TITLE) in text
    assert nr.squad_line(_KADER, title_len=nr.SUMMARY_TITLE) in text
    assert text.index("• Kader:") < text.index("• Kauf?")
    assert "Kauf nur auf deinen Auftrag" in text


def test_news_radar_hinweis_und_leere_lage_werden_genannt(db):
    with db() as session:
        report_store.save_report(
            "recherche", "News-Radar 24.09.2026", "voll", nr.SOURCE,
            meta={"kauf": [], "kader": [], "hinweis": "Jev nicht erreichbar", "artikel": 9},
            session=session, created_at=MORNING - timedelta(minutes=4),
        )
        quellen = ds.load_summary_sources(session, MORNING)
    text = ds.compose_daily_summary("**Kopf**\n\n• Rest", quellen)
    assert "Jev nicht erreichbar" in text
    assert "keine Rückkehr am Markt" not in text  # mit Hinweis keine behauptete Ruhe


def test_news_radar_von_gestern_zaehlt_nicht(db):
    with db() as session:
        report_store.save_report(
            "recherche", "News-Radar 23.09.2026", "alt", nr.SOURCE,
            meta={"kauf": [_KAUF], "kader": [], "hinweis": None, "artikel": 3},
            session=session, created_at=MORNING - timedelta(days=1),
        )
        quellen = ds.load_summary_sources(session, MORNING)
    assert quellen.news is None
    text = ds.compose_daily_summary("**Kopf**\n\n• Rest", quellen)
    assert "kein News-Radar-Bericht" in text


def test_kurze_zusammenfassung_bleibt_ungekuerzt():
    quellen = ds.SummarySources(findings=(), intents=(), radar=())
    text = ds.compose_daily_summary("**Kopf**\n\n• Zeile", quellen)
    assert ds.MORE_MARKER not in text and text.endswith("• Zeile")


# --- Belege: wer darf überhaupt noch in den Kanal? ----------------------------------


def test_nur_anpfiff_kaskade_und_ergebnis_waechter_rufen_send_blocker():
    """Beleg (Auftrag Punkt 6): Kein anderer Worker-Pfad benutzt den Sofortkanal, und kein
    Aufrufer gibt `send_info` eine Art mit, die der Digest durchlässt."""
    import ast
    from pathlib import Path

    root = Path(ds.__file__).resolve().parents[1]
    blocker_callers: set[str] = set()
    passthrough_kinds: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # Aufrufer holen den Sofortkanal per `getattr(channel, "send_blocker", None)`
            # oder rufen ihn direkt; die Definition in notify/ zählt nicht.
            if (isinstance(node, ast.Attribute) and node.attr == "send_blocker") or (
                isinstance(node, ast.Constant) and node.value == "send_blocker"
            ):
                blocker_callers.add(path.relative_to(root).as_posix())
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "send_info"
            ):
                for kw in node.keywords:
                    if (
                        kw.arg == "kind"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value in report_store.DIGEST_PASSTHROUGH_KINDS
                    ):
                        passthrough_kinds.append(path.name)
    assert blocker_callers == {"worker/kickoff_guard.py", "worker/outcome_guard.py"}
    assert passthrough_kinds == []


def test_digest_laesst_nur_blocker_und_den_morgen_slot_durch():
    assert report_store.DIGEST_PASSTHROUGH_KINDS == frozenset({"blocker"})
    assert report_store.DIGEST_DELIVERY_SLOTS == frozenset({"morning"})


def test_news_radar_ist_kurz_und_nennt_den_kader_zuerst(db):
    kauf = [{**_KAUF, "player_id": str(i), "last_name": f"Markt{i}"} for i in range(5)]
    kader = [{**_KADER, "player_id": f"k{i}", "last_name": f"Kader{i}"} for i in range(3)]
    with db() as session:
        report_store.save_report(
            "recherche", "News-Radar 24.09.2026", "voll", nr.SOURCE,
            meta={"kauf": kauf, "kader": kader, "hinweis": None, "artikel": 30},
            session=session, created_at=MORNING - timedelta(minutes=4),
        )
        quellen = ds.load_summary_sources(session, MORNING)
    news = list(quellen.news)
    assert [line.split()[1] for line in news[:4]] == ["Kader:", "Kader:", "Kauf?", "Kauf?"]
    assert "4 weitere im Cockpit" in news[-1]  # 1 Kader- und 3 Kaufzeilen
    assert all(len(line) <= 230 for line in news)  # lange Namen und „fehlt noch" passen


def test_news_radar_mit_hinweis_behauptet_keine_ruhige_lage(db):
    with db() as session:
        report_store.save_report(
            "recherche", "News-Radar 24.09.2026", "voll", nr.SOURCE,
            meta={"kauf": [], "kader": [], "hinweis": "Jev nicht erreichbar", "artikel": 9},
            session=session, created_at=MORNING - timedelta(minutes=4),
        )
        quellen = ds.load_summary_sources(session, MORNING)
    assert quellen.news == ("• Hinweis: Jev nicht erreichbar",)


@pytest.mark.parametrize("meta", [
    {"kauf": ["kein dict"], "kader": [], "hinweis": None},
    {"kauf": 5, "kader": None, "hinweis": None},
    {"kauf": [{**_KAUF, "probability": float("inf")}], "kader": [], "hinweis": None},
    ["gar kein dict"],
])
def test_kaputte_news_meta_kippt_nie_die_zusammenfassung(db, meta):
    with db() as session:
        report_store.save_report(
            "recherche", "News-Radar 24.09.2026", "voll", nr.SOURCE, meta=meta,
            session=session, created_at=MORNING - timedelta(minutes=4),
        )
        quellen = ds.load_summary_sources(session, MORNING)  # wirft nicht
    # Nie eine behauptete ruhige Lage, wenn etwas nicht lesbar war.
    assert nr.NEWS_EMPTY not in quellen.news
    assert any("nicht lesbar" in line for line in quellen.news)
    text = ds.compose_daily_summary("**Kopf**\n\n• Rest", quellen)
    assert "keine offenen Befunde" in text and "News-Radar" in text  # alles andere bleibt


# ------------------------------------------------------------------ Handels-Radar (01.10.)


_HANDEL = {
    "positionen": [{"player_id": "p1", "name": "Bilal Nadir", "signal": "verkaufen",
                    "mv": 3_500_000, "pnl": -300_000, "buy_price": 3_800_000}],
    "kauf": [{"player_id": "k1", "name": "Oskar Wójcik", "mv": 3_940_000, "r3": 0.214,
              "max_bid": 5_122_000, "jev_return": True,
              "expires_at": "2026-10-01T16:00:00+00:00"}],
    "konto": {"budget": 2_376_645, "after_sale": 5_876_645, "covered": True},
    "frist": "2026-10-09T18:30:00+00:00",
}


def test_handels_radar_von_heute_steht_nach_den_veto_fristen(db):
    from kickbase_agent.worker import trade_radar as tr

    with db() as session:
        report_store.save_report(
            "recherche", "Handels-Radar 24.09.2026", "voll", tr.SOURCE, meta=_HANDEL,
            session=session, created_at=MORNING - timedelta(minutes=3),
        )
        quellen = ds.load_summary_sources(session, MORNING)
    text = ds.compose_daily_summary("**Kopf**\n\n• Rest", quellen)
    assert "__💹 Handels-Radar__" in text
    assert text.index("Veto-Fristen") < text.index("Handels-Radar") < text.index("Steiger-Radar")
    assert "• Verkaufen: Bilal Nadir" in text and "• Kandidat: Oskar Wójcik" in text
    assert text.index("• Verkaufen:") < text.index("• Kandidat: Oskar")
    assert "Der Handels-Autopilot bietet und verkauft selbst" in text
    assert "App" not in text  # Emil 01.10.2026: „ich will nicht dass ich bieten muss“


def test_ohne_handels_radar_von_heute_sagt_die_zusammenfassung_das(db):
    with db() as session:
        quellen = ds.load_summary_sources(session, MORNING)
    assert quellen.trade is None
    text = ds.compose_daily_summary("**Kopf**\n\n• Rest", quellen)
    assert "heute kein Handels-Radar-Bericht" in text


def test_auch_ein_reines_verkaufssignal_traegt_den_autopilot_hinweis():
    quellen = ds.SummarySources((), (), (), (), ("• Verkaufen: Bilal Nadir · MW 3,50 M · -300 k",))
    text = ds.compose_daily_summary("**Kopf**\n\n• Rest", quellen)
    assert "Der Handels-Autopilot bietet und verkauft selbst" in text
