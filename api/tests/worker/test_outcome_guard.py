"""Ergebnis-Wächter (Turnaround-Studie P0-4): Zustände statt Job-Erfolge melden.

Anlass: 94 h bei −8,6 M Konto ohne einen Write und 1049 „ok"-Läufe bei Kader 8.
Die Bewertung (`evaluate`) ist rein und wird ohne Fakes getestet; nur die
Verdrahtung (`run_outcome_guard`) bekommt einen Fake-Client nach dem Muster von
tests/worker/test_pfand_einloeser.py.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import kickbase_agent.worker.outcome_guard as og
from kickbase_agent.db.models import Base, Intent, Report
from kickbase_agent.decision.overdraft import PLANNING_OVERDRAFT_UNTIL
from kickbase_agent.kickbase.models import Fixture, MarketPlayer, Player, SquadPlayer
from kickbase_agent.worker.outcome_guard import (
    NO_COVER_SALE_SENTENCE,
    SLO_BALANCE_STALE,
    SLO_COVERAGE,
    SLO_SQUAD_PATH,
    SLO_STATUS_CHANGE,
    GuardMemory,
    GuardState,
    Listing,
    OwnBid,
    SquadEntry,
    allowed_open_slots,
    evaluate,
    fresh_findings,
    next_memory,
    run_outcome_guard,
)

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


def _squad(size: int, mv: int = 3_000_000) -> tuple[SquadEntry, ...]:
    return tuple(
        SquadEntry(player_id=f"p{i}", name=f"Spieler {i}", market_value=mv, fitness=0, kb_prob=1)
        for i in range(size)
    )


def _state(
    *,
    squad_size: int = 11,
    budget: int = 5_000_000,
    bids: tuple[OwnBid, ...] = (),
    market: tuple[Listing, ...] = (),
    deadline: datetime | None = None,
    now: datetime = NOW,
    squad: tuple[SquadEntry, ...] | None = None,
    planned_bid_ids: frozenset[str] = frozenset(),
) -> GuardState:
    return GuardState(
        now=now,
        squad=squad if squad is not None else _squad(squad_size),
        budget=budget,
        own_bids=bids,
        market=market,
        matchday=5 if deadline is not None else None,
        deadline=deadline,
        planned_bid_ids=planned_bid_ids,
    )


# --- Gleitpfad (a) -----------------------------------------------------------


def test_gleitpfad_grenzen():
    assert allowed_open_slots(timedelta(days=17)) == 3
    assert allowed_open_slots(timedelta(days=7, seconds=1)) == 3
    assert allowed_open_slots(timedelta(days=7)) == 2
    assert allowed_open_slots(timedelta(hours=96, seconds=1)) == 2
    assert allowed_open_slots(timedelta(hours=96)) == 1
    assert allowed_open_slots(timedelta(hours=72, seconds=1)) == 1
    assert allowed_open_slots(timedelta(hours=72)) == 0
    assert allowed_open_slots(timedelta(minutes=5)) == 0


def test_kader_10_bei_t_minus_17_tagen_ist_kein_alarm():
    state = _state(squad_size=10, deadline=NOW + timedelta(days=17))
    assert evaluate(state, GuardMemory()) == []


def test_kader_10_bei_t_minus_60h_ist_ein_gleitpfad_alarm():
    state = _state(squad_size=10, deadline=NOW + timedelta(hours=60))
    (finding,) = evaluate(state, GuardMemory())
    assert finding.slo == SLO_SQUAD_PATH
    assert "10/11" in finding.text
    assert NO_COVER_SALE_SENTENCE in finding.text


def test_kader_8_bei_t_minus_8_tagen_ist_ein_alarm_kader_8_bei_t_minus_5_tagen_auch():
    assert evaluate(_state(squad_size=8, deadline=NOW + timedelta(days=8)), GuardMemory()) == []
    (finding,) = evaluate(_state(squad_size=8, deadline=NOW + timedelta(days=5)), GuardMemory())
    assert finding.slo == SLO_SQUAD_PATH


def _elf(*positions: int) -> tuple[SquadEntry, ...]:
    return tuple(
        SquadEntry(player_id=f"p{i}", name=f"Spieler {i}", market_value=3_000_000,
                   fitness=0, kb_prob=1, position=pos)
        for i, pos in enumerate(positions)
    )


def test_elf_koepfe_mit_zwei_torhuetern_haben_einen_offenen_platz():
    """Review-Befund 21.09.: Elf Köpfe sind keine besetzbare Elf. Mit zwei Torhütern
    bleibt ein Feldplatz leer (100 Punkte), der Wächter meldete trotzdem „ok"."""
    kader = _elf(1, 1, 2, 2, 2, 3, 3, 3, 3, 4, 4)
    assert og.fillable_slots(kader) == 10
    state = _state(squad=kader, deadline=NOW + timedelta(hours=60))
    (finding,) = evaluate(state, GuardMemory())
    assert finding.slo == SLO_SQUAD_PATH and "1 offen" in finding.text


def test_besetzbare_elf_und_unbekannte_positionen():
    assert og.fillable_slots(_elf(1, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4)) == 11
    assert og.fillable_slots(_elf(1, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3)) == 10  # kein Stürmer
    # Ohne Positionsangabe (Altzustand) zählt der Wächter wie bisher die Köpfe.
    assert og.fillable_slots(_squad(10)) == 10 and og.fillable_slots(_squad(14)) == 11


def test_karte_nennt_keine_verletzten_oder_rollenlosen_auktionen():
    ende = NOW + timedelta(hours=20)
    state = _state(
        squad_size=10, budget=9_000_000, deadline=NOW + timedelta(hours=60),
        market=(
            Listing("fit", "Fitter Starter", 2_000_000, ende),
            Listing("verletzt", "Verletzter", 1_000_000, ende, fitness=2, kb_prob=1),
            Listing("bank", "Dauerbank", 1_000_000, ende, fitness=0, kb_prob=5),
        ),
    )
    (finding,) = evaluate(state, GuardMemory())
    assert "Fitter Starter" in finding.text
    assert "Verletzter" not in finding.text and "Dauerbank" not in finding.text


def test_unbekannte_frist_gibt_keinen_gleitpfad_alarm():
    assert evaluate(_state(squad_size=8, deadline=None), GuardMemory()) == []


