from datetime import UTC, datetime

from kickbase_agent.kickbase.models import (
    League,
    MarketPlayer,
    Player,
    Position,
    SquadPlayer,
)


def test_player_parses_from_api_shape():
    raw = {
        "i": "12345",
        "fn": "Florian",
        "n": "Wirtz",
        "pos": 3,
        "mv": 20800000,
        "mvt": 1,
        "p": 112,
        "tid": "9",
    }
    player = Player.from_api(raw)
    assert player.id == "12345"
    assert player.first_name == "Florian"
    assert player.last_name == "Wirtz"
    assert player.position is Position.MIDFIELDER
    assert player.market_value == 20_800_000
    assert player.points == 112
    assert player.team_id == "9"


def test_position_enum_covers_all_four():
    assert Position.from_api(1) is Position.GOALKEEPER
    assert Position.from_api(2) is Position.DEFENDER
    assert Position.from_api(3) is Position.MIDFIELDER
    assert Position.from_api(4) is Position.STRIKER


def test_league_parses_from_api_shape():
    raw = {"id": "42", "n": "Kickbase Mit Kumpels", "b": 50_000_000}
    league = League.from_api(raw)
    assert league.id == "42"
    assert league.name == "Kickbase Mit Kumpels"
    assert league.budget == 50_000_000


def test_market_player_parses_with_expiry():
    raw = {
        "i": "99",
        "fn": "Hidden",
        "n": "Gem",
        "pos": 4,
        "mv": 5_000_000,
        "price": 5_500_000,
        "exp": 1714320000,
    }
    mp = MarketPlayer.from_api(raw)
    assert mp.player.id == "99"
    assert mp.price == 5_500_000
    assert mp.expires_at == datetime.fromtimestamp(1714320000)


def test_market_player_parses_v4_keys_prc_and_dt_iso():
    """Echte v4-Marktantworten nutzen prc (Preis) und dt (ISO-Ablaufzeitpunkt)."""
    raw = {
        "i": "32",
        "fn": "Loris",
        "n": "Karius",
        "pos": 1,
        "mv": 16_495_882,
        "mvt": 1,
        "tid": "8",
        "prc": 16_240_896,
        "dt": "2026-08-15T20:50:31Z",
    }
    mp = MarketPlayer.from_api(raw)
    assert mp.price == 16_240_896
    assert mp.player.first_name == "Loris"
    assert mp.expires_at.year == 2026
    assert mp.expires_at.month == 8


def test_market_player_parses_v4_keys_prc_and_exs():
    """Echte v4-Antworten nutzen prc (Preis) und exs (Restlaufzeit in Sekunden)."""
    raw = {
        "i": "9",
        "fn": "Nick",
        "n": "Woltemade",
        "pos": 4,
        "mv": 12_000_000,
        "tid": "10",
        "prc": 12_500_000,
        "exs": 3600,
    }
    mp = MarketPlayer.from_api(raw)
    assert mp.price == 12_500_000
    remaining = (mp.expires_at - datetime.now(UTC)).total_seconds()
    assert 3500 < remaining <= 3600


def test_squad_player_parses_fitness_status_not_starter():
    # `st` ist der FITNESS-Status (0 fit, 2 angeschlagen — live bewiesen
    # 23.08.26: Bernardo st=2 bei Achillessehnen-Problemen). Ein Startelf-Flag
    # liefert die API nicht (lst=1 für ALLE Kaderspieler), also bleibt
    # is_starter ehrlich False statt Verletzte als Starter zu markieren.
    raw = {
        "i": "12345",
        "fn": "Florian",
        "n": "Wirtz",
        "pos": 3,
        "mv": 20_800_000,
        "st": 2,
    }
    sp = SquadPlayer.from_api(raw)
    assert sp.player.id == "12345"
    assert sp.fitness_status == 2
    assert sp.is_starter is False


