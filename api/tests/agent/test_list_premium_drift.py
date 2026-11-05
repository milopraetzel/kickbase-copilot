"""Listungen über Marktwert sind ein Preis, keine Drift (16.09.2026).

Eigene, unversiegelte Datei: `tests/agent/test_executor.py` ist über
`guardrails.lock` versiegelt. Der dortige Fall (Listung 5,0 M bei MW 6,5 M →
refused, Unterverkauf nach Kursanstieg) bleibt unverändert und wird hier als
Gegenprobe mitgeführt.

Anlass: Olise sollte auf Emils Anweisung zu 100 M gelistet werden (MW 65,09 M).
Der symmetrische 20-%-Drift-Check verweigerte das als „Zustand hat sich
geändert" (Audit 199) — obwohl sich nichts geändert hatte außer Emils Preis.
"""

import pytest

from kickbase_agent.agent.executor import ActionExecutor

from .test_executor import NOW, FakeClient, _list, _squad_member
from .test_executor import (
    env as env,
)
from .test_executor import (
    session as session,
)


@pytest.mark.asyncio
async def test_listing_above_market_value_is_a_price_not_a_drift(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.squad = [_squad_member("99", 65_089_670)]  # Olise-Fall: 154 % MW

    result = await ActionExecutor(client, session).execute(
        _list(amount=100_000_000), now=NOW
    )

    assert result.status == "executed", result.message
    assert client.calls == [("list", "TEST", "99", 100_000_000)]


@pytest.mark.asyncio
async def test_listing_below_market_value_still_trips_the_drift_check(session, env):
    """Gegenprobe zum versiegelten Fall: Preis 5,0 M, MW inzwischen 6,5 M →
    wir würden unter Wert verkaufen → refused bleibt."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.squad = [_squad_member("99", 6_500_000)]

    result = await ActionExecutor(client, session).execute(
        _list(amount=5_000_000), now=NOW
    )

    assert result.status == "refused"
    assert "20 %" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_listing_exactly_at_market_value_passes(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.squad = [_squad_member("99", 5_000_000)]

    result = await ActionExecutor(client, session).execute(
        _list(amount=5_000_000), now=NOW
    )

    assert result.status == "executed", result.message
