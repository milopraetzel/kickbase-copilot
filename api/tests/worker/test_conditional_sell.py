"""Tausch-Bedingung für Sofortverkaufs-Vorhaben (02.09.2026, Emils Auftrag).

Neue Datei (L-087). `squad_condition_unmet` ist die reine Warte-Logik: ein
Verkauf mit sources.after_squad_size wartet, bis der Kader die Größe hat —
„erst Ersatz, dann Abgang", befristet durch die Deadline des Vorhabens.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kickbase_agent.worker.intents import (
    CONDITION_RETRY_DELAY,
    STATUS_FAILED,
    STATUS_PLANNED,
    squad_condition_unmet,
)

_NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


class _Client:
    def __init__(self, size: int | None = 11, *, raise_exc: bool = False) -> None:
        self.size = size
        self.raise_exc = raise_exc

    async def get_squad(self, league_id):
        if self.raise_exc:
            raise RuntimeError("API weg")
        return [SimpleNamespace(id=str(i)) for i in range(self.size or 0)]


def _executor(client) -> SimpleNamespace:
    return SimpleNamespace(_client=client)


def _intent(kind="sell", need=12, deadline=_NOW + timedelta(days=2)) -> SimpleNamespace:
    sources = {"after_squad_size": need} if need is not None else {}
    return SimpleNamespace(kind=kind, sources=sources, deadline=deadline)


@pytest.mark.asyncio
async def test_ohne_bedingung_und_fuer_gebote_keine_wartezeit() -> None:
    ex = _executor(_Client(9))
    assert await squad_condition_unmet(_intent(need=None), ex, "L1", now=_NOW) is None
    assert await squad_condition_unmet(_intent(kind="bid"), ex, "L1", now=_NOW) is None


@pytest.mark.asyncio
async def test_bedingung_erfuellt_fuehrt_aus() -> None:
    ex = _executor(_Client(12))
    assert await squad_condition_unmet(_intent(need=12), ex, "L1", now=_NOW) is None


@pytest.mark.asyncio
async def test_bedingung_offen_wartet_mit_naechster_pruefung() -> None:
    res = await squad_condition_unmet(_intent(need=12), _executor(_Client(11)), "L1", now=_NOW)
    assert res is not None
    status, text = res
    assert status == STATUS_PLANNED
    assert "Kader 11 < 12" in text and "warte auf Ersatz" in text


@pytest.mark.asyncio
async def test_kurz_vor_deadline_wird_ehrlich_verworfen() -> None:
    bald = _NOW + CONDITION_RETRY_DELAY  # nächste Prüfung läge hinter deadline − 15 min
    it = _intent(need=12, deadline=bald)
    res = await squad_condition_unmet(it, _executor(_Client(10)), "L1", now=_NOW)
    assert res is not None and res[0] == STATUS_FAILED
    assert "Ersatz kam nicht" in res[1]


@pytest.mark.asyncio
async def test_ohne_deadline_nicht_befristbar() -> None:
    it = _intent(need=12, deadline=None)
    res = await squad_condition_unmet(it, _executor(_Client(11)), "L1", now=_NOW)
    assert res is not None and res[0] == STATUS_FAILED


@pytest.mark.asyncio
async def test_lesefehler_heisst_warten_nie_verkaufen() -> None:
    res = await squad_condition_unmet(
        _intent(need=12), _executor(_Client(raise_exc=True)), "L1", now=_NOW
    )
    assert res is not None and res[0] == STATUS_PLANNED
    assert "nicht lesbar" in res[1]


@pytest.mark.asyncio
async def test_unlesbare_bedingung_wird_verworfen() -> None:
    it = _intent(need="zwoelf")
    res = await squad_condition_unmet(it, _executor(_Client(12)), "L1", now=_NOW)
    assert res is not None and res[0] == STATUS_FAILED
