"""Pfand-Einlöser (01.09.): Deckungsverkäufe vor der Deadline — Reconcile-Nachlauf.

Eigene Datei (L-087); Fakes nach dem Muster von tests/worker/test_intents.py.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.worker.reconcile as rc
from kickbase_agent.db.models import Base, Intent, TransferAction
from kickbase_agent.kickbase.models import Fixture, MarketPlayer, Player, SquadPlayer
from kickbase_agent.worker.intents import insert_cover_sells, upsert_intents
from kickbase_agent.worker.reconcile import cover_sell_drafts, run_pfand_einloeser

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


def _sp(pid: str, mv: int, *, lo: int | None = None, prob: int | None = None,
        position: int = 3) -> SquadPlayer:
    return SquadPlayer(
        player=Player(
            id=pid, first_name="T", last_name=pid, team_id="T1",
            position=position, market_value=mv, lineup_prob=prob,
        ),
        lineup_order=lo,
    )


def _squad_12() -> list[SquadPlayer]:
    """Gültige Elf (lo 0–10) + ein Bank-Spieler."""
    elf = [_sp("gk", 3_000_000, lo=0, position=1)]
    elf += [_sp(f"d{i}", 3_000_000, lo=1 + i, position=2) for i in range(4)]
    elf += [_sp(f"m{i}", 3_000_000, lo=5 + i, position=3) for i in range(4)]
    elf += [_sp(f"s{i}", 3_000_000, lo=9 + i, position=4) for i in range(2)]
    bank = [_sp("bank1", 2_000_000, prob=4, position=3)]
    return elf + bank


# --- cover_sell_drafts (rein) ------------------------------------------------


def test_cover_nimmt_nur_bank_und_deckt_den_fehlbetrag():
    drafts, rest = cover_sell_drafts(_squad_12(), frozenset(), 1_500_000, NOW)
    assert [d.player_id for d in drafts] == ["bank1"]
    assert drafts[0].kind == "sell" and drafts[0].sources["cover"] is True
    assert rest == 0


def test_cover_fasst_die_elf_nie_an():
    squad = _squad_12()
    drafts, rest = cover_sell_drafts(squad, frozenset(), 50_000_000, NOW)
    assert {d.player_id for d in drafts} <= {"bank1"}
    assert rest == 50_000_000 - 2_000_000  # Bank-Pool erschöpft, Rest ehrlich


def test_cover_respektiert_verfassung():
    drafts, rest = cover_sell_drafts(_squad_12(), frozenset({"bank1"}), 1_000_000, NOW)
    assert drafts == [] and rest == 1_000_000


def test_cover_positions_gate_letzte_besetzung_ist_keine_kasse():
    # Bank-Stürmer, aber einziger Stürmer im Kader (9c-Fund „Vogt-Fiktion"):
    squad = [_sp("gk", 3_000_000, lo=0, position=1)]
    squad += [_sp(f"d{i}", 3_000_000, lo=1 + i, position=2) for i in range(5)]
    squad += [_sp(f"m{i}", 3_000_000, lo=6 + i, position=3) for i in range(5)]
    squad += [_sp("st", 2_000_000, position=4)]  # Bank, einziger STU
    drafts, rest = cover_sell_drafts(squad, frozenset(), 1_000_000, NOW)
    assert drafts == [] and rest == 1_000_000


def test_cover_rangfolge_l080_und_kader_minimum():
    # 13er-Kader, zwei Bank-MF: prob-5 (teuer) vor prob-None (billig);
    # max. len(squad) − 11 = 2 Verkäufe.
    squad = _squad_12() + [_sp("bank2", 5_000_000, prob=5, position=3)]
    drafts, _ = cover_sell_drafts(squad, frozenset(), 10_000_000, NOW)
    assert [d.player_id for d in drafts] == ["bank2", "bank1"]


def test_cover_grosse_betraege_bekommen_das_2h_fenster():
    squad = _squad_12() + [_sp("teuer", 6_000_000, prob=5, position=3)]
    drafts, _ = cover_sell_drafts(squad, frozenset(), 6_000_000, NOW)
    fenster = {d.player_id: d.execute_at - NOW for d in drafts}
    assert fenster["teuer"] == timedelta(hours=2)


# --- insert_cover_sells + Supersede-Schutz -----------------------------------


def test_insert_cover_ist_idempotent_je_spieler(session):
    drafts, _ = cover_sell_drafts(_squad_12(), frozenset(), 1_500_000, NOW)
    first = insert_cover_sells(session, drafts, NOW)
    second = insert_cover_sells(session, drafts, NOW)
    assert len(first) == 1 and second == []


def test_planner_supersedet_cover_vorhaben_nie(session):
    drafts, _ = cover_sell_drafts(_squad_12(), frozenset(), 1_500_000, NOW)
    (row,) = insert_cover_sells(session, drafts, NOW)
    stats = upsert_intents(session, [], NOW, market_pids=set())
    assert stats.superseded == []
    assert row.status == "planned"


# --- run_pfand_einloeser (End-to-End mit Fake-Client) ------------------------


class _Client:
    def __init__(self, *, budget: int, bids: list[dict], squad: list[SquadPlayer],
                 kickoff: datetime, market: list[MarketPlayer] | None = None):
        self._budget = budget
        self._bids = bids
        self._squad = squad
        self._kickoff = kickoff
        self._market = market or []

    async def get_matchdays(self):
        return [Fixture(matchday=3, home_team_id="1", away_team_id="2",
                        kickoff=self._kickoff)]

    async def get_budget(self, league_id):
        return self._budget

    async def get_own_bids(self, league_id, user_id):
        return self._bids

    async def get_squad(self, league_id):
        return self._squad

    async def get_market(self, league_id):
        return self._market


def _patch_client(monkeypatch, client):
    async def fake_factory():
        return client, SimpleNamespace(league_id="L1", user_id="U1")

    monkeypatch.setattr(rc, "make_kickbase_client", fake_factory)


@pytest.mark.asyncio
async def test_einloeser_zieht_deckung_bei_fehlbetrag(session, monkeypatch):
    client = _Client(
        budget=1_000_000,
        bids=[{"amount": 2_500_000, "player_id": "x"}],  # worst case −1,5M
        squad=_squad_12(),
        kickoff=NOW + timedelta(hours=70),
    )
    _patch_client(monkeypatch, client)
    out = await run_pfand_einloeser(session, None, NOW)
    assert out == "cover=1"
    rows = session.query(Intent).all()
    assert len(rows) == 1 and rows[0].kind == "sell"
    assert (rows[0].sources or {}).get("cover") is True


@pytest.mark.asyncio
async def test_einloeser_zaehlt_offene_deckung_und_verdoppelt_nicht(session, monkeypatch):
    client = _Client(
        budget=1_000_000,
        bids=[{"amount": 2_500_000, "player_id": "x"}],
        squad=_squad_12(),
        kickoff=NOW + timedelta(hours=70),
    )
    _patch_client(monkeypatch, client)
    assert await run_pfand_einloeser(session, None, NOW) == "cover=1"
    assert await run_pfand_einloeser(session, None, NOW) is None  # schon gedeckt


@pytest.mark.asyncio
async def test_einloeser_schweigt_ohne_fehlbetrag_oder_ausserhalb_horizont(
    session, monkeypatch
):
    ok = _Client(budget=5_000_000, bids=[], squad=_squad_12(),
                 kickoff=NOW + timedelta(hours=70))
    _patch_client(monkeypatch, ok)
    assert await run_pfand_einloeser(session, None, NOW) is None
    fern = _Client(budget=0, bids=[{"amount": 9_000_000, "player_id": "x"}],
                   squad=_squad_12(), kickoff=NOW + timedelta(hours=120))
    _patch_client(monkeypatch, fern)
    assert await run_pfand_einloeser(session, None, NOW) is None


@pytest.mark.asyncio
async def test_einloeser_meldet_luecke_wenn_bank_nicht_reicht(session, monkeypatch):
    client = _Client(
        budget=0,
        bids=[{"amount": 9_000_000, "player_id": "x"}],  # Fehlbetrag 9,01M
        squad=_squad_12(),  # Bank deckt nur 2M
        kickoff=NOW + timedelta(hours=70),
    )
    _patch_client(monkeypatch, client)
    out = await run_pfand_einloeser(session, None, NOW)
    assert out is not None and "luecke=" in out


# --- Dedupe der Lücken-Meldung (Turnaround-Studie P0-4) ----------------------


class _Kanal:
    def __init__(self) -> None:
        self.posts: list[str] = []

    async def send_info(self, text: str, **_: object) -> None:
        self.posts.append(text)


def _luecken_client(bid: int = 9_000_000) -> _Client:
    """Kader ohne Bank: nichts verkäuflich, die Lücke bleibt von Lauf zu Lauf gleich."""
    return _Client(
        budget=0,
        bids=[{"amount": bid, "player_id": "x"}],
        squad=_squad_12()[:11],
        kickoff=NOW + timedelta(hours=70),
    )


@pytest.mark.asyncio
async def test_einloeser_meldet_dieselbe_luecke_nur_einmal(session, monkeypatch):
    """Real passiert: Lücke unverändert — und der Stundentakt postete dieselbe
    Meldung bis zu 90-mal. Eine Meldung je Zustand genügt."""
    _patch_client(monkeypatch, _luecken_client())
    kanal = _Kanal()
    ergebnisse = [
        await run_pfand_einloeser(session, kanal, NOW + timedelta(hours=i)) for i in range(6)
    ]
    assert len(kanal.posts) == 1 and "Deckungslücke bleibt: 9.01M" in kanal.posts[0]
    # Die Job-Historie bleibt in JEDEM Lauf ehrlich, auch ohne neue Meldung.
    assert ergebnisse == ["cover=0 luecke=9010000"] * 6


@pytest.mark.asyncio
async def test_einloeser_meldet_erneut_wenn_sich_die_luecke_aendert(session, monkeypatch):
    client = _luecken_client()
    _patch_client(monkeypatch, client)
    kanal = _Kanal()
    await run_pfand_einloeser(session, kanal, NOW)
    await run_pfand_einloeser(session, kanal, NOW + timedelta(hours=1))
    client._bids = [{"amount": 12_000_000, "player_id": "x"}]  # neuer Zustand
    await run_pfand_einloeser(session, kanal, NOW + timedelta(hours=2))
    await run_pfand_einloeser(session, kanal, NOW + timedelta(hours=3))
    assert len(kanal.posts) == 2 and "12.01M" in kanal.posts[1]


@pytest.mark.asyncio
async def test_einloeser_meldet_eine_geschlossene_und_wieder_geoeffnete_luecke(
    session, monkeypatch
):
    """Review-Befund 21.09.: Lücke 9,01 M gemeldet, danach gedeckt, dann reißt ein
    neues Gebot fast dieselbe Lücke wieder auf. Ohne gemerkten Schluss-Zustand galt
    sie als „schon gemeldet" — genau die Stille, die die Studie abstellen sollte."""
    client = _luecken_client()
    _patch_client(monkeypatch, client)
    kanal = _Kanal()
    await run_pfand_einloeser(session, kanal, NOW)
    client._bids = []
    client._budget = 5_000_000  # gedeckt
    assert await run_pfand_einloeser(session, kanal, NOW + timedelta(hours=1)) is None
    assert await run_pfand_einloeser(session, kanal, NOW + timedelta(hours=2)) is None
    client._bids = [{"amount": 9_040_000, "player_id": "y"}]
    client._budget = 0
    await run_pfand_einloeser(session, kanal, NOW + timedelta(hours=3))
    assert len(kanal.posts) == 2 and "9.05M" in kanal.posts[1]
    # Der Schluss-Zustand wird einmal gemerkt, nicht in jedem gedeckten Lauf.
    from kickbase_agent.db.models import Report

    closed = [
        r for r in session.query(Report).filter(Report.source == rc.PFAND_GAP_SOURCE)
        if (r.meta or {}).get("signature") == "closed"
    ]
    assert len(closed) == 1