def test_gleitpfad_karte_nennt_nur_bezahlbare_kb_auktionen_vor_der_frist():
    deadline = NOW + timedelta(hours=60)
    market = (
        Listing("ok1", "Bezahlbar Eins", 2_000_000, NOW + timedelta(hours=5), ""),
        Listing("mgr", "Manager Listing", 1_000_000, NOW + timedelta(hours=5), "U9"),
        Listing("teuer", "Zu Teuer", 4_000_000, NOW + timedelta(hours=5), ""),
        Listing("spaet", "Zu Spaet", 1_000_000, deadline - timedelta(minutes=11), ""),
        Listing("knapp", "Gerade Noch", 1_000_000, deadline - timedelta(minutes=12), ""),
        Listing("geboten", "Schon Geboten", 500_000, NOW + timedelta(hours=3), ""),
        Listing("geplant", "Schon Geplant", 500_000, NOW + timedelta(hours=3), ""),
        Listing("vorbei", "Abgelaufen", 500_000, NOW - timedelta(minutes=1), ""),
    )
    # Konto 5,0 M − Gebot 1,0 M − Puffer 10.000 € = 3,99 M Spielraum.
    state = _state(
        squad_size=10,
        budget=5_000_000,
        bids=(OwnBid("geboten", "Schon Geboten", 1_000_000),),
        market=market,
        deadline=deadline,
        planned_bid_ids=frozenset({"geplant"}),
    )
    (finding,) = evaluate(state, GuardMemory())
    assert "Bezahlbar Eins" in finding.text and "Gerade Noch" in finding.text
    for fremd in ("Manager Listing", "Zu Teuer", "Zu Spaet", "Schon Geplant", "Abgelaufen"):
        assert fremd not in finding.text
    assert "Gebot auf KB-Auktion" in finding.text


def test_gleitpfad_karte_nennt_hoechstens_drei_auktionen():
    market = tuple(
        Listing(f"l{i}", f"Auktion{i}", 1_000_000, NOW + timedelta(hours=1 + i), "")
        for i in range(5)
    )
    state = _state(squad_size=10, market=market, deadline=NOW + timedelta(hours=60))
    (finding,) = evaluate(state, GuardMemory())
    assert [n for n in (f"Auktion{i}" for i in range(5)) if n in finding.text] == [
        "Auktion0", "Auktion1", "Auktion2",
    ]


def test_gleitpfad_karte_ohne_auktion_verweist_auf_emil_in_der_app():
    state = _state(squad_size=10, deadline=NOW + timedelta(hours=60))
    (finding,) = evaluate(state, GuardMemory())
    assert "Emil in der App" in finding.text
    assert "Gebot auf KB-Auktion" not in finding.text


# --- Deckung (c) und Dauer-Minus (d) -----------------------------------------


def test_gebote_ueber_konto_sind_rot_und_beide_gebote_stehen_im_text():
    state = _state(
        budget=11_650_000,
        bids=(OwnBid("a", "Erster Bieter", 8_700_000), OwnBid("b", "Zweiter Bieter", 2_980_000)),
    )
    (finding,) = evaluate(state, GuardMemory())
    assert finding.slo == SLO_COVERAGE
    assert "Erster Bieter" in finding.text and "8,70 M" in finding.text
    assert "Zweiter Bieter" in finding.text and "2,98 M" in finding.text
    assert NO_COVER_SALE_SENTENCE in finding.text


def _second_look(first: GuardState, second: GuardState) -> list[str]:
    """Erst `first` melden und merken, dann `second` bewerten: welche SLOs sind neu?"""
    memory = GuardMemory()
    delivered = fresh_findings(evaluate(first, memory), memory)
    memory = next_memory(first, memory, delivered)
    return [f.slo for f in fresh_findings(evaluate(second, memory), memory)]


def test_neue_groessere_deckungsluecke_am_selben_tag_gibt_eine_neue_karte():
    """Review-Befund 21.09.: Morgens fehlen 100 € (Karte), abends kommt ein App-Gebot
    über 8,0 M dazu. Gleicher Tag, gleiche Kadergröße — der alte Schlüssel verschluckte
    genau den Fall vom 18.09., für den die Prüfung gebaut wurde."""
    morgens = _state(budget=9_900)
    abends = _state(budget=9_900, bids=(OwnBid("a", "App-Gebot", 8_000_000),),
                    now=NOW + timedelta(hours=9))
    assert _second_look(morgens, abends) == [SLO_COVERAGE]


def test_zweites_gebot_am_selben_tag_gibt_eine_neue_karte():
    eins = _state(budget=11_650_000, bids=(OwnBid("a", "A", 11_645_000),))
    zwei = _state(budget=11_650_000, now=NOW + timedelta(hours=6),
                  bids=(OwnBid("a", "A", 11_645_000), OwnBid("b", "B", 2_980_000)))
    assert _second_look(eins, zwei) == [SLO_COVERAGE]


def test_unveraenderte_deckungsluecke_am_selben_tag_bleibt_bei_einer_karte():
    eins = _state(budget=1_000_000, bids=(OwnBid("a", "A", 2_000_000),))
    zwei = _state(budget=1_000_000, bids=(OwnBid("a", "A", 2_000_000),),
                  now=NOW + timedelta(hours=6))
    assert _second_look(eins, zwei) == []


def test_marktwertrauschen_unter_einer_million_gibt_keine_neue_karte():
    eins = _state(budget=1_000_000, bids=(OwnBid("a", "A", 2_400_000),))
    zwei = _state(budget=1_050_000, bids=(OwnBid("a", "A", 2_400_000),),
                  now=NOW + timedelta(hours=6))
    assert _second_look(eins, zwei) == []


def test_deckung_ist_fristunabhaengig():
    fern = _state(budget=1_000_000, bids=(OwnBid("a", "A", 2_000_000),),
                  deadline=NOW + timedelta(days=17))
    ohne = _state(budget=1_000_000, bids=(OwnBid("a", "A", 2_000_000),), deadline=None)
    assert [f.slo for f in evaluate(fern, GuardMemory())] == [SLO_COVERAGE]
    assert [f.slo for f in evaluate(ohne, GuardMemory())] == [SLO_COVERAGE]


