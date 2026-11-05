"""build_plan: Gebots-Exposure + Club-Zählung LIVE aus get_own_bids.

Review-Fund: Vorher wurden offene Gebote aus bis zu 4 Tage alten
transfer_actions-Audit-Zeilen rekonstruiert (zählte gewonnene/stornierte Gebote
mit). Jetzt fließen Betrag UND team_id jedes offenen Gebots live aus dem Markt
(`get_own_bids`) in Kaufkraft und Vereinslimit. Außerdem: der Saturations-Flag
der 200er-Zählung wandert in den Plan und sperrt die Engine konservativ.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import kickbase_agent.cli.plan as plan_mod
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.kickbase.models import MarketPlayer, Player, Position
from kickbase_agent.scouts.ligainsider.models import Availability, Status
from kickbase_agent.trading.trade_budget import FEED_MAX_ITEMS


def _mp(pid: str, mv: int, team_id: str, lineup_prob: int | None = 1) -> MarketPlayer:
    return MarketPlayer(
        player=Player(
            id=pid,
            first_name="",
            last_name=f"P{pid}",
            position=Position.MIDFIELDER,
            market_value=mv,
            team_id=team_id,
            lineup_prob=lineup_prob,
            fitness_status=0,
            mv_change_24h=100_000,
        ),
        price=mv,
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )


def _bid(pid: str, team_id: str, amount: int) -> dict:
    # Rückgabeform von KickbaseClient.get_own_bids (live geprobt 20.08.):
    # player_id, name, team_id (tid aus dem Markt-Payload), amount, offer_id, expires_in_s
    return {
        "player_id": pid,
        "name": f"P{pid}",
        "team_id": team_id,
        "amount": amount,
        "offer_id": f"o-{pid}",
        "expires_in_s": 600,
    }


def _availability(pid: str, status: Status) -> Availability:
    return Availability(
        player_id=pid,
        status=status,
        multiplier=1.0 if status is Status.STARTER else 0.4,
        reason="test",
        confidence=1.0,
    )


def test_verified_starter_needs_evidence_and_respects_li_veto():
    """Elf-Bedarf zählt Belege, nicht bloß Körper: LI startet oder KB 1/2.

    Fehlende Daten, KB 3 und ein ausdrücklicher LI-Bankstatus sind keine
    belastbare Startelf-Abdeckung.
    """
    assert plan_mod.is_verified_starter("li", None, {"li": Status.STARTER}, None) is True
    assert plan_mod.is_verified_starter("conflict", 5, {"conflict": Status.STARTER}, 0) is False
    assert plan_mod.is_verified_starter("li-kb3", 3, {"li-kb3": Status.STARTER}, None) is True
    assert plan_mod.is_verified_starter("kb", 2, {}, 0) is True
    assert plan_mod.is_verified_starter("kb-fit-open", 2, {}, None) is False
    assert plan_mod.is_verified_starter("invalid-zero", 0, {}, 0) is False
    assert plan_mod.is_verified_starter("invalid-negative", -1, {}, 0) is False
    assert plan_mod.is_verified_starter("li-injured", 1, {"li-injured": Status.STARTER}, 2) is False
    assert plan_mod.is_verified_starter("offen", None, {}, None) is False
    assert plan_mod.is_verified_starter("fifty", 3, {}, 0) is False
    assert plan_mod.is_verified_starter("bank", 1, {"bank": Status.BENCH}, 0) is False
    assert plan_mod.is_verified_starter("out", 1, {"out": Status.OUT}, 0) is False


def test_verified_deployable_counts_ignore_bench_unknown_and_listed_players():
    players = [
        Player(
            id="d-li",
            first_name="",
            last_name="LI",
            position=Position.DEFENDER,
            market_value=1_000_000,
            team_id="1",
            lineup_prob=None,
        ),
        Player(
            id="d-kb",
            first_name="",
            last_name="KB",
            position=Position.DEFENDER,
            market_value=1_000_000,
            team_id="1",
            lineup_prob=2,
            fitness_status=0,
        ),
        Player(
            id="m-bank",
            first_name="",
            last_name="Bank",
            position=Position.MIDFIELDER,
            market_value=1_000_000,
            team_id="2",
            lineup_prob=1,
        ),
        Player(
            id="m-open",
            first_name="",
            last_name="Offen",
            position=Position.MIDFIELDER,
            market_value=1_000_000,
            team_id="2",
            lineup_prob=None,
        ),
        Player(
            id="s-list",
            first_name="",
            last_name="Listed",
            position=Position.STRIKER,
            market_value=1_000_000,
            team_id="3",
            lineup_prob=1,
        ),
    ]
    statuses = {
        a.player_id: a.status
        for a in [_availability("d-li", Status.STARTER), _availability("m-bank", Status.BENCH)]
    }

    assert plan_mod.verified_deployable_counts(
        players, statuses, {"s-list"}, unavailable_ids=frozenset({"m-open"})
    ) == {2: 2}


class FakePlanClient:
    def __init__(self):
        self.market: list[MarketPlayer] = []
        self.own_bids: list[dict] = []
        self.feed: list[dict] = []
        self.max_per_club: int = 11
        self.budget: int = 10_000_000
        self.own_bids_call: tuple[str, str] | None = None

    async def get_squad(self, league_id):
        return []

    async def get_market(self, league_id):
        return self.market

    async def get_player(self, league_id, player_id):
        # Produktionsform (L-111/L-112, nachgezählt 25.09.2026): Der Detailabruf liefert
        # `tfhmvt`, `Player.from_api` setzt dann mv_change_24h_observed=True — bei allen
        # 234 gewählten Kickbase-Zielen der letzten 300 Engine-Belege. Ein Test, der
        # „nicht beobachtet" prüfen will, setzt False ausdrücklich.
        player = next(m.player for m in self.market if m.player.id == player_id)
        if player.mv_change_24h_observed is None:
            player = player.model_copy(update={"mv_change_24h_observed": True})
        return player

    async def get_budget(self, league_id):
        return self.budget

    async def get_matchdays(self):
        return []

    async def get_team_names(self):
        return {}

    async def get_league_rules(self, league_id):
        return {"max_players_per_club": self.max_per_club, "is_admin": False}

    async def get_own_bids(self, league_id, user_id):
        self.own_bids_call = (league_id, user_id)
        return self.own_bids

    async def get_ranking(self, league_id):
        return [SimpleNamespace(user_id="u1", name="emil")]

    async def get_activity_feed(self, league_id, max_items=30):
        return self.feed[:max_items]


def _patch_world(monkeypatch, client: FakePlanClient, scores: dict[str, float]):
    async def fake_factory():
        auth = SimpleNamespace(
            token="t",
            user_id="u1",
            leagues=[SimpleNamespace(id="L1", name="BASE")],
            league_id="L1",
        )
        return client, auth

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    async def fake_collect(players, fixtures, team_names, history, perf):
        return [
            ExplosionCandidate(
                player_id=p.id,
                name=p.last_name,
                team_id=p.team_id,
                position=int(p.position),
                current_market_value=p.market_value,
                total_score=scores[p.id],
                lineup_score=scores[p.id],
                signals=[],
            )
            for p in players
        ]

    async def fake_enrich(cands, **_):
        return SimpleNamespace(candidates=cands, snapshot=None, availabilities=[])

    monkeypatch.setattr(plan_mod, "make_kickbase_client", fake_factory)
    monkeypatch.setattr(plan_mod, "SessionLocal", _FakeSession)
    monkeypatch.setattr(plan_mod, "load_history", lambda session, ids: {})
    monkeypatch.setattr(plan_mod, "load_performance", lambda session, ids: {})
    monkeypatch.setattr(plan_mod, "load_recent_tips", lambda session: [])
    # Jev-Rolle (29.09.2026): ohne Artikel kein Urteil; eigene Tests in test_plan_news_role.py.
    monkeypatch.setattr(plan_mod, "load_recent_articles", lambda session, since: [])
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments", lambda *args: [])
    monkeypatch.setattr(plan_mod, "collect_from_players", fake_collect)
    monkeypatch.setattr(plan_mod, "enrich_candidates", fake_enrich)
    monkeypatch.setattr(plan_mod, "load_active", lambda: None)
    monkeypatch.setattr(plan_mod, "load_competition", lambda *args: None)
    monkeypatch.setattr(
        plan_mod, "matchday_deadline", lambda fixtures, now: (1, now + timedelta(days=3))
    )
    # Planungs-Dispo (Tausch-Finanzierung, Emil 22./23.09.2026, bis 05.10.2026) hier
    # AUS: diese Domain-Welt prueft Puffer, Staffel und Deckung ohne geliehenes Geld.
    # Die Dispo-Tests in tests/cli/test_plan_dispo.py schalten ihn ausdruecklich ein.
    monkeypatch.setattr(
        plan_mod, "PLANNING_OVERDRAFT_UNTIL", datetime(2000, 1, 1, tzinfo=UTC)
    )
    monkeypatch.setattr(plan_mod, "load_swap_couplings", lambda *args: [])


async def test_open_bid_exposure_comes_live_from_get_own_bids(monkeypatch):
    """Ein offenes 7M-Gebot senkt die Kaufkraft live. Seit Paket 1 (P0-3, 21.09.2026)
    gilt bei Elf-Lücke der volle Rahmen auf dem Planungsgeld Konto − Gebote −
    10.000 €: 10M − 7M − 0,01M = 2,99M < Ziel 4M → kein Kaufvorschlag mehr.
    Ziel unter 5 M, damit hier die Geldregel geprüft wird, nicht der Rollenbeleg."""
    client = FakePlanClient()
    client.market = [_mp("mk1", 4_000_000, team_id="77")]
    _patch_world(monkeypatch, client, scores={"mk1": 0.9})

    baseline = await plan_mod.build_plan()
    assert len(baseline.buys) == 1
    # Gebotsregel V3 (25.09.2026): +100.000 € seit gestern = +2,56 % Tagesschritt →
    # 3-Tage-Prognose + 3 Pp = +9,6 % (unter der Zuschlagskurve von 28,75 %).
    expected_bid = 4_384_000
    assert baseline.buys[0].action.amount == expected_bid
    assert baseline.trade_count_saturated is False

    client.own_bids = [_bid("x9", team_id="88", amount=7_000_000)]
    plan = await plan_mod.build_plan()
    assert plan.buys == []
    assert client.own_bids_call == ("L1", "u1")  # Live-Abruf, keine Audit-Zeilen


async def test_open_bids_count_toward_club_limit(monkeypatch):
    """team_id steckt direkt in get_own_bids (tid aus dem Markt-Payload) —
    ein offenes Gebot auf Club 77 blockiert bei lpc=1 weitere 77er-Käufe."""
    client = FakePlanClient()
    client.market = [_mp("mk1", 1_000_000, team_id="77")]
    client.max_per_club = 1
    _patch_world(monkeypatch, client, scores={"mk1": 0.9})

    client.own_bids = [_bid("x9", team_id="88", amount=100_000)]
    plan = await plan_mod.build_plan()
    assert len(plan.buys) == 1  # fremder Club → Limit nicht betroffen

    client.own_bids = [_bid("x9", team_id="77", amount=100_000)]
    plan = await plan_mod.build_plan()
    assert plan.buys == []  # gleicher Club → Vereinslimit erreicht


async def test_completion_accepts_known_kb_three_but_rejects_unknown_role(monkeypatch):
    """Eine offene Einsatzchance bleibt bekannt; fehlende Rolle bleibt gesperrt."""
    client = FakePlanClient()
    client.market = [_mp("mk1", 1_000_000, team_id="77", lineup_prob=3)]
    _patch_world(monkeypatch, client, scores={"mk1": 0.9})
    monkeypatch.setattr(
        plan_mod,
        "matchday_deadline",
        lambda fixtures, now: (1, now + timedelta(days=3)),
    )
    [proposal] = (await plan_mod.build_plan()).buys
    assert proposal.action.squad_need is True
    # Gebotsregel V3: +100.000 € auf 900.000 € = +11,1 % Tagesschritt → Deckel 15 %.
    assert proposal.action.amount == 1_150_000
    client.market = [_mp("mk1", 1_000_000, team_id="77", lineup_prob=None)]
    assert (await plan_mod.build_plan()).buys == []


async def test_saturated_trade_feed_blocks_engine_and_flags_plan(monkeypatch):
    """Voller Activity-Feed → Zählung ist Untergrenze: Engine schlägt nichts
    vor, der Plan trägt das Flag für die Anzeige (kein stilles „0/200")."""
    client = FakePlanClient()
    client.market = [_mp("mk1", 1_000_000, team_id="77")]
    client.feed = [{"t": 3, "data": {"ln": "Listing"}} for _ in range(FEED_MAX_ITEMS)]
    _patch_world(monkeypatch, client, scores={"mk1": 0.9})

    plan = await plan_mod.build_plan()
    assert plan.trade_count_saturated is True
    assert plan.trades_used == 0  # Untergrenze — trotzdem Sperre
    assert plan.buys == [] and plan.sells == []


async def test_own_listed_players_never_become_buy_candidates(monkeypatch):
    """Hermes-Audit: gelistete EIGENE Spieler stehen in Kader UND Markt —
    sie dürfen nicht als Markt-Kandidat gescort oder zum Kauf vorgeschlagen
    werden (16 statt 14 Kader-Zeilen, falsche Slots)."""
    from kickbase_agent.kickbase.models import SquadPlayer

    client = FakePlanClient()
    eigener = Player(
        id="sq1",
        first_name="Luca",
        last_name="Reggiani",
        position=Position.DEFENDER,
        market_value=4_000_000,
        team_id="5",
    )
    client.market = [
        MarketPlayer(player=eigener, price=4_000_000, expires_at=datetime.now(UTC)),
        _mp("mk1", 5_000_000, team_id="77"),
    ]

    async def squad_with_listed(league_id):
        return [SquadPlayer(player=eigener, is_starter=False)]

    client.get_squad = squad_with_listed
    _patch_world(monkeypatch, client, scores={"mk1": 0.9, "sq1": 0.9})

    plan = await plan_mod.build_plan()
    market_ids = {c.player_id for c in plan.market_candidates}
    assert "sq1" not in market_ids  # nicht als Markt-Kandidat
    assert "mk1" in market_ids  # echte Marktspieler bleiben
    assert all(p.action.player_id != "sq1" for p in plan.buys)