@pytest.mark.asyncio
async def test_einloeser_meldet_neue_deckungsverkaeufe_immer(session, monkeypatch):
    client = _luecken_client()
    _patch_client(monkeypatch, client)
    kanal = _Kanal()
    await run_pfand_einloeser(session, kanal, NOW)
    client._squad = _squad_12()  # ein Bank-Spieler kommt dazu → neuer Deckungsverkauf
    await run_pfand_einloeser(session, kanal, NOW + timedelta(hours=1))
    assert len(kanal.posts) == 2 and "Deckungsverkauf" in kanal.posts[1]


# --- Handel (Handels-Autopilot, Review 02.10.2026 P0) -------------------------


def _trade_bid(session, pid: str, amount: int, *, at: datetime = NOW - timedelta(hours=20)):
    """Ausgeführtes Handelsgebot samt Vorhaben, wie der Intent-Executor es hinterlässt."""
    action = TransferAction(
        league_id="L1", action_type="bid", player_id=pid, amount=amount, status="executed",
        idempotency_key=f"t-{pid}", rule_id="intent-bid", dry_run=False, payload={},
        created_at=at,
    )
    session.add(action)
    session.flush()
    session.add(Intent(
        created_at=at - timedelta(hours=1), updated_at=at, kind="bid", player_id=pid,
        player_name=pid, amount=amount, sources={"trade": True, "league_id": "L1"},
        listing_type="kickbase", deadline=at, execute_at=at, status="executed",
        executed_action_id=action.id,
    ))
    session.commit()