def test_konto_minus_ist_rot():
    (finding,) = evaluate(_state(budget=-8_600_000), GuardMemory())
    assert finding.slo == SLO_COVERAGE
    assert "-8,60 M" in finding.text
    assert NO_COVER_SALE_SENTENCE in finding.text


def test_konto_genau_am_puffer_ist_gruen():
    assert evaluate(_state(budget=10_000), GuardMemory()) == []
    assert [f.slo for f in evaluate(_state(budget=9_999), GuardMemory())] == [SLO_COVERAGE]


def test_konto_unter_puffer_laenger_als_24h_gibt_zweite_karte():
    state = _state(budget=-8_600_000)
    frisch = GuardMemory(low_balance_since=NOW - timedelta(hours=23))
    alt = GuardMemory(low_balance_since=NOW - timedelta(hours=25))
    assert [f.slo for f in evaluate(state, frisch)] == [SLO_COVERAGE]
    findings = evaluate(state, alt)
    assert [f.slo for f in findings] == [SLO_COVERAGE, SLO_BALANCE_STALE]
    assert all(NO_COVER_SALE_SENTENCE in f.text for f in findings)


def test_gedaechtnis_merkt_beginn_des_minus_und_loescht_ihn_bei_erholung():
    minus = _state(budget=5_000)
    mem = next_memory(minus, GuardMemory(), [])
    assert mem.low_balance_since == NOW
    spaeter = _state(budget=5_000, now=NOW + timedelta(hours=3))
    assert next_memory(spaeter, mem, []).low_balance_since == NOW
    erholt = _state(budget=50_000, now=NOW + timedelta(hours=4))
    assert next_memory(erholt, mem, []).low_balance_since is None


# --- Statuswechsel (e) -------------------------------------------------------


def _squad_mit_star(fitness: int = 0, kb_prob: int = 1, li: str | None = "starter"):
    star = SquadEntry("star", "Michael Olise", 65_000_000, fitness, kb_prob, li)
    return (star, *_squad(10, mv=3_000_000))  # 65 / 95 M = 68 % Teamwert


def test_statuswechsel_eines_spielers_ueber_30_prozent_wird_gemeldet():
    mem = next_memory(_state(squad=_squad_mit_star()), GuardMemory(), [])
    state = _state(squad=_squad_mit_star(fitness=2))
    (finding,) = evaluate(state, mem)
    assert finding.slo == SLO_STATUS_CHANGE
    assert "Michael Olise" in finding.text and "Fitness" in finding.text
    assert NO_COVER_SALE_SENTENCE in finding.text


def test_statuswechsel_kb_und_ligainsider_zaehlen_ebenfalls():
    mem = next_memory(_state(squad=_squad_mit_star()), GuardMemory(), [])
    (kb,) = evaluate(_state(squad=_squad_mit_star(kb_prob=4)), mem)
    (li,) = evaluate(_state(squad=_squad_mit_star(li="out")), mem)
    assert "KB-Stufe" in kb.text and "LigaInsider" in li.text


def test_erste_beobachtung_und_fehlende_werte_sind_kein_wechsel():
    assert evaluate(_state(squad=_squad_mit_star(fitness=2)), GuardMemory()) == []
    mem = next_memory(_state(squad=_squad_mit_star()), GuardMemory(), [])
    unbekannt = _state(squad=_squad_mit_star(li=None))
    assert evaluate(unbekannt, mem) == []
    # Der zuletzt BEOBACHTETE Wert bleibt die Vergleichsbasis.
    assert next_memory(unbekannt, mem, []).player_status["star"]["li"] == "starter"


def test_statuswechsel_kleiner_spieler_bleibt_still():
    gleich = tuple(
        SquadEntry(f"p{i}", f"S{i}", 3_000_000, 0, 1) for i in range(11)
    )
    mem = next_memory(_state(squad=gleich), GuardMemory(), [])
    krank = (SquadEntry("p0", "S0", 3_000_000, 2, 5), *gleich[1:])
    assert evaluate(_state(squad=krank), mem) == []


# --- Trockenlauf aus der Studie ----------------------------------------------


def test_trockenlauf_kader_10_konto_372k_von_t18_bis_t7_tagen_null_alarme():
    deadline = NOW + timedelta(days=18)
    mem = GuardMemory()
    alarme = []
    for tag in range(0, 12):  # T-18 … T-7 Tage in Tagesschritten
        state = _state(squad_size=10, budget=372_334, deadline=deadline,
                       now=NOW + timedelta(days=tag))
        findings = evaluate(state, mem)
        alarme += findings
        mem = next_memory(state, mem, findings)
    assert deadline - (NOW + timedelta(days=11)) == timedelta(days=7)
    assert alarme == []


# --- Verdrahtung: run_outcome_guard ------------------------------------------


class _DigestChannel:
    """Kanal im Digest-Modus: `send_info` archiviert nur, `send_blocker` stellt zu."""

    def __init__(self) -> None:
        self.info: list[str] = []
        self.blocker: list[str] = []

    async def send_info(self, text: str, **_: object) -> None:
        self.info.append(text)

    async def send_blocker(self, text: str) -> None:
        self.blocker.append(text)


class _LegacyChannel:
    def __init__(self) -> None:
        self.info: list[str] = []

    async def send_info(self, text: str, **_: object) -> None:
        self.info.append(text)


#: Besetzbare Elf (3-5-2): p0 … p10 bekommen der Reihe nach diese Positionen. Seit der
#: Wächter besetzbare Plätze statt Köpfe zählt, wären elf Mittelfeldspieler keine Elf.
_ELEVEN_POSITIONS = (1, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4)


def _sp(pid: str, mv: int, *, fitness: int = 0, prob: int | None = 1) -> SquadPlayer:
    index = int(pid[1:]) if pid[1:].isdigit() else len(_ELEVEN_POSITIONS) - 1
    position = _ELEVEN_POSITIONS[index % len(_ELEVEN_POSITIONS)]
    return SquadPlayer(
        player=Player(id=pid, first_name="T", last_name=pid, team_id="T1", position=position,
                      market_value=mv, lineup_prob=prob, fitness_status=fitness),
        fitness_status=fitness,
    )