def test_player_parses_member_lineup_prob():
    # Echtes Squad-Item-Fragment (Member-Abo, Probe 23.08.26): `prob` ist die
    # Kickbase-Startelf-Stufe (Hypothese: 1 sicher … 5 Bank).
    raw = {
        "i": "1954",
        "n": "Bernardo",
        "pos": 2,
        "mv": 7_811_721,
        "mvt": 2,
        "tid": "14",
        "st": 2,
        "stl": [2],
        "prob": 3,
        "lst": 1,
        "mdst": 0,
        "pim": "content/file/2e01b620f1354d0f9383cc0003c49d97.png",
    }
    sp = SquadPlayer.from_api(raw)
    assert sp.player.lineup_prob == 3
    assert sp.fitness_status == 2


def test_player_lineup_prob_none_without_member_field():
    # Ohne Member-Abo fehlt `prob` komplett — None, nie 0 (0 wäre eine
    # erfundene Stufe).
    raw = {"i": "1", "n": "X", "pos": 1, "mv": 1}
    assert Player.from_api(raw).lineup_prob is None


def test_player_fitness_is_nullable_when_api_omits_status():
    raw = {"i": "1", "n": "X", "pos": 1, "mv": 1}
    assert Player.from_api(raw).fitness_status is None
    raw["st"] = 0
    assert Player.from_api(raw).fitness_status == 0
    raw["st"] = ""
    assert Player.from_api(raw).fitness_status is None
    raw["st"] = 0.5
    assert Player.from_api(raw).fitness_status is None


def test_player_lineup_prob_requires_an_integral_status():
    raw = {"i": "1", "n": "X", "pos": 1, "mv": 1}
    raw["prob"] = 1.9
    assert Player.from_api(raw).lineup_prob == 0
    raw["prob"] = True
    assert Player.from_api(raw).lineup_prob == 0
    raw["prob"] = "4"
    assert Player.from_api(raw).lineup_prob == 4


def test_market_player_parses_manager_seller_id():
    """Manager-Listings tragen ein `u`-Objekt (Verkäufer, live geprobt 20.08.);
    ihr `dt` ist der Listing-ZEITPUNKT, kein Ablauf."""
    raw = {
        "i": "77",
        "fn": "Jae-sung",
        "n": "Lee",
        "pos": 3,
        "mv": 11_000_000,
        "tid": "5",
        "prc": 11_500_000,
        "dt": "2026-08-16T09:00:00Z",
        "u": {"i": "1842317", "n": "Moritz W."},
    }
    mp = MarketPlayer.from_api(raw)
    assert mp.seller_id == "1842317"


def test_market_player_kickbase_listing_has_empty_seller():
    raw = {"i": "9", "n": "Woltemade", "pos": 4, "mv": 12_000_000,
           "tid": "10", "prc": 12_500_000, "exs": 3600}
    assert MarketPlayer.from_api(raw).seller_id == ""


def test_zombie_listing_detection():
    """Zombie = Manager-Listing älter als 48 h (HTTP 500 auf jedes Gebot,
    Live-Probe 23.08.); frische Manager-Listings und Kickbase-Listings nie."""
    from datetime import timedelta

    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    base = {"i": "1", "n": "X", "pos": 2, "mv": 1_000_000, "tid": "2", "prc": 900_000}

    old_manager = MarketPlayer.from_api(
        {**base, "dt": "2026-08-16T09:00:00Z", "u": {"i": "5", "n": "Moritz W."}}
    )
    fresh_manager = MarketPlayer.from_api(
        {**base, "dt": "2026-08-23T18:00:00Z", "u": {"i": "5", "n": "Moritz W."}}
    )
    kickbase = MarketPlayer.from_api({**base, "exs": 7200})
    rolled_kickbase = MarketPlayer.from_api({**base, "dt": "2026-08-10T09:00:00Z"})

    assert old_manager.is_zombie(now) is True
    assert fresh_manager.is_zombie(now) is False
    assert kickbase.is_zombie(now + timedelta(hours=100)) is False  # kein Verkäufer
    # Kickbase rollt Listings: dt in der Vergangenheit OHNE u-Objekt ist kein Zombie.
    assert rolled_kickbase.is_zombie(now) is False
