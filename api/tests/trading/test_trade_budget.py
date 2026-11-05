"""200er-Regel: Feed-Zählung, Saturations-Erkennung und konservative Sperre.

Review-Fund: Der Feed-Abruf lädt maximal FEED_MAX_ITEMS Einträge. Kommt der Feed
VOLL zurück, ist er abgeschnitten — `used` ist dann nur eine Untergrenze und die
195er-Sperre wäre sonst nie mehr verifizierbar. Ein Paginierungs-Parameter ist
für den Endpoint nicht belegt (kein Live-Call in Tests → nicht erfunden).
"""

from types import SimpleNamespace

from kickbase_agent.trading.trade_budget import (
    FEED_MAX_ITEMS,
    TradeUsage,
    budget_line,
    count_my_trades,
    get_trade_usage,
    trades_blocked,
)


def _own_trade(name: str = "emil") -> dict:
    return {"t": 15, "data": {"byr": name, "pn": "X", "trp": 1}}


def _foreign_item() -> dict:
    return {"t": 3, "data": {"ln": "Listing"}}


class _FakeClient:
    def __init__(self, feed: list[dict]):
        self.feed = feed
        self.requested_max: int | None = None

    async def get_ranking(self, league_id):
        return [SimpleNamespace(user_id="u1", name="emil")]

    async def get_activity_feed(self, league_id, max_items=30):
        self.requested_max = max_items
        return self.feed[:max_items]


async def test_unsaturated_feed_counts_exactly():
    client = _FakeClient([_own_trade(), _own_trade(), _foreign_item()])
    usage = await get_trade_usage(client, "L1", "u1")
    assert usage == TradeUsage(
        used=2, my_name="emil", count_saturated=False, buys=2, sells=0
    )
    assert client.requested_max == FEED_MAX_ITEMS


async def test_saturated_feed_flags_lower_bound():
    """Feed kommt voll zurück → Zählung ist Untergrenze, Flag gesetzt."""
    feed = [_own_trade(), _own_trade(), _own_trade()] + [
        _foreign_item() for _ in range(FEED_MAX_ITEMS - 3)
    ]
    client = _FakeClient(feed)
    usage = await get_trade_usage(client, "L1", "u1")
    assert usage.used == 3
    assert usage.count_saturated is True


async def test_trade_usage_supports_legacy_tuple_unpacking():
    """Alt-Aufrufer (cli/research.py) entpacken weiter `used, name = ...`."""
    client = _FakeClient([_own_trade()])
    used, name = await get_trade_usage(client, "L1", "u1")
    assert (used, name) == (1, "emil")


def test_trades_blocked_conservative_when_saturated():
    """Saturiert = Sperre aktiv, egal wie klein die Untergrenze ist: die
    195er-Grenze lässt sich mit abgeschnittenem Feed nicht mehr ausschließen."""
    assert trades_blocked(3, 200) is False
    assert trades_blocked(195, 200) is True          # Notreserve 5 (unverändert)
    assert trades_blocked(3, 200, count_saturated=True) is True
    assert trades_blocked(0, 200, count_saturated=True) is True


def test_budget_line_never_shows_silent_count_when_saturated():
    """NIE stilles „3/200" bei vollem Feed: Untergrenzen-Marker + rote Ampel."""
    line = budget_line(3, 200, count_saturated=True)
    assert "≥3/200" in line
    assert "🔴" in line
    assert "Untergrenze" in line
    # Ohne Saturation bleibt das alte Format erhalten.
    assert "🟢 Trade-Budget: 3/200" in budget_line(3, 200)


def test_count_my_trades_matches_buyer_and_seller():
    feed = [
        {"t": 15, "data": {"byr": "emil", "pn": "A", "trp": 1}},
        {"t": 15, "data": {"slr": "emil", "pn": "B", "trp": 1}},
        {"t": 15, "data": {"byr": "Rivale", "pn": "C", "trp": 1}},
        _foreign_item(),
    ]
    assert count_my_trades(feed, "emil") == 2


# --- Emils Fund 25.08.: die Summe allein verschweigt, wofür Trades draufgingen ---


def test_split_separates_buys_from_sells():
    """Vier Aufräum-Verkäufe und ein Kauf sind fünf Trades — aber sehr
    verschiedene fünf."""
    from kickbase_agent.trading.trade_budget import split_my_trades

    feed = [
        {"t": 15, "data": {"byr": "emil", "pn": "Olise"}},
        {"t": 15, "data": {"slr": "emil", "pn": "Scally"}},
        {"t": 15, "data": {"slr": "emil", "pn": "Ndiaye"}},
        {"t": 15, "data": {"byr": "Leon", "pn": "Fremd"}},
        {"t": 3, "data": {"slr": "emil"}},          # kein Transfer-Ereignis
    ]
    assert split_my_trades(feed, "emil") == (1, 2)


def test_split_matches_total_count():
    """Die Aufteilung darf nie von der Summe abweichen — sonst lügt die Ampel."""
    from kickbase_agent.trading.trade_budget import count_my_trades, split_my_trades

    feed = [{"t": 15, "data": {"byr": "emil"}}] * 3 + [{"t": 15, "data": {"slr": "emil"}}] * 7
    buys, sells = split_my_trades(feed, "emil")
    assert buys + sells == count_my_trades(feed, "emil") == 10