class _Client:
    def __init__(self, *, squad, budget=5_000_000, bids=None, market=None, kickoff=None):
        self.squad = squad
        self.budget = budget
        self.bids = bids or []
        self.market = market or []
        self.kickoff = kickoff

    async def get_matchdays(self):
        if self.kickoff is None:
            return []
        return [Fixture(matchday=5, home_team_id="1", away_team_id="2", kickoff=self.kickoff)]

    async def get_budget(self, _lg):
        return self.budget

    async def get_own_bids(self, _lg, _uid):
        return self.bids

    async def get_squad(self, _lg):
        return self.squad

    async def get_market(self, _lg):
        return self.market


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(og, "SessionLocal", factory)
    return factory


@pytest.fixture(autouse=True)
def _kein_netz(monkeypatch):
    """LigaInsider wird in Tests nie wirklich abgerufen."""

    async def _leer(_players):
        return {}

    monkeypatch.setattr(og, "fetch_li_statuses", _leer)


def _patch_client(monkeypatch, client):
    async def fake_factory():
        return client, SimpleNamespace(league_id="L1", user_id="U1")

    monkeypatch.setattr(og, "make_kickbase_client", fake_factory)


def _befunde(db) -> list[Report]:
    """Für die Tageszusammenfassung gespeicherte Befunde (Emil 23.09.2026)."""
    with db() as session:
        return list(session.scalars(
            select(Report).where(Report.source == og.FINDINGS_SOURCE).order_by(Report.id)
        ))


#: Frist binnen 24 h: Deckung und Gleitpfad gehen dann sofort raus (`deliver_now`).
BALD = NOW + timedelta(hours=20)


async def test_kader_10_bei_t_minus_60h_genau_ein_befund_auch_nach_zehn_laeufen(
    db, monkeypatch
):
    client = _Client(squad=[_sp(f"p{i}", 3_000_000) for i in range(10)],
                     kickoff=NOW + timedelta(hours=60))
    _patch_client(monkeypatch, client)
    kanal = _DigestChannel()
    ergebnisse = [
        await run_outcome_guard(kanal, now=NOW + timedelta(minutes=10 * i)) for i in range(10)
    ]
    assert ergebnisse[0] == f"digest:{SLO_SQUAD_PATH}"
    assert all(e == f"open:{SLO_SQUAD_PATH}" for e in ergebnisse[1:])
    # 60 h vor der Frist: nichts in den Kanal, ein Befund für die Tageszusammenfassung.
    assert kanal.blocker == [] and kanal.info == []
    (befund,) = _befunde(db)
    assert befund.kind == "blocker" and befund.meta["delivered"] is False
    assert NO_COVER_SALE_SENTENCE in befund.body


async def test_kanal_ohne_send_blocker_bekommt_die_karte_ueber_send_info(db, monkeypatch):
    _patch_client(monkeypatch, _Client(squad=[_sp(f"p{i}", 3_000_000) for i in range(11)],
                                       budget=-8_600_000, kickoff=BALD))
    kanal = _LegacyChannel()
    assert await run_outcome_guard(kanal, now=NOW) == f"alert:{SLO_COVERAGE}"
    assert len(kanal.info) == 1 and "-8,60 M" in kanal.info[0]


def _mp(pid: str, price: int, ends: datetime, seller: str = "") -> MarketPlayer:
    return MarketPlayer(
        player=Player(id=pid, first_name="Markt", last_name=pid, team_id="T2", position=3,
                      market_value=price),
        price=price, expires_at=ends, seller_id=seller,
    )


async def test_karte_nennt_kb_auktion_aus_dem_markt_ohne_geplantes_gebot(db, monkeypatch):
    market = [
        _mp("frei", 2_000_000, NOW + timedelta(hours=5)),
        _mp("geplant", 1_000_000, NOW + timedelta(hours=5)),
        _mp("manager", 1_000_000, NOW - timedelta(hours=5), seller="U9"),
    ]
    with db() as session:
        session.add(Intent(created_at=NOW, updated_at=NOW, kind="bid", player_id="geplant",
                           amount=1_000_000, execute_at=NOW + timedelta(hours=4),
                           status="planned"))
        session.commit()
    _patch_client(monkeypatch, _Client(squad=[_sp(f"p{i}", 3_000_000) for i in range(10)],
                                       market=market, kickoff=BALD))
    kanal = _DigestChannel()
    assert await run_outcome_guard(kanal, now=NOW) == f"alert:{SLO_SQUAD_PATH}"
    (karte,) = kanal.blocker
    assert "Gebot auf KB-Auktion" in karte and "Markt frei" in karte
    assert "Markt geplant" not in karte and "Markt manager" not in karte


async def test_app_gebote_aus_live_own_bids_zaehlen_und_stehen_im_text(db, monkeypatch):
    bids = [
        {"player_id": "a", "name": "Erster Bieter", "amount": 8_700_000},
        {"player_id": "b", "name": "Zweiter Bieter", "amount": 2_980_000},
    ]
    _patch_client(monkeypatch, _Client(squad=[_sp(f"p{i}", 3_000_000) for i in range(11)],
                                       budget=11_650_000, bids=bids, kickoff=BALD))
    kanal = _DigestChannel()
    assert await run_outcome_guard(kanal, now=NOW) == f"alert:{SLO_COVERAGE}"
    (karte,) = kanal.blocker
    assert "Erster Bieter" in karte and "Zweiter Bieter" in karte


async def test_alles_gruen_ist_ok_und_schreibt_nicht_bei_jedem_lauf_einen_bericht(
    db, monkeypatch
):
    _patch_client(monkeypatch, _Client(squad=[_sp(f"p{i}", 3_000_000) for i in range(11)],
                                       kickoff=NOW + timedelta(hours=60)))
    kanal = _DigestChannel()
    for i in range(5):
        assert await run_outcome_guard(kanal, now=NOW + timedelta(minutes=10 * i)) == "ok"
    assert kanal.blocker == [] and kanal.info == []
    with db() as session:
        rows = session.scalars(select(Report).where(Report.source == og.SOURCE)).all()
    assert len(rows) <= 1  # höchstens die erste Status-Basis, nie eine Zeile je Lauf