def _listing(pid: str, mv: int) -> MarketPlayer:
    return MarketPlayer(
        player=Player(id=pid, first_name="T", last_name=pid, team_id="T2", position=3,
                      market_value=mv),
        price=mv, expires_at=NOW + timedelta(hours=10),
    )


def _cover_sells(session) -> list[str]:
    return [r.player_id for r in session.query(Intent).filter(Intent.kind == "sell")]


@pytest.mark.asyncio
async def test_einloeser_zaehlt_den_handelsverkauf_als_deckung(session, monkeypatch):
    """Demo 2 des Reviews: Der Autopilot kauft gewollt ins Minus und verkauft zum Termin
    (T−50 h). Ohne Handelsdeckung verkaufte der Einlöser dafür einen Stammspieler."""
    _trade_bid(session, "t1", 3_000_000)
    _patch_client(monkeypatch, _Client(
        budget=-2_000_000, bids=[], squad=_squad_12() + [_sp("t1", 3_000_000)],
        kickoff=NOW + timedelta(hours=70),
    ))
    assert await run_pfand_einloeser(session, None, NOW) is None  # −2 M + 0,9 × 3 M
    assert _cover_sells(session) == []


@pytest.mark.asyncio
async def test_einloeser_deckt_nur_den_rest_und_laesst_den_handelsspieler_dem_autopiloten(
    session, monkeypatch
):
    _trade_bid(session, "t1", 3_000_000)
    _patch_client(monkeypatch, _Client(
        budget=-4_000_000, bids=[], squad=_squad_12() + [_sp("t1", 3_000_000)],
        kickoff=NOW + timedelta(hours=70),
    ))
    assert await run_pfand_einloeser(session, None, NOW) == "cover=1"  # Rest 1,31 M
    assert _cover_sells(session) == ["bank1"]


