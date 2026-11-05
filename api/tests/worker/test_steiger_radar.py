"""Steiger-Radar (Emils Entscheidung 21.09.2026): rein lesender Tagesbericht.

Die reinen Funktionen (`momentum_candidates`, `format_radar`) werden ohne Fakes
getestet; nur die Verdrahtung (`run_steiger_radar`) bekommt einen Fake-Client nach
dem Muster von tests/worker/test_outcome_guard.py. Der Fake scheitert bei JEDEM
unbekannten Attribut — ein Aufruf einer schreibenden Client-Methode fällt so auf.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import kickbase_agent.worker.steiger_radar as sr
from kickbase_agent.db.models import Base, PlayerHistory, Report
from kickbase_agent.db.models import Player as DbPlayer
from kickbase_agent.kickbase.models import MarketPlayer, Player, SquadPlayer
from kickbase_agent.worker.steiger_radar import (
    CLOSING_LINE,
    MAX_DETAIL_LOOKUPS,
    MAX_LINES,
    Candidate,
    ListingInfo,
    RadarEntry,
    format_radar,
    momentum_candidates,
    run_steiger_radar,
)

NOW = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def _hist(mv_now: int, mv_before: int, *, before_at: datetime | None = None):
    """Zwei Tageswerte: heute und (standardmäßig) genau drei Tage davor."""
    return [(before_at or NOW - 3 * DAY, mv_before), (NOW, mv_now)]


# --- momentum_candidates (rein) ----------------------------------------------


def test_steiger_mit_genau_zehn_prozent_ist_drin_neun_komma_neun_nicht():
    history = {
        "genau": _hist(3_300_000, 3_000_000),  # 3,3/3,0 ist in float < 1,1 — Bruchrechnung
        "knapp": _hist(1_099_000, 1_000_000),
    }
    (cand,) = momentum_candidates(history, NOW)
    assert cand.player_id == "genau"
    assert cand.mv_now == 3_300_000 and cand.mv_before == 3_000_000
    assert cand.rise_pct == pytest.approx(10.0)


def test_gefallener_spieler_ist_kein_kandidat():
    assert momentum_candidates({"fall": _hist(2_000_000, 2_500_000)}, NOW) == []


def test_marktwert_grenzen_ein_bis_zehn_millionen_einschliesslich():
    history = {
        "zu_billig": _hist(999_999, 800_000),
        "untergrenze": _hist(1_000_000, 800_000),
        "obergrenze": _hist(10_000_000, 8_000_000),
        "zu_teuer": _hist(10_000_001, 8_000_000),
    }
    ids = {c.player_id for c in momentum_candidates(history, NOW)}
    assert ids == {"untergrenze", "obergrenze"}


def test_fehlender_vergleichstag_ergibt_keinen_kandidaten():
    # Nur Werte JÜNGER als now − 3 Tage: kein Vergleichswert, kein Kandidat.
    history = {"jung": _hist(2_000_000, 1_000_000, before_at=NOW - 3 * DAY + timedelta(hours=1))}
    assert momentum_candidates(history, NOW) == []
    # Ein einzelner Wert ist ebenfalls kein Vergleich.
    assert momentum_candidates({"einer": [(NOW, 2_000_000)]}, NOW) == []


def test_vergleichswert_ist_der_naechstliegende_tageswert_vor_der_frist():
    history = {
        "p": [
            (NOW - 5 * DAY, 1_000_000),  # älter, nicht der nächstliegende
            (NOW - 4 * DAY, 2_000_000),  # nächstliegend ≤ now − 3 Tage
            (NOW - 2 * DAY, 2_050_000),  # jünger als die Frist, zählt nicht als Vergleich
            (NOW, 2_300_000),
        ]
    }
    (cand,) = momentum_candidates(history, NOW)
    assert cand.mv_before == 2_000_000 and cand.mv_now == 2_300_000
    assert cand.rise_pct == pytest.approx(15.0)


def test_werte_nach_now_zaehlen_nicht_als_heute():
    history = {"p": [(NOW - 3 * DAY, 1_000_000), (NOW, 1_200_000), (NOW + DAY, 5_000_000)]}
    (cand,) = momentum_candidates(history, NOW)
    assert cand.mv_now == 1_200_000


def test_kandidaten_sind_nach_anstieg_absteigend_sortiert():
    history = {
        "klein": _hist(1_100_000, 1_000_000),
        "gross": _hist(1_500_000, 1_000_000),
        "mitte": _hist(1_300_000, 1_000_000),
    }
    assert [c.player_id for c in momentum_candidates(history, NOW)] == ["gross", "mitte", "klein"]


# --- format_radar (rein) ------------------------------------------------------


def _cand(pid: str, mv_now: int = 3_300_000, mv_before: int = 3_000_000) -> Candidate:
    return Candidate(player_id=pid, mv_now=mv_now, mv_before=mv_before,
                     rise_pct=round((mv_now - mv_before) / mv_before * 100, 1))


def _entry(name: str, *, prob: int | None = 2, fitness: int | None = 0,
           listing: ListingInfo | None = None, team: str = "Bayern",
           position: int = 3) -> RadarEntry:
    return RadarEntry(name=name, team=team, position=position, lineup_prob=prob,
                      fitness_status=fitness, listing=listing)


def _radar(candidates, entries, *, own=frozenset(), budget=5_000_000, **kw) -> str:
    return format_radar(candidates, market_by_id=entries, budget=budget, own_ids=own,
                        now=NOW, **kw)


def test_text_hat_kopf_regel_grundlage_kandidat_und_schlusszeile():
    ende = NOW + timedelta(hours=6)
    text = _radar([_cand("a")], {"a": _entry("Anton Auktion",
                                              listing=ListingInfo(3_400_000, ende, ""))})
    assert text.startswith("Steiger-Radar Di 22.09.2026")
    assert "Konto 5,00 M" in text
    assert "Steiger ≥ +10 % / 3 Tage, 1–10 M, fit" in text
    assert "Verkauf am ersten Tag ohne Gewinn" in text and "alles glatt bis Mo 05.10." in text
    assert "Analyse 21.09.2026" in text
    assert "Anton Auktion" in text and "Bayern" in text and "MF" in text
    assert "3,30 M" in text and "+10,0 % / 3 Tage" in text and "KB-Stufe 2" in text
    assert "Auktion 3,40 M" in text and "Auktion: +4–10 % Aufschlag üblich" in text
    assert text.rstrip().endswith(CLOSING_LINE)
    assert "CLAUDE.md §5.12" in CLOSING_LINE and "Der Radar liest nur." in CLOSING_LINE


def test_nicht_am_markt_heisst_nur_angebot_an_besitzer():
    text = _radar([_cand("a")], {"a": _entry("Bernd Besitz")})
    assert "nur Angebot an Besitzer; nimmt erfahrungsgemäß niemand an" in text
    assert "Aufschlag" not in text


def test_manager_listing_zaehlt_wie_nicht_am_markt():
    listing = ListingInfo(3_500_000, NOW - timedelta(hours=3), "U9")
    text = _radar([_cand("a")], {"a": _entry("Manfred Manager", listing=listing)})
    assert "nur Angebot an Besitzer" in text
    assert "Aufschlag" not in text


def test_eigener_kaderspieler_wird_ausgeschlossen():
    text = _radar([_cand("mein"), _cand("fremd")],
                  {"mein": _entry("Mein Spieler"), "fremd": _entry("Fremder Spieler")},
                  own=frozenset({"mein"}))
    assert "Fremder Spieler" in text and "Mein Spieler" not in text


def test_unfitter_spieler_und_unbekannte_fitness_fliegen_raus():
    entries = {
        "fit": _entry("Fitter"),
        "verletzt": _entry("Verletzter", fitness=2),
        "unbekannt": _entry("Unbekannter", fitness=None),
    }
    text = _radar([_cand("fit"), _cand("verletzt"), _cand("unbekannt")], entries)
    assert "Fitter" in text
    assert "Verletzter" not in text and "Unbekannter" not in text


def test_kb_stufe_vier_und_fehlende_stufe_fliegen_raus_stufe_drei_bleibt():
    entries = {
        "drei": _entry("Stufe Drei", prob=3),
        "vier": _entry("Stufe Vier", prob=4),
        "keine": _entry("Ohne Stufe", prob=None),
    }
    text = _radar([_cand("drei"), _cand("vier"), _cand("keine")], entries)
    assert "Stufe Drei" in text and "KB-Stufe 3" in text
    assert "Stufe Vier" not in text and "Ohne Stufe" not in text


def test_hoechstens_fuenf_zeilen_und_unter_1900_zeichen():
    candidates = [_cand(f"p{i}", 9_900_000 - i * 10_000, 8_000_000) for i in range(8)]
    entries = {
        f"p{i}": _entry(
            f"Maximilian-Alexander Langname-Doppelname {i}",
            team="M'gladbach",
            listing=ListingInfo(9_990_000, NOW + timedelta(hours=40 + i), ""),
        )
        for i in range(8)
    }
    text = _radar(candidates, entries)
    lines = [ln for ln in text.splitlines() if ln[:2] in {f"{n}." for n in range(1, 10)}]
    assert len(lines) == MAX_LINES == 5
    assert len(text) <= 1900
    assert "Langname-Doppelname 0" in text and "Langname-Doppelname 5" not in text


def test_kandidat_ohne_eintrag_wird_nicht_erfunden_sondern_als_ungeprueft_gezaehlt():
    text = _radar([_cand("a"), _cand("b")], {"a": _entry("Geprüfter")}, unchecked=1)
    assert "Geprüfter" in text
    assert "1 weitere" in text and "ungeprüft" in text


def test_leerer_fall_ist_ein_ehrlicher_einzeiler():
    text = _radar([], {})
    assert "\n" not in text
    assert "Steiger-Radar Di 22.09.2026" in text
    assert "Kein Kandidat" in text


# --- Verdrahtung: run_steiger_radar ------------------------------------------


class _Channel:
    def __init__(self) -> None:
        self.info: list[tuple[str, dict]] = []

    async def send_info(self, text: str, **kw: object) -> None:
        self.info.append((text, dict(kw)))


def _player(pid: str, mv: int, *, prob: int | None = 2, fitness: int | None = 0,
            tid: str = "2", position: int = 3) -> Player:
    return Player(id=pid, first_name="Vor", last_name=pid.upper(), team_id=tid,
                  position=position, market_value=mv, lineup_prob=prob,
                  fitness_status=fitness)


def _detail(pid: str, mv: int, *, prob: int | None = 2, fitness: int = 0) -> Player:
    """Genau das, was `KickbaseClient.get_player` liefert: ein `Player` OHNE team_name.
    Ein PlayerDetail-Fake hatte am 22.09. einen AttributeError im Container verdeckt."""
    return Player(id=pid, first_name="Vor", last_name=pid.upper(), team_id="2",
                  position=3, market_value=mv, lineup_prob=prob, fitness_status=fitness)


class _ReadOnlyClient:
    """Nur die vier Lesemethoden; jedes andere Attribut ist ein Testfehler."""

    def __init__(self, *, squad=(), market=(), budget=5_000_000, details=None):
        self._squad = list(squad)
        self._market = list(market)
        self._budget = budget
        self._details = dict(details or {})
        self.detail_calls: list[str] = []

    async def get_squad(self, _lg):
        return self._squad

    async def get_market(self, _lg):
        return self._market

    async def get_budget(self, _lg):
        return self._budget

    async def get_player(self, _lg, pid):
        self.detail_calls.append(pid)
        return self._details[pid]

    def __getattr__(self, name: str):
        raise AssertionError(f"Steiger-Radar darf {name!r} nicht aufrufen — er liest nur.")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(sr, "SessionLocal", factory)
    return factory


def _patch_client(monkeypatch, client):
    async def fake_factory():
        return client, SimpleNamespace(league_id="L1", user_id="U1")

    monkeypatch.setattr(sr, "make_kickbase_client", fake_factory)


def _seed(factory, rows: dict[str, list[tuple[datetime, int]]], *, tid: str = "2") -> None:
    with factory() as session:
        for pid, points in rows.items():
            session.add(DbPlayer(id=pid, kickbase_player_id=pid, name=f"Vor {pid.upper()}",
                                 position=3, team=tid, current_market_value=points[-1][1]))
            for at, mv in points:
                session.add(PlayerHistory(player_id=pid, recorded_at=at, market_value=mv))
        session.commit()


def _report_text(db) -> str:
    with db() as session:
        return session.scalars(select(Report)).one().body


async def test_job_speichert_bericht_sendet_nicht_und_liest_nur(db, monkeypatch):
    _seed(db, {
        "auk": _hist(3_300_000, 3_000_000),          # am Markt (Kickbase-Listing)
        "det": _hist(2_400_000, 2_000_000),          # nicht am Markt → get_player
        "mein": _hist(4_400_000, 4_000_000),         # eigener Kaderspieler
        "flach": _hist(2_000_000, 1_950_000),        # kein Steiger
    })
    client = _ReadOnlyClient(
        squad=[SquadPlayer(player=_player("mein", 4_400_000))],
        market=[MarketPlayer(player=_player("auk", 3_300_000), price=3_350_000,
                             expires_at=NOW + timedelta(hours=5))],
        details={"det": _detail("det", 2_400_000)},
        budget=7_410_000,
    )
    _patch_client(monkeypatch, client)
    kanal = _Channel()

    assert await run_steiger_radar(kanal, now=NOW) == "ok:2"

    # Emil 23.09.2026: nur einmal am Tag eine Zusammenfassung — der Radar sendet nicht
    # selbst, die Tageszusammenfassung 08:15 übernimmt seine Zeilen.
    assert kanal.info == []
    text = _report_text(db)
    assert "Vor AUK" in text and "Vor DET" in text
    assert "Vor MEIN" not in text and "Vor FLACH" not in text
    assert "Bayern" in text and "Konto 7,41 M" in text
    assert client.detail_calls == ["det"]  # eigene und Markt-Spieler kosten keinen Abruf

    with db() as session:
        (report,) = session.scalars(select(Report)).all()
    assert report.kind == "recherche"
    assert report.created_at.replace(tzinfo=UTC) == NOW  # Beobachtungszeit des Laufs
    assert report.title == "Steiger-Radar 22.09.2026"
    assert report.body == text
    assert report.source == "worker:steiger_radar"
    assert [c["player_id"] for c in report.meta["kandidaten"]] == ["det", "auk"]
    assert report.meta["kandidaten"][1]["listing"]["price"] == 3_350_000


async def test_job_liest_nur_die_letzten_fuenf_tage_der_historie(db, monkeypatch):
    # Der einzige Vergleichswert ist älter als fünf Tage — er wird nicht geladen,
    # also gibt es keinen Kandidaten (und keinen Detailabruf).
    _seed(db, {"alt": [(NOW - 6 * DAY, 1_000_000), (NOW, 1_500_000)]})
    client = _ReadOnlyClient()
    _patch_client(monkeypatch, client)
    kanal = _Channel()

    assert await run_steiger_radar(kanal, now=NOW) == "ok:0"
    assert client.detail_calls == []
    text = _report_text(db)
    assert "Kein Kandidat" in text
    with db() as session:
        assert session.scalars(select(Report)).one().meta["kandidaten"] == []


async def test_job_ruft_hoechstens_24_details_ab_und_nennt_den_rest_ungeprueft(
    db, monkeypatch
):
    rows = {f"p{i:02d}": _hist(2_000_000 + i * 1_000, 1_000_000) for i in range(30)}
    _seed(db, rows)
    # Alle 30 sind unfit → keiner erscheint, der Job muss trotzdem bei 24 stoppen
    # (22.09.: bei 8 blieben 37 von 39 echten Steigern ungeprüft).
    details = {pid: _detail(pid, 2_000_000, fitness=2) for pid in rows}
    client = _ReadOnlyClient(details=details)
    _patch_client(monkeypatch, client)
    kanal = _Channel()

    assert await run_steiger_radar(kanal, now=NOW) == "ok:0"
    assert len(client.detail_calls) == MAX_DETAIL_LOOKUPS == 24
    text = _report_text(db)
    assert "6 weitere" in text and "ungeprüft" in text


async def test_job_hoert_auf_abzurufen_sobald_fuenf_zeilen_stehen(db, monkeypatch):
    rows = {f"p{i:02d}": _hist(2_000_000 + i * 1_000, 1_000_000) for i in range(7)}
    _seed(db, rows)
    details = {pid: _detail(pid, 2_000_000) for pid in rows}
    client = _ReadOnlyClient(details=details)
    _patch_client(monkeypatch, client)

    assert await run_steiger_radar(_Channel(), now=NOW) == "ok:5"
    assert len(client.detail_calls) == 5


async def test_job_ohne_liga_tut_nichts(db, monkeypatch):
    async def fake_factory():
        return _ReadOnlyClient(), SimpleNamespace(league_id=None, user_id="U1")

    monkeypatch.setattr(sr, "make_kickbase_client", fake_factory)
    kanal = _Channel()
    assert await run_steiger_radar(kanal, now=NOW) == "no_league"
    assert kanal.info == []