async def test_statuswechsel_grosser_spieler_kommt_genau_einmal_in_die_zusammenfassung(
    db, monkeypatch
):
    rest = [_sp(f"p{i}", 3_000_000) for i in range(10)]
    client = _Client(squad=[_sp("star", 65_000_000, fitness=0), *rest], kickoff=BALD)
    _patch_client(monkeypatch, client)
    kanal = _DigestChannel()
    assert await run_outcome_guard(kanal, now=NOW) == "ok"
    client.squad = [_sp("star", 65_000_000, fitness=2), *rest]
    ergebnisse = [
        await run_outcome_guard(kanal, now=NOW + timedelta(minutes=10 * i)) for i in range(1, 6)
    ]
    assert ergebnisse[0] == f"digest:{SLO_STATUS_CHANGE}"
    assert ergebnisse[1:] == ["ok"] * 4
    # Auch 20 h vor der Frist nie sofort — nur Deckung und Gleitpfad sind dringend.
    assert kanal.blocker == []
    (befund,) = _befunde(db)
    assert befund.meta["slo"] == SLO_STATUS_CHANGE and "star" in befund.body


async def test_ligainsider_status_fliesst_nur_fuer_grosse_spieler_ein(db, monkeypatch):
    rest = [_sp(f"p{i}", 3_000_000) for i in range(10)]
    _patch_client(monkeypatch, _Client(squad=[_sp("star", 65_000_000), *rest]))
    gefragt: list[list[str]] = []
    antwort = {"star": "starter"}

    async def _li(players):
        gefragt.append([p.player_id for p in players])
        return dict(antwort)

    monkeypatch.setattr(og, "fetch_li_statuses", _li)
    kanal = _DigestChannel()
    assert await run_outcome_guard(kanal, now=NOW) == "ok"
    antwort["star"] = "out"
    assert await run_outcome_guard(kanal, now=NOW + timedelta(minutes=10)) == (
        f"digest:{SLO_STATUS_CHANGE}"
    )
    assert gefragt == [["star"], ["star"]]
    assert "LigaInsider" in _befunde(db)[0].body


async def test_neuer_tag_erinnert_erneut_gleicher_tag_nicht(db, monkeypatch):
    _patch_client(monkeypatch, _Client(squad=[_sp(f"p{i}", 3_000_000) for i in range(11)],
                                       budget=-8_600_000))
    kanal = _DigestChannel()
    await run_outcome_guard(kanal, now=NOW)
    await run_outcome_guard(kanal, now=NOW + timedelta(hours=5))
    assert len(_befunde(db)) == 1
    await run_outcome_guard(kanal, now=NOW + timedelta(hours=24))
    assert len(_befunde(db)) == 2
    assert kanal.blocker == []  # ohne bekannte Frist nie sofort


async def test_neuer_tag_erinnert_auch_bei_sofortzustellung_erneut(db, monkeypatch):
    client = _Client(squad=[_sp(f"p{i}", 3_000_000) for i in range(11)],
                     budget=-8_600_000, kickoff=BALD)
    _patch_client(monkeypatch, client)
    kanal = _DigestChannel()
    await run_outcome_guard(kanal, now=NOW)
    await run_outcome_guard(kanal, now=NOW + timedelta(hours=5))
    assert len(kanal.blocker) == 1
    client.kickoff = NOW + timedelta(hours=40)
    await run_outcome_guard(kanal, now=NOW + timedelta(hours=24))
    assert len(kanal.blocker) == 2


async def test_ohne_liga_kein_lauf(db, monkeypatch):
    async def fake_factory():
        return object(), SimpleNamespace(league_id=None, user_id="U1")

    monkeypatch.setattr(og, "make_kickbase_client", fake_factory)
    assert await run_outcome_guard(_DigestChannel(), now=NOW) == "no_league"


# --- Tausch-Finanzierung auf Planungs-Dispo in der Deckung (c) (Emil 22./23.09.2026) -
#
# Entscheidung: Ohne offenen Dispo-Tausch gilt bis und nach dem 05.10. unverändert die
# Puffer-Regel — ein Minus ohne gekoppelten Verkauf ist nicht gewollt, sondern ein
# Alarm. Nur ein LIVE-Gebot, dessen wartender Sofortverkauf noch im Kader steht, zählt
# bis zur Frist mit Marktwert × 0,90 (höchstens der Gebotsbetrag) als Deckung; dann muss
# zusätzlich die Kickbase-33-%-Grenze halten. Ab der Frist (PLANNING_OVERDRAFT_UNTIL, seit
# 05.10.2026 das Ende des 31.10.) wieder allein der Puffer. Die Frist kommt aus der
# Konstante — eingetragen stand hier 05.10.; mit der Verlängerung wurde der Test rot.

FRIST = PLANNING_OVERDRAFT_UNTIL.astimezone(UTC)


def _tausch(**kwargs) -> GuardState:
    base = dict(
        budget=1_000_000,
        bids=(OwnBid("t", "Neuer Stürmer", 3_500_000),),
        swap_sales=(og.SwapSale(player_id="p0", for_player_id="t", after_squad_size=12),),
    )
    base.update(kwargs)
    swap_sales = base.pop("swap_sales")
    state = _state(**base)
    return GuardState(**{**state.__dict__, "swap_sales": swap_sales})


def test_ohne_tauschbeleg_gilt_vor_dem_5_10_die_pufferregel():
    """Der Studienfall bleibt ein Alarm — auch vor der Dispo-Frist."""
    state = _state(
        budget=11_650_000,
        bids=(OwnBid("a", "Erster Bieter", 8_700_000), OwnBid("b", "Zweiter Bieter", 2_980_000)),
    )
    (finding,) = evaluate(state, GuardMemory())
    assert finding.slo == SLO_COVERAGE and "Deckung gerissen" in finding.text
    assert "Tausch" not in finding.text


def test_gekoppelter_tauschverkauf_deckt_das_dispo_gebot():
    # Konto 1 M − Gebot 3,5 M = −2,5 M; p0 (3 M × 0,90 = 2,7 M) wartet → netto +0,2 M.
    assert evaluate(_tausch(), GuardMemory()) == []