@pytest.mark.asyncio
async def test_einloeser_verkauft_handelsspieler_zuerst_wenn_der_termin_verpasst_ist(
    session, monkeypatch
):
    """Termin T−6 h ist seit 4 h vorbei (> 3 h Karenz), der Handelsspieler ist noch da: Sein
    Verkauf ist gescheitert und deckt nichts mehr — er geht zuerst, er steht ohnehin nur auf der
    Bank."""
    _trade_bid(session, "t1", 3_000_000)
    _patch_client(monkeypatch, _Client(
        budget=-2_000_000, bids=[], squad=_squad_12() + [_sp("t1", 3_000_000)],
        kickoff=NOW + timedelta(hours=2),
    ))
    assert await run_pfand_einloeser(session, None, NOW) == "cover=1"
    assert _cover_sells(session) == ["t1"]


@pytest.mark.asyncio
async def test_einloeser_laesst_elf_stammspieler_stehen_auch_neben_einem_handelsspieler(
    session, monkeypatch
):
    """Der Handelsspieler geht zum Termin — die Stammspieler dürfen nie unter elf fallen,
    auch wenn er (von Hand) in der Elf steht und der Kader zusammen dreizehn zählt."""
    _trade_bid(session, "t1", 3_000_000)
    squad = _squad_12()[:10] + [_sp("t1", 3_000_000, lo=10, position=4)]
    squad += [_sp("bank1", 2_000_000, prob=4), _sp("bank2", 5_000_000, prob=5)]
    _patch_client(monkeypatch, _Client(
        budget=-20_000_000, bids=[], squad=squad, kickoff=NOW + timedelta(hours=70),
    ))
    out = await run_pfand_einloeser(session, None, NOW)
    assert out is not None and out.startswith("cover=1 ")
    assert _cover_sells(session) == ["bank2"]


