"""Gebots-Wächter: erinnert im Eskalations-Fenster, sonst leise — und nur 1×."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import kickbase_agent.worker.bid_watch as bw
from kickbase_agent.decision.guardrails import Guardrails
from kickbase_agent.worker.bid_watch import (
    format_escalations,
    run_bid_watch,
)


@pytest.fixture(autouse=True)
def _clear_marker():
    """Modul-globalen Erinnerungs-Merker je Test zurücksetzen."""
    bw._reminded.clear()
    yield
    bw._reminded.clear()


def _bid(pid="1", amount=5_113_337, exs=6_300, name="Wirtz", offer_id="o1") -> dict:
    return {"player_id": pid, "name": name, "team_id": "9", "amount": amount,
            "offer_id": offer_id, "expires_in_s": exs}


MV = {"1": 5_000_000}  # Ziel: 5.000.000 × 1,05 + 13.337 = 5.263.337
_G = Guardrails()
TARGET = int(5_000_000 * (1 + _G.bid_overpay_fraction)) + _G.bid_psych_offset
NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)


def _reviews(now=NOW):
    """Interne ausgewählte Kaufentscheidung, ausdrücklich kein Kickbase-Payload."""
    return {"1": {
        "schema_version": 1, "player_id": "1", "selected": True, "max_bid_eur": TARGET,
        "observed_at": now.isoformat(), "valid_until": (now + timedelta(hours=2)).isoformat(),
        "market_value_eur": 5_000_000, "purpose": "starter",
        "reasons": ["Startrolle und finanzierte Alternative geprüft."],
    }}


def find_escalations(*args, **kwargs):
    kwargs.setdefault("purchase_reviews", _reviews())
    kwargs.setdefault("now", NOW)
    return bw.find_escalations(*args, **kwargs)


# --- find_escalations ---

def test_escalation_inside_window_below_target():
    """Restzeit 1h 45m (Fenster 1–2h), Gebot unter Ziel → Erinnerung mit Ziel."""
    out = find_escalations([_bid()], MV, Guardrails())
    assert len(out) == 1
    assert out[0]["target"] == TARGET
    assert out[0]["name"] == "Wirtz"


@pytest.mark.parametrize("change", [None, {"selected": False},
                                    {"valid_until": NOW.isoformat()}, {"player_id": "other"}])
def test_no_raise_recommendation_without_valid_selected_review(change):
    reviews = {} if change is None else {"1": {**_reviews()["1"], **change}}
    assert bw.find_escalations([_bid()], MV, purchase_reviews=reviews, now=NOW) == []


@pytest.mark.parametrize("cap,target,expected", [(5_200_000, 5_400_000, 5_200_000),
                                                (5_400_000, 5_250_000, 5_250_000),
                                                (6_000_000, 6_000_000, 5_500_000)])
def test_raise_stays_within_player_price_and_existing_market_value_cap(cap, target, expected):
    review = {**_reviews()["1"], "max_bid_eur": cap, "target_bid_eur": target}
    [entry] = bw.find_escalations([_bid()], MV, purchase_reviews={"1": review}, now=NOW)
    assert entry["target"] == expected


@pytest.mark.parametrize("chosen,expected", [(5_220_000, 5_220_000), (5_600_000, None)])
def test_engine_watcher_uses_selected_scenario_not_economic_cap(chosen, expected):
    current = 5_113_337
    review = {
        **_reviews()["1"], "purpose": "lineup_upgrade", "max_bid_eur": 5_700_000,
        "engine_version": "season-v1", "plan_id": "team-1",
        "team_plan": {
            "plan_id": "team-1", "observed_at": NOW.isoformat(), "budget_eur": 10_000_000,
            "open_bid_exposure_eur": current, "reserved_cash_eur": max(current, chosen),
            "open_bids": [{"player_id": "1", "amount_eur": current}],
            "owned_player_ids": [],
            "purchases": [{"player_id": "1", "amount_eur": chosen, "max_bid_eur": 5_700_000}],
        },
        "bid_scenarios": [
            {"amount": current, "win_probability": None, "selected": False},
            {"amount": chosen, "win_probability": None, "selected": True},
        ],
    }
    result = bw.find_escalations([_bid(amount=current)], MV,
                                 purchase_reviews={"1": review}, now=NOW)
    if expected is None:
        assert result == []  # technische Kappe erzeugt keinen neuen Szenariopreis
    else:
        assert [row["target"] for row in result] == [expected]


def test_stored_review_comes_from_latest_executed_bid_in_our_league(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from kickbase_agent.db.models import Base, TransferAction

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(bw, "SessionLocal", sessionmaker(bind=engine))
    review = _reviews()["1"]
    # Interne Audit-Zeilen: kein API-Fixture. Neuere Dry-runs, Fehler und eine
    # andere Liga dürfen den echten Kaufbeleg nicht verdrängen.
    rows = [
        ("L1", "executed", False, "bid", {"purchase_review": review, "squad_need": True}),
        ("L2", "executed", False, "bid", {}),
        ("L1", "failed", False, "bid", {}),
        ("L1", "executed", True, "bid", {}),
        ("L1", "executed", False, "list", {}),
    ]
    with Session(engine) as session:
        for i, (league, status, dry_run, kind, payload) in enumerate(rows):
            session.add(TransferAction(
                league_id=league, status=status, dry_run=dry_run, action_type=kind,
                player_id="1", amount=5_113_337, payload=payload, idempotency_key=f"audit-{i}",
                created_at=NOW + timedelta(minutes=i),
            ))
        session.commit()
    assert bw._load_purchase_reviews("L1", {"1"}) == {
        "1": {**review, "squad_need": True},
    }
    assert bw._load_purchase_reviews("L1", {"missing"}) == {}
    with Session(engine) as session:
        session.add(TransferAction(
            league_id="L1", status="executed", dry_run=False, action_type="bid",
            player_id="1", amount=5_120_000, payload={}, idempotency_key="manual-latest",
            created_at=NOW + timedelta(hours=1),
        ))
        session.commit()
    assert bw._load_purchase_reviews("L1", {"1"}) == {}  # kein Rückgriff auf alten Beleg


def test_quiet_outside_window():
    """Zu früh (Staffel hält), Fenstergrenzen, abgelaufen → alles leise."""
    g = Guardrails()  # Fenster: 3600 < exs < 7200
    for exs in (10_800, 7_200, 3_600, 0, -5):
        assert find_escalations([_bid(exs=exs)], MV, g) == []


def test_quiet_when_bid_at_or_above_target():
    """Gebot schon auf/über Ziel-Level → nichts zu erinnern."""
    assert find_escalations([_bid(amount=TARGET)], MV, Guardrails()) == []
    assert find_escalations([_bid(amount=TARGET + 1)], MV, Guardrails()) == []


def test_quiet_when_player_left_market():
    """Spieler nicht (mehr) im Markt → kein Ziel bestimmbar, keine Erinnerung."""
    assert find_escalations([_bid(pid="99")], MV, Guardrails()) == []


def test_reminds_only_once_per_listing():
    """Merker (player_id, offer_id) unterdrückt die zweite Erinnerung."""
    assert find_escalations([_bid()], MV, Guardrails(), reminded={("1", "o1")}) == []
    # anderes Listing/Gebot desselben Spielers erinnert wieder
    out = find_escalations([_bid(offer_id="o2")], MV, Guardrails(), reminded={("1", "o1")})
    assert len(out) == 1


def test_raise_hours_is_configurable():
    """bid_ladder_raise_hours verschiebt das Fenster (hier: 3–4h Restzeit)."""
    g = Guardrails(bid_ladder_raise_hours=4.0)
    assert len(find_escalations([_bid(exs=12_600)], MV, g)) == 1   # 3h 30m
    assert find_escalations([_bid(exs=6_300)], MV, g) == []        # unter 3h


# --- format_escalations ---

def test_format_contains_amounts_rest_and_route_hint():
    entries = find_escalations([_bid()], MV, Guardrails())
    msg = format_escalations(entries)
    assert msg is not None
    assert "⏫" in msg and "Gebots-Eskalation empfohlen" in msg
    assert "Wirtz" in msg
    assert "5.11M €" in msg          # aktuelles Gebot
    assert "5.41M €" in msg          # empfohlenes Ziel (+5 %)
    assert "1h 45m" in msg           # Restzeit
    assert "individuelles Limit" in msg and "finanzierte Alternative" in msg
    assert "+9 %" not in msg
    # Textpräzision (Review-Fund): der Befehl gehört an Claude im CHAT — der
    # Discord-Bot hat keinen on_message-Handler und versteht keine Nachrichten.
    assert "sag Claude im Chat" in msg and "mach den Trade" in msg
    # Vollmandat (25.08.): Die Karte erinnert nur, sie verlangt keine Freigabe.
    assert "erhöht nie selbst" in msg
    assert "Freigabe braucht es seit dem Vollmandat nicht" in msg


def test_format_none_without_candidates():
    assert format_escalations([]) is None


# --- run_bid_watch (Client/Notifier gemockt) ---

class _FakeChannel:
    def __init__(self):
        self.sent: list[str] = []
        self.kinds: list[str | None] = []

    async def send_info(self, text: str, *, kind: str | None = None, title=None) -> None:
        self.sent.append(text)
        self.kinds.append(kind)


def _patch_world(monkeypatch, bids, market, squad=()):
    class FakeClient:
        async def get_own_bids(self, league_id, user_id):
            assert (league_id, user_id) == ("L1", "U1")
            return bids

        async def get_market(self, league_id):
            return market

        async def get_squad(self, league_id):
            return list(squad)

    async def fake_factory():
        auth = SimpleNamespace(
            token="t", user_id="U1", leagues=[SimpleNamespace(id="L1")], league_id="L1"
        )
        return FakeClient(), auth

    monkeypatch.setattr(bw, "make_kickbase_client", fake_factory)
    monkeypatch.setattr(bw, "_load_purchase_reviews", lambda *_: _reviews(datetime.now(UTC)))


def _mp(pid: str, mv: int, price: int = 0, name: str = "Test Spieler",
        expires_in: float | None = None, seller_id: str = ""):
    from datetime import UTC, datetime, timedelta
    first, last = name.split(" ", 1)
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=expires_in)
        if expires_in is not None else None
    )
    return SimpleNamespace(
        price=price,
        expires_at=expires_at,
        seller_id=seller_id,  # leer = Kickbase-Listing (wie MarketPlayer)
        player=SimpleNamespace(
            id=pid, market_value=mv, first_name=first, last_name=last
        ),
    )


@pytest.mark.asyncio
async def test_run_posts_one_message_then_stays_quiet(monkeypatch):
    """Erster Lauf postet EINE Nachricht; der zweite schweigt (Merker gesetzt)."""
    channel = _FakeChannel()
    _patch_world(monkeypatch, [_bid()], [_mp("1", 5_000_000)])
    assert await run_bid_watch(channel) == "posted"
    assert len(channel.sent) == 1 and "Wirtz" in channel.sent[0]
    assert channel.kinds == ["bid_watch"]  # Berichte-Ablage: explizite Berichtsart
    assert await run_bid_watch(channel) == "quiet"
    assert len(channel.sent) == 1


@pytest.mark.asyncio
async def test_run_quiet_without_open_bids(monkeypatch):
    channel = _FakeChannel()
    _patch_world(monkeypatch, [], [])
    assert await run_bid_watch(channel) == "quiet"
    assert channel.sent == []


@pytest.mark.asyncio
async def test_run_quiet_when_bids_outside_window(monkeypatch):
    """Offene Gebote weit vor Ablauf → kein Post (Staffel hält das Erstgebot)."""
    channel = _FakeChannel()
    _patch_world(monkeypatch, [_bid(exs=36_000)], [_mp("1", 5_000_000)])
    assert await run_bid_watch(channel) == "quiet"
    assert channel.sent == []


# ------------------------------------------------------- Schnäppchen-Radar

def test_bargain_found_below_85_percent():
    out = bw.find_bargains([_mp("9", 8_000_000, price=4_830_000, name="Otavio Silva")],
                           own_bid_pids=set(), squad_pids=set())
    assert len(out) == 1
    assert out[0]["discount_pct"] == 40
    assert out[0]["suggested"] == 4_830_000 + 13_337


def test_bargain_ignores_small_discounts_own_bids_and_squad():
    market = [
        _mp("1", 10_000_000, price=9_500_000),           # nur −5 %
        _mp("2", 10_000_000, price=5_000_000),           # eigenes Gebot
        _mp("3", 10_000_000, price=5_000_000),           # eigener Kader (Listing)
        _mp("4", 500_000, price=100_000),                # MW zu klein
    ]
    out = bw.find_bargains(market, own_bid_pids={"2"}, squad_pids={"3"})
    assert out == []


def test_bargain_seen_suppresses_repeat():
    mp = _mp("9", 8_000_000, price=4_830_000)
    assert bw.find_bargains([mp], set(), set(), seen={("9", 4_830_000)}) == []


def test_bargain_skips_manager_listings_err5080():
    """Manager-Listings verlangen ≥ 100 % MW (err 5080, 24.08.) — der „Discount"
    existiert nicht (prc ist Deko), Sub-MW-Listings inaktiver Manager sind
    überwiegend Zombies. Sie sind KEINE Schnäppchen mehr; Kickbase-Listings
    (Underpay erlaubt) bleiben der einzige Radar-Gegenstand."""
    manager = _mp("9", 8_000_000, price=4_830_000, seller_id="4242")
    kickbase = _mp("7", 8_000_000, price=4_830_000)
    out = bw.find_bargains([manager, kickbase], own_bid_pids=set(), squad_pids=set())
    assert [b["player_id"] for b in out] == ["7"]


def test_format_bargains_warns_about_cancel_trap():
    msg = bw.format_bargains(bw.find_bargains(
        [_mp("9", 8_000_000, price=4_830_000)], set(), set()))
    assert "Schnäppchen-Radar" in msg
    assert "Storno verbrennt" in msg
    assert bw.format_bargains([]) is None


@pytest.mark.asyncio
async def test_run_stays_quiet_without_own_bids(monkeypatch):
    """Meldungs-Umbau 31.08.: Schnaeppchen-Radar und Sniper-Fenster posten
    nicht mehr (81 Posts/Woche, redundant zu den Veto-Karten) — ohne eigene
    Gebote bleibt der Lauf komplett still."""
    monkeypatch.setattr(bw, "_bargain_seen", set())
    channel = _FakeChannel()
    _patch_world(monkeypatch, [], [_mp("9", 8_000_000, price=4_000_000)])
    assert await run_bid_watch(channel) == "quiet"
    assert channel.sent == []


# ------------------------------------------------------------------ Sniper

def test_snipe_finds_expiring_kickbase_listing():
    out = bw.find_snipes([_mp("7", 5_000_000, price=5_000_000, expires_in=3600)],
                         own_bid_pids=set(), squad_pids=set())
    assert len(out) == 1
    assert out[0]["rest_s"] <= 3600


def test_snipe_ignores_manager_listings_far_expiry_and_small_mv():
    market = [
        _mp("1", 5_000_000, price=5_000_000),                      # kein expires_at (Manager)
        _mp("2", 5_000_000, price=5_000_000, expires_in=90_000),   # weit weg
        _mp("3", 1_000_000, price=1_000_000, expires_in=3600),     # MW zu klein
        _mp("4", 5_000_000, price=5_000_000, expires_in=-60),      # schon abgelaufen
    ]
    assert bw.find_snipes(market, set(), set()) == []


def test_snipe_skips_own_bids_squad_and_seen():
    mp = _mp("7", 5_000_000, price=5_000_000, expires_in=3600)
    assert bw.find_snipes([mp], {"7"}, set()) == []
    assert bw.find_snipes([mp], set(), {"7"}) == []
    assert bw.find_snipes([mp], set(), set(), seen={"7"}) == []


def test_format_snipes_no_longer_advertises_bids_below_market_value():
    """Paket 1, P0-6: ligaweit gingen 0 von 316 Käufen unter Marktwert weg, Kickbase
    lehnt Gebote unter MW ab (Leiter-Beweis im Executor-Test). Der Text versprach
    trotzdem „~90 % MW = realistische Chance" — eine Einladung zu verbrannten Slots."""
    msg = bw.format_snipes(bw.find_snipes(
        [_mp("7", 5_000_000, price=5_000_000, expires_in=1800)], set(), set()))
    assert "Sniper-Fenster" in msg
    for falsch in ("90 %", "Veerman", "Unter-MW", "Lotterie"):
        assert falsch not in msg
    assert "Marktwert" in msg
    assert bw.format_snipes([]) is None