def test_tausch_netto_nicht_gedeckt_gibt_die_karte():
    (finding,) = evaluate(_tausch(bids=(OwnBid("t", "Neuer Stürmer", 3_950_000),)),
                          GuardMemory())
    assert finding.slo == SLO_COVERAGE
    assert "Tausch-Deckung gerissen" in finding.text and "2,85 M" in finding.text
    assert NO_COVER_SALE_SENTENCE in finding.text


def test_tausch_genau_am_puffer_ist_gruen():
    # −2,84 M + 2,85 M = 10.000 € (Puffer) → grün; ein Euro mehr Gebot → rot.
    assert evaluate(_tausch(bids=(OwnBid("t", "T", 3_840_000),)), GuardMemory()) == []
    (finding,) = evaluate(_tausch(bids=(OwnBid("t", "T", 3_840_001),)), GuardMemory())
    assert finding.slo == SLO_COVERAGE


def test_tausch_ueber_der_33_prozent_grenze_gibt_die_karte():
    """Teamwert 30 M (Grenze −10 M): Verkauf 20 M deckt netto, Konto −14 M reißt."""
    squad = (SquadEntry("p0", "Verkauf", 20_000_000, 0, 1),) + tuple(
        SquadEntry(f"p{i}", f"S{i}", 1_000_000, 0, 1) for i in range(1, 11)
    )
    (finding,) = evaluate(
        _tausch(squad=squad, bids=(OwnBid("t", "T", 15_000_000),)), GuardMemory()
    )
    assert finding.slo == SLO_COVERAGE and "33-%" in finding.text


def test_ab_der_dispo_frist_zaehlt_der_tauschverkauf_nicht_mehr():
    assert evaluate(_tausch(now=FRIST - timedelta(seconds=1)), GuardMemory()) == []
    (finding,) = evaluate(_tausch(now=FRIST), GuardMemory())
    assert finding.slo == SLO_COVERAGE and "Deckung gerissen" in finding.text


def test_tauschverkauf_deckt_hoechstens_sein_eigenes_gebot():
    """2,7 M Verkauf für ein 1-M-Gebot: angerechnet 1 M, nicht das zweite Gebot."""
    bids = (OwnBid("t", "Tauschziel", 1_000_000), OwnBid("x", "Anderes", 2_500_000))
    (finding,) = evaluate(_tausch(bids=bids), GuardMemory())
    assert finding.slo == SLO_COVERAGE and "1,00 M angerechnet" in finding.text


def test_tauschverkauf_ohne_spieler_im_kader_oder_ohne_live_gebot_deckt_nichts():
    ohne_spieler = _tausch(swap_sales=(og.SwapSale(
        player_id="weg", for_player_id="t", after_squad_size=12),))
    ohne_gebot = _tausch(swap_sales=(og.SwapSale(
        player_id="p0", for_player_id="x", after_squad_size=12),))
    for state in (ohne_spieler, ohne_gebot):
        (finding,) = evaluate(state, GuardMemory())
        assert finding.slo == SLO_COVERAGE


async def test_verdrahtung_liest_wartende_dispo_verkaeufe_aus_den_vorhaben(db, monkeypatch):
    bids = [{"player_id": "t", "name": "Neuer Stürmer", "amount": 3_500_000}]
    _patch_client(monkeypatch, _Client(squad=[_sp(f"p{i}", 3_000_000) for i in range(11)],
                                       budget=1_000_000, bids=bids))
    kanal = _DigestChannel()
    assert await run_outcome_guard(kanal, now=NOW) == f"digest:{SLO_COVERAGE}"  # ohne Beleg
    with db() as session:
        # Ein gewöhnlicher Tausch-Verkauf (echtes Geld) ist kein Dispo-Beleg.
        plain = Intent(
            created_at=NOW, updated_at=NOW, kind="sell", player_id="p0", amount=3_000_000,
            execute_at=NOW, status="planned",
            sources={"rule_id": "swap_sell", "swap_for": "t", "after_squad_size": 12},
        )
        session.add(plain)
        session.commit()
        plain_id = plain.id
    assert await run_outcome_guard(_DigestChannel(), now=NOW + timedelta(minutes=5)) != "ok"
    with db() as session:
        session.delete(session.get(Intent, plain_id))
        session.add(Intent(
            created_at=NOW, updated_at=NOW, kind="sell", player_id="p0", amount=3_000_000,
            execute_at=NOW, status="planned",
            sources={"rule_id": "swap_sell", "swap_for": "t", "swap_dispo": True,
                     "after_squad_size": 12},
        ))
        session.commit()
    assert await run_outcome_guard(_DigestChannel(), now=NOW + timedelta(minutes=10)) == "ok"


# --- Review 23.09.2026: Befund 6 (Lärm nach dem Zuschlag) und 2b (Glättung) --------


def _gewonnen(**kwargs) -> GuardState:
    """Zuschlag da: Ziel t im Kader, Konto −10 M, der gekoppelte Verkauf p0 wartet."""
    squad = (SquadEntry("p0", "Verkauf", 13_000_000, 0, 1),) + tuple(
        SquadEntry(f"p{i}", f"S{i}", 13_000_000, 0, 1) for i in range(1, 11)
    ) + (SquadEntry("t", "Neuer Stürmer", 12_000_000, 0, 1),)
    return _tausch(**{"squad": squad, "budget": -10_000_000, "bids": (), **kwargs})


def test_nach_dem_zuschlag_deckt_der_wartende_tauschverkauf_das_minus():
    """Das Minus bis zum Verkauf ist gewollt: keine Deckungskarte, solange der gekoppelte
    Verkauf wartet (11,7 M Erlös gegen −10 M) — auch nach der Dispo-Frist."""
    assert evaluate(_gewonnen(), GuardMemory()) == []
    assert evaluate(_gewonnen(now=FRIST + timedelta(days=1)), GuardMemory()) == []


def test_nach_dem_zuschlag_ohne_wartenden_verkauf_bleibt_die_karte():
    (finding,) = evaluate(_gewonnen(swap_sales=()), GuardMemory())
    assert finding.slo == SLO_COVERAGE