@pytest.mark.asyncio
async def test_einloeser_zaehlt_laufende_handelsgebote_mit_dem_marktwert_des_ziels(
    session, monkeypatch
):
    """Gewinnt das Handelsgebot, verkauft der Autopilot das Ziel zum Termin — dieselbe
    Anrechnung wie im Ergebnis-Wächter: Marktwert × 0,90, nicht der Gebotsbetrag."""
    _trade_bid(session, "z", 3_000_000, at=NOW - timedelta(hours=1))
    _patch_client(monkeypatch, _Client(
        budget=1_000_000, bids=[{"amount": 3_000_000, "player_id": "z"}], squad=_squad_12(),
        kickoff=NOW + timedelta(hours=70), market=[_listing("z", 2_800_000)],
    ))
    assert await run_pfand_einloeser(session, None, NOW) is None  # −2 M + 2,52 M


def test_cover_zaehlt_das_positions_minimum_mit_jedem_entwurf_herunter():
    """Vier ABW, zwei davon auf der Bank: Nur EINER ist Kasse — nach ihm stehen drei, und den
    zweiten Verkauf verweigerte die Schreibstelle (Positions-Minimum)."""
    squad = [_sp("gk", 3_000_000, lo=0, position=1)]
    squad += [_sp(f"d{i}", 3_000_000, lo=1 + i, position=2) for i in range(2)]
    squad += [_sp(f"m{i}", 3_000_000, lo=3 + i, position=3) for i in range(6)]
    squad += [_sp(f"s{i}", 3_000_000, lo=9 + i, position=4) for i in range(2)]
    squad += [_sp("b1", 2_000_000, prob=5, position=2), _sp("b2", 1_000_000, prob=5, position=2)]
    drafts, rest = cover_sell_drafts(squad, frozenset(), 10_000_000, NOW)
    assert [d.player_id for d in drafts] == ["b1"] and rest == 8_000_000


def test_cover_haelt_das_positions_minimum_im_stammkader_ohne_handelsspieler():
    """Codex-Review 02.10.2026: Drei Stamm-ABW (einer auf der Bank) plus ein Handels-ABW. Über
    den ganzen Kader gezählt war der Bank-ABW „Reserve“ — geht der Handels-ABW zum Termin, fehlt
    der Elf ein Abwehrspieler."""
    squad = [_sp("gk", 3_000_000, lo=0, position=1)]
    squad += [_sp(f"d{i}", 3_000_000, lo=1 + i, position=2) for i in range(2)]
    squad += [_sp(f"m{i}", 3_000_000, lo=3 + i, position=3) for i in range(6)]
    squad += [_sp(f"s{i}", 3_000_000, lo=9 + i, position=4) for i in range(2)]
    squad += [_sp("bd", 2_000_000, prob=5, position=2), _sp("td", 2_000_000, position=2)]
    drafts, rest = cover_sell_drafts(squad, frozenset(), 1_000_000, NOW,
                                     trade_ids=frozenset({"td"}))
    assert drafts == [] and rest == 1_000_000
    # Termin verpasst: Der Handels-ABW selbst darf gehen (ganzer Kader: vier ABW).
    drafts, _ = cover_sell_drafts(squad, frozenset(), 1_000_000, NOW,
                                  trade_ids=frozenset({"td"}), trade_due=True)
    assert [d.player_id for d in drafts] == ["td"]


def test_cover_zaehlt_auch_bei_handelsspielern_den_ganzen_kader_herunter():
    """Stammkader nur noch mit zwei ABW, dazu zwei Handels-ABW, Termin verpasst: Der erste geht
    (vier → drei ABW im Kader), der zweite nicht — die Schreibstelle hielte das Minimum."""
    squad = [_sp("gk", 3_000_000, lo=0, position=1)]
    squad += [_sp(f"d{i}", 3_000_000, lo=1 + i, position=2) for i in range(2)]
    squad += [_sp(f"m{i}", 3_000_000, lo=3 + i, position=3) for i in range(6)]
    squad += [_sp(f"s{i}", 3_000_000, lo=9 + i, position=4) for i in range(2)]
    squad += [_sp("t1", 3_000_000, position=2), _sp("t2", 2_000_000, position=2)]
    drafts, rest = cover_sell_drafts(squad, frozenset(), 10_000_000, NOW,
                                     trade_ids=frozenset({"t1", "t2"}), trade_due=True)
    assert [d.player_id for d in drafts] == ["t1"] and rest == 7_000_000
