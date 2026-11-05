"""Lesefehler im Preflight der Schreibstelle (Befund 02.10.2026, Codex-Review von dd11bed).

Eigene Datei neben tests/worker/test_momentum_pipeline.py (pbaa-Retry, L-087). Anders als dort
läuft hier die ECHTE Schreibstelle (ActionExecutor mit default_preflight): Ein Client, dessen
Abrufe wie im Netz scheitern (httpx-Fehler), liefert dem Intent-Executor genau die Meldung, die
er im Betrieb sieht. Geparst wird mit den echten Methoden von KickbaseClient; die Rohantworten
haben das Format von tests/fixtures/kickbase_squad_response.json (plus `lo`, live verifiziert)
und tests/kickbase/test_fixtures.py (Spielplan).
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.config as cfg
from kickbase_agent.agent.executor import ActionExecutor
from kickbase_agent.db.models import Base, Intent
from kickbase_agent.kickbase import endpoints
from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.worker.intents import PBAA_MAX_RETRIES, blocked_keys, execute_intent

#: Mo 05.10.2026 18:30 Berlin — Spieltag 5 beginnt Fr 09.10. 20:30 (weit vor T-120).
NOW = datetime(2026, 10, 5, 16, 30, tzinfo=UTC)
H = timedelta(hours=1)
MATCHDAYS = endpoints.competition_matchdays(endpoints.BUNDESLIGA_COMPETITION_ID)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


@pytest.fixture
def env(monkeypatch):
    """Wie tests/agent/test_kader_fenster.py: live nur in der Testliga, alle Pfad-Flags aus."""
    monkeypatch.setenv("AGENT_DRY_RUN", "0")
    monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", "TEST")
    for flag in ("AGENT_ALLOW_LIVE", "AGENT_AUTO_PURGE", "AGENT_AUTO_BONUS", "AGENT_AUTO_LINEUP",
                 "AGENT_APPROVE_LIVE", "AGENT_AUTO_TRADING", "AGENT_AUTO_INTENTS"):
        monkeypatch.setenv(flag, "0")
    cfg._settings = None
    yield
    cfg._settings = None


def _raw_player(pid: str, pos: int, lo: int | None = None) -> dict:
    raw = {"i": pid, "fn": "T", "n": pid, "pos": pos, "mv": 9_000_000, "st": 0, "tid": "2"}
    if lo is not None:
        raw["lo"] = lo
    return raw


def _responses() -> dict[str, dict]:
    """Elf Spieler (m3 auf der Bank) und der Spielplan von Spieltag 5."""
    squad = [_raw_player("gk", 1, 0)]
    squad += [_raw_player(f"d{i}", 2, 1 + i) for i in range(4)]
    squad += [_raw_player(f"m{i}", 3, 5 + i) for i in range(3)] + [_raw_player("m3", 3)]
    squad += [_raw_player(f"s{i}", 4, 8 + i) for i in range(2)]
    return {
        endpoints.squad("TEST"): {"it": squad},
        MATCHDAYS: {"it": [{"day": 5, "it": [
            {"day": 5, "dt": "2026-10-09T18:30:00Z", "t1": "2", "t2": "9", "st": 0},
        ]}]},
    }


class _FlakyClient:
    """Die ersten `failures` Abrufe scheitern mit `error`, danach antwortet Kickbase normal."""

    get_squad = KickbaseClient.get_squad
    get_matchdays = KickbaseClient.get_matchdays

    def __init__(self, error: Exception | None = None, *, failures: int = 99) -> None:
        self.error = error
        self.failures = failures if error is not None else 0
        self.responses = _responses()
        self.writes: list[tuple] = []

    async def _get(self, path: str) -> dict:
        if self.failures > 0:
            self.failures -= 1
            assert self.error is not None
            raise self.error
        return self.responses[path]

    async def sell_to_kickbase(self, league_id, player_id):
        self.writes.append(("sell", league_id, player_id))
        return {"sold": True}


def _sell_intent(*, deadline: datetime | None = NOW + 48 * H) -> Intent:
    """Sofortverkauf eines Bankspielers, fällig jetzt — wie der Pflichtverkauf am Termin."""
    return Intent(
        created_at=NOW - H, updated_at=NOW - H, kind="sell", player_id="m3",
        player_name="Spieler m3", team_id="2", position=3, amount=9_000_000,
        rationale="Verkaufstermin", sources={"league_id": "TEST"}, listing_type="kickbase",
        deadline=deadline, execute_at=NOW - timedelta(minutes=1), status="planned",
    )


async def _run(session, client, row, *, now=NOW) -> str:
    return await execute_intent(session, row, ActionExecutor(client, session), "TEST", now=now)


def _at(stamp: datetime) -> datetime:
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _status_error(code: int) -> httpx.HTTPStatusError:
    """Genau der Fehler, den KickbaseClient._request über raise_for_status() wirft."""
    request = httpx.Request("GET", "https://api.kickbase.com" + endpoints.squad("TEST"))
    try:
        httpx.Response(code, request=request).raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError(f"HTTP {code} ist kein Fehler")


# ------------------------------------------------------------------ Wiederholung


@pytest.mark.asyncio
async def test_bleibt_kickbase_weg_endet_das_vorhaben_nach_drei_wiederholungen(session, env):
    """Bis dd11bed beerdigte schon der ERSTE Lesefehler das Vorhaben: failed, und blocked_keys
    sperrte (sell, Spieler) bis Tagesende — auch wenn Kickbase fünf Minuten später wieder
    antwortete. Jetzt: drei Wiederholungen im Abstand von 10 min, erst dann ehrlich failed."""
    row = _sell_intent()
    session.add(row)
    session.commit()
    client = _FlakyClient(httpx.ReadTimeout("timed out"))  # Kickbase bleibt weg

    outcomes = [await _run(session, client, row, now=NOW + i * timedelta(minutes=10))
                for i in range(4)]

    assert outcomes == ["planned", "planned", "planned", "failed"]
    assert "Preflight fehlgeschlagen (ReadTimeout" in (row.result or "")
    assert ("sell", "m3") in blocked_keys(session, NOW + timedelta(minutes=35))
    assert client.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    httpx.ReadTimeout("timed out"),
    httpx.ConnectError("connection refused"),
    _status_error(503),
    _status_error(429),
])
async def test_ein_lesefehler_im_preflight_wird_nach_zehn_minuten_wiederholt(
    session, env, error
):
    row = _sell_intent()
    session.add(row)
    session.commit()

    assert await _run(session, _FlakyClient(error, failures=1), row) == "planned"
    assert _at(row.execute_at) == NOW + timedelta(minutes=10)
    assert row.pbaa_retries == 1
    assert "Lesefehler" in (row.result or "")
    assert ("sell", "m3") not in blocked_keys(session, NOW + timedelta(minutes=5))


@pytest.mark.asyncio
async def test_der_zweite_versuch_verkauft_wenn_kickbase_wieder_antwortet(session, env):
    """Der abgelehnte Versuch blockiert die Idempotenz nicht (refused wird recycelt)."""
    row = _sell_intent()
    session.add(row)
    session.commit()
    client = _FlakyClient(httpx.ReadTimeout("timed out"), failures=1)

    assert await _run(session, client, row) == "planned"
    assert await _run(session, client, row, now=NOW + timedelta(minutes=10)) == "executed"
    assert client.writes == [("sell", "TEST", "m3")]


@pytest.mark.asyncio
async def test_pbaa_luecke_und_lesefehler_teilen_sich_drei_versuche(session, env):
    """Gemeinsamer Zähler (Emils GO, keine Migration): Wer schon drei Versuche wegen der
    pbaa-Lücke hatte, bekommt für einen Lesefehler keinen vierten."""
    row = _sell_intent()
    row.pbaa_retries = PBAA_MAX_RETRIES
    session.add(row)
    session.commit()

    assert await _run(session, _FlakyClient(httpx.ReadTimeout("timed out")), row) == "failed"


@pytest.mark.asyncio
async def test_kein_versuch_nach_der_frist_des_vorhabens(session, env):
    """Frist in 12 min: Ein Versuch in 10 min läge hinter Frist − 15 min."""
    row = _sell_intent(deadline=NOW + timedelta(minutes=12))
    session.add(row)
    session.commit()

    assert await _run(session, _FlakyClient(httpx.ReadTimeout("timed out")), row) == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    _status_error(404),        # Kickbase kennt den Pfad nicht — kein Netzfehler
    _status_error(400),
    ValueError("Spielplan leer — Deadline-Fenster nicht bestimmbar (fail-closed)"),
    KeyError("it"),            # unerwartetes Antwortformat
])
async def test_inhaltliche_oder_unerwartete_fehler_bleiben_failed(session, env, error):
    """Nur Netz-/Lesefehler sind vorübergehend. Inhaltliche Verweigerungen (wie der leere
    Spielplan, den der Preflight bewusst als ValueError meldet), Client-Fehler und unerwartete
    Antworten werden nicht wiederholt."""
    row = _sell_intent()
    session.add(row)
    session.commit()

    assert await _run(session, _FlakyClient(error, failures=1), row) == "failed"
    assert row.pbaa_retries == 0


@pytest.mark.asyncio
async def test_ein_leerer_spielplan_ueber_den_echten_parser_bleibt_failed(session, env):
    """Kickbase antwortet, aber ohne Spiele: Der Preflight verweigert inhaltlich (fail-closed,
    dd11bed) — das ist kein Lesefehler und wird nicht wiederholt."""
    row = _sell_intent()
    session.add(row)
    session.commit()
    client = _FlakyClient()
    client.responses[MATCHDAYS] = {"it": []}

    assert await _run(session, client, row) == "failed"
    assert "Spielplan leer" in (row.result or "")
    assert row.pbaa_retries == 0