def test_wartender_verkauf_deckt_nur_bei_erfuellbarer_kadergroesse():
    """Zweites Review 23.09.2026, Auflage 3: Der Verkauf geht erst ab Kader ≥ 12. Fehlt
    ein anderer Spieler (Kader 11 statt 12), wartet er bis zur Frist und bringt nichts —
    sein Erlös ist keine Deckung, die Karte muss kommen (Konto −10 M)."""
    elf = (SquadEntry("p0", "Verkauf", 13_000_000, 0, 1),) + tuple(
        SquadEntry(f"p{i}", f"S{i}", 13_000_000, 0, 1) for i in range(1, 10)
    ) + (SquadEntry("t", "Neuer Stürmer", 12_000_000, 0, 1),)
    (finding,) = evaluate(_gewonnen(squad=elf), GuardMemory())
    assert finding.slo == SLO_COVERAGE

    # Vor dem Zuschlag dasselbe: Kader 10 + Ziel = 11 < 12 → kein angerechneter Erlös.
    zehn = (SquadEntry("p0", "Verkauf", 3_000_000, 0, 1),) + tuple(
        SquadEntry(f"p{i}", f"S{i}", 1_000_000, 0, 1) for i in range(1, 10)
    )
    (finding,) = evaluate(_tausch(squad=zehn), GuardMemory())
    assert finding.slo == SLO_COVERAGE


def test_wartender_verkauf_mit_erfuellter_kadergroesse_deckt_weiter():
    assert evaluate(_gewonnen(), GuardMemory()) == []  # Kader 12 = after_squad_size
    assert evaluate(_tausch(), GuardMemory()) == []  # Kader 11 + Ziel = 12


def _debt(**kwargs):
    base = dict(player_id="p0", for_player_id="t", status="failed",
                result="Tages-Cap Verkäufe erreicht (5/5) — nicht ausgeführt",
                updated_at=NOW - timedelta(minutes=20))
    base.update(kwargs)
    return og.DispoDebt(**base)


def test_gescheiterte_dispo_glaettung_meldet_einen_blocker():
    state = _gewonnen(swap_sales=())
    state = GuardState(**{**state.__dict__, "dispo_debts": (_debt(),)})

    found = [f for f in evaluate(state, GuardMemory()) if f.slo == og.SLO_DISPO_SMOOTHING]

    (finding,) = found
    assert "Dispo-Glättung gescheitert" in finding.text
    assert "Verkauf" in finding.text and "13,00 M" in finding.text and "Tages-Cap" in finding.text


def test_dispo_glaettung_meldet_erst_nach_der_karenz_und_nur_bei_kontominus():
    frisch = _gewonnen(swap_sales=())
    frisch = GuardState(**{**frisch.__dict__,
                           "dispo_debts": (_debt(updated_at=NOW - timedelta(minutes=5)),)})
    plus = _gewonnen(swap_sales=(), budget=50_000)
    plus = GuardState(**{**plus.__dict__, "dispo_debts": (_debt(),)})
    weg = _gewonnen(swap_sales=())
    weg = GuardState(**{**weg.__dict__, "dispo_debts": (_debt(player_id="verkauft"),)})
    for state in (frisch, plus, weg):
        assert not [f for f in evaluate(state, GuardMemory())
                    if f.slo == og.SLO_DISPO_SMOOTHING]


def _dispo_bid(*, won: bool) -> Intent:
    """Ausgeführtes Dispo-Gebot auf t (Tauschpartner p0), Listingende vor 2 h."""
    sources = {"swap_out": "p0", "league_id": "L1"}
    if won:
        sources["swap_won_at"] = (NOW - timedelta(hours=2)).isoformat()
    return Intent(
        created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=3), kind="bid",
        player_id="t", amount=12_000_000, execute_at=NOW, status="executed",
        deadline=NOW - timedelta(hours=2), sources=sources,
    )


async def test_verdrahtung_meldet_keine_glaettung_ohne_zuschlag_aus_dem_paar(db, monkeypatch):
    """Zweites Review, Auflage 1: verlorene Auktion + späterer eigener Kauf ist keine
    gescheiterte Glättung — dafür gilt die gewöhnliche Deckungskarte."""
    squad = [_sp(f"p{i}", 13_000_000) for i in range(11)] + [_sp("t", 12_000_000)]
    _patch_client(monkeypatch, _Client(squad=squad, budget=-10_000_000))
    with db() as session:
        session.add(_dispo_bid(won=False))
        session.add(Intent(
            created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(minutes=20),
            kind="sell", player_id="p0", amount=13_000_000, execute_at=NOW,
            status="superseded", result="nicht mehr im Plan",
            sources={"rule_id": "swap_sell", "swap_for": "t", "swap_dispo": True,
                     "after_squad_size": 12, "league_id": "L1"},
        ))
        session.commit()

    result = await run_outcome_guard(_DigestChannel(), now=NOW)

    assert og.SLO_DISPO_SMOOTHING not in result and SLO_COVERAGE in result


async def test_verdrahtung_meldet_gescheiterte_glaettung_aus_den_vorhaben(db, monkeypatch):
    squad = [_sp(f"p{i}", 13_000_000) for i in range(11)] + [_sp("t", 12_000_000)]
    _patch_client(monkeypatch, _Client(squad=squad, budget=-10_000_000))
    with db() as session:
        session.add(_dispo_bid(won=True))
        session.add(Intent(
            created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(minutes=20),
            kind="sell", player_id="p0", amount=13_000_000, execute_at=NOW, status="failed",
            result="refused: Preflight: aktueller Marktwert weicht mehr als 20 % ab",
            sources={"rule_id": "swap_sell", "swap_for": "t", "swap_dispo": True,
                     "after_squad_size": 12, "league_id": "L1"},
        ))
        session.commit()
    kanal = _DigestChannel()

    result = await run_outcome_guard(kanal, now=NOW)

    assert og.SLO_DISPO_SMOOTHING in result
    # Emil 23.09.2026: nur in die Tageszusammenfassung, nie sofort.
    assert kanal.blocker == []
    assert any("Dispo-Glättung gescheitert" in r.body for r in _befunde(db))


# --- Handels-Autopilot (Emil 01.10.2026) -----------------------------------------


def _trade_state(*, budget, trade=(), bids=(), market=(), trade_bids=(), deadline=None,
                 regular=11):
    squad = _squad(regular) + tuple(
        SquadEntry(player_id=pid, name=f"Handel {pid}", market_value=mv, fitness=0, kb_prob=1)
        for pid, mv in trade
    )
    return GuardState(
        now=NOW, squad=squad, budget=budget, own_bids=tuple(bids), market=tuple(market),
        matchday=5 if deadline is not None else None, deadline=deadline,
        trade_ids=frozenset(pid for pid, _ in trade), trade_bid_ids=frozenset(trade_bids),
        sale_by=deadline - timedelta(hours=96) if deadline is not None else None,
    )


def test_handelsspieler_fuellen_keinen_elfplatz():
    """„Sie bleiben Bank“: 10 Stammspieler plus 2 Handelsspieler sind 10 besetzbare Plätze."""
    state = _trade_state(budget=5_000_000, regular=10, trade=[("t1", 1), ("t2", 1)],
                         deadline=NOW + timedelta(hours=60))
    (finding,) = evaluate(state, GuardMemory())
    assert finding.slo == SLO_SQUAD_PATH
    assert "10/11" in finding.text and "plus 2 Handelsspieler auf der Bank" in finding.text


def test_handel_ins_minus_ist_kein_alarm_solange_der_verkauf_deckt():
    """Konto −2 M nach einem Handelskauf, der Handelsspieler (MW 3 M) geht spätestens am
    Verkaufstermin: 0,9 × 3 M deckt das Minus — kein Befund, auch kein Dauer-Minus."""
    state = _trade_state(budget=-2_000_000, trade=[("t", 3_000_000)],
                         deadline=NOW + timedelta(days=5))
    since = GuardMemory(low_balance_since=NOW - timedelta(hours=30))
    assert evaluate(state, since) == []
    assert next_memory(state, since, []).low_balance_since is None


def test_laufende_handelsgebote_decken_sich_ueber_ihr_ziel():
    state = _trade_state(
        budget=1_000_000, bids=[OwnBid("x", "Steiger", 4_000_000)],
        market=[Listing("x", "Steiger", 3_500_000, NOW + timedelta(hours=5),
                        market_value=3_500_000)],
        trade_bids={"x"},
    )
    assert [f.slo for f in evaluate(state, GuardMemory())] == []


def test_reicht_auch_der_verkauf_nicht_bleibt_es_ein_befund():
    state = _trade_state(budget=-5_000_000, trade=[("t", 3_000_000)],
                         deadline=NOW + timedelta(days=5))
    findings = {f.slo: f for f in evaluate(state, GuardMemory())}
    assert "Deckung gerissen trotz Handel" in findings[SLO_COVERAGE].text
    assert "-2,00 M" in findings[SLO_COVERAGE].text  # netto: −5 M + 1,0 × 3 M
    assert NO_COVER_SALE_SENTENCE in findings[SLO_COVERAGE].text


def test_ohne_handel_bleibt_die_deckungsregel_unveraendert():
    state = _trade_state(budget=-2_000_000)
    assert [f.slo for f in evaluate(state, GuardMemory())] == [SLO_COVERAGE]


def test_handelsgebote_zaehlen_mit_dem_marktwert_nicht_dem_listingpreis():
    """Codex-Review 02.10.2026 (P1): Der Listingpreis einer Kickbase-Auktion kann vom Marktwert
    abweichen (Update seit dem Einstellen) — gedeckt wird nur, was der Verkauf bringt."""
    state = _trade_state(
        budget=1_000_000, bids=[OwnBid("x", "Steiger", 12_000_000)],
        market=[Listing("x", "Steiger", 13_000_000, NOW + timedelta(hours=5),
                        market_value=10_000_000)],
        trade_bids={"x"},
    )
    (finding,) = evaluate(state, GuardMemory())
    assert finding.slo == SLO_COVERAGE  # 1 − 12 + 1,0 × 10 = −1 M
    assert "-1,00 M" in finding.text


def test_nach_dem_verkaufstermin_deckt_ein_ungeverkaufter_handelsspieler_nichts_mehr():
    """Review 02.10.2026: Scheitert der Pflichtverkauf, darf der Handelsspieler den Deckungs-
    befund nicht länger verschlucken — drei Stunden nach dem Termin zählt er nicht mehr."""
    deadline = NOW + timedelta(hours=40)  # Verkaufstermin T−96 h liegt 56 h zurück
    state = _trade_state(budget=-2_000_000, trade=[("t", 3_000_000)], deadline=deadline)
    assert [f.slo for f in evaluate(state, GuardMemory())] == [SLO_COVERAGE]


def _tausch_mit_handel(trade_mv: int, **kwargs) -> GuardState:
    """Offener Dispo-Tausch plus ein Handelsspieler „h“ auf der Bank."""
    base = _tausch(**kwargs)
    squad = base.squad + (SquadEntry("h", "Handel h", trade_mv, 0, 1),)
    return GuardState(**{**base.__dict__, "squad": squad, "trade_ids": frozenset({"h"})})


def test_tausch_und_handel_zugleich_decken_sich_beide_ueber_ihren_verkauf():
    """Konto 1 M − Tauschgebot 3,8 M + Tauschverkauf 2,7 M = −0,1 M; der Handelsspieler
    (MW 2 M) bringt 1,8 M — netto 1,7 M, kein Befund (ohne ihn: Karte, siehe oben)."""
    state = _tausch_mit_handel(2_000_000, bids=(OwnBid("t", "Neuer Stürmer", 3_800_000),))
    assert evaluate(state, GuardMemory()) == []


def test_die_33_prozent_grenze_zaehlt_das_echte_konto_nicht_den_spaeteren_handelsverkauf():
    """Teamwert 35 M (Grenze −11,67 M): Schlagen alle zu, steht das Konto bei −14 M — der
    Handelsverkauf kommt erst später und hebt Kickbases Grenze nicht auf."""
    squad = (SquadEntry("p0", "Verkauf", 20_000_000, 0, 1),) + tuple(
        SquadEntry(f"p{i}", f"S{i}", 1_000_000, 0, 1) for i in range(1, 11)
    )
    state = _tausch_mit_handel(5_000_000, squad=squad, bids=(OwnBid("t", "T", 15_000_000),))
    (finding,) = evaluate(state, GuardMemory())
    assert finding.slo == SLO_COVERAGE and "33-%" in finding.text
