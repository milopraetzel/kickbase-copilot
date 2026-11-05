"""Kader-Fenster im Verkaufs-Preflight (02.09.2026, Emils Auftrag „Verlierer verkaufen").

Neue Datei neben der versiegelten tests/agent/test_executor.py (L-087). Die
versiegelte Zusicherung „ein endgültiger Abgang braucht Deckung" bleibt — HIER
steht ihre Präzisierung: Der 11er-Floor gilt ab T-120 vor dem Anpfiff
für autonome Pfade; außerhalb (und für Emils manuelle Wege auch innerhalb)
zählen nur die Positions-Minima 1 TW / 3 ABW / 2 MF / 1 ST.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.config as cfg
from kickbase_agent.agent.executor import ActionExecutor, PlannedAction, PreflightState
from kickbase_agent.db.models import Base
from kickbase_agent.kickbase import endpoints
from kickbase_agent.kickbase.client import KickbaseClient

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


@pytest.fixture
def env(monkeypatch):
    def _set(dry="0", test_league="TEST"):
        monkeypatch.setenv("AGENT_DRY_RUN", dry)
        monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", test_league)
        monkeypatch.setenv("AGENT_ALLOW_LIVE", "0")
        monkeypatch.setenv("AGENT_AUTO_PURGE", "0")
        monkeypatch.setenv("AGENT_AUTO_BONUS", "0")
        monkeypatch.setenv("AGENT_AUTO_LINEUP", "0")
        monkeypatch.setenv("AGENT_APPROVE_LIVE", "0")
        monkeypatch.setenv("AGENT_AUTO_TRADING", "0")
        monkeypatch.setenv("AGENT_AUTO_INTENTS", "0")
        cfg._settings = None

    yield _set
    cfg._settings = None


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def sell_to_kickbase(self, league_id, player_id):
        self.calls.append(("sell", league_id, player_id))
        return {"sold": True}


def _stub(state: PreflightState):
    async def cb(action: PlannedAction) -> PreflightState:
        return state

    return cb


def _squad_11() -> dict[str, int]:
    """Elf = Kader: 1 TW, 5 ABW, 4 MF, 1 ST — genau die Lage vom 02.09."""
    squad = {"gk": 1, "s1": 4}
    squad.update({f"d{i}": 2 for i in range(5)})
    squad.update({f"m{i}": 3 for i in range(4)})
    return squad


def _state(deadline: datetime | None, squad: dict[str, int] | None = None) -> PreflightState:
    return PreflightState(
        in_squad=True,
        current_value=9_000_000,
        squad_positions=squad or _squad_11(),
        matchday_deadline=deadline,
    )


def _sell(pid: str, rule: str) -> PlannedAction:
    return PlannedAction(
        action_type="sell", league_id="TEST", player_id=pid, amount=9_000_000, rule_id=rule
    )


@pytest.mark.asyncio
async def test_ausserhalb_des_fensters_darf_autonom_unter_elf(session, env):
    """Deadline in 3 Tagen: der Kader ist Portfolio — Verkauf aus dem 11er geht."""
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(NOW + timedelta(days=3))))
    result = await ex.execute(_sell("d1", "intent-sell"), now=NOW)
    assert result.status == "executed", result.message
    assert client.calls == [("sell", "TEST", "d1")]


@pytest.mark.asyncio
async def test_ohne_bekannte_deadline_gilt_ausserhalb(session, env):
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(None)))
    result = await ex.execute(_sell("d1", "intent-sell"), now=NOW)
    assert result.status == "executed", result.message


@pytest.mark.asyncio
async def test_positions_minimum_haelt_auch_ausserhalb(session, env):
    """Der einzige Stürmer fällt nie — keine Formation wäre mehr auffüllbar."""
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(NOW + timedelta(days=3))))
    result = await ex.execute(_sell("s1", "intent-sell"), now=NOW)
    assert result.status == "refused"
    assert "Positions-Minimum" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_im_fenster_haelt_der_autonome_pfad_den_elfer_floor(session, env):
    """T-90min, autonom: 11 → 10 wäre ein leerer Platz am Spieltag — refused."""
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(NOW + timedelta(minutes=90))))
    result = await ex.execute(_sell("d1", "intent-sell"), now=NOW)
    assert result.status == "refused"
    assert "Kader-Deckung" in result.message and "T-120" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_t20h_darf_autonom_den_verlierer_nach_ersatzkauf_abgeben(session, env):
    """Vor T-120 ist der Kader Portfolio; Positionsminima bleiben Pflicht."""
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(NOW + timedelta(hours=20))))
    result = await ex.execute(_sell("d1", "intent-sell"), now=NOW)
    assert result.status == "executed", result.message
    assert client.calls == [("sell", "TEST", "d1")]


@pytest.mark.asyncio
async def test_im_fenster_darf_emils_manueller_weg_tauschen(session, env):
    """T-20h, mcp-*: Emils Wille — das -100-Risiko je leerem Platz ist sein Entscheid."""
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(NOW + timedelta(hours=20))))
    result = await ex.execute(_sell("d1", "mcp-sell"), now=NOW)
    assert result.status == "executed", result.message
    assert client.calls == [("sell", "TEST", "d1")]


@pytest.mark.asyncio
async def test_manuelle_remote_vorhaben_gelten_als_emils_weg(session, env):
    """Zwei-DB-Fix: intent-sell-manual trägt das Intent-Präfix, ist aber manuell."""
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(NOW + timedelta(hours=20))))
    result = await ex.execute(_sell("d1", "intent-sell-manual"), now=NOW)
    assert result.status == "executed", result.message


@pytest.mark.asyncio
async def test_auch_manuell_faellt_der_letzte_stuermer_nicht(session, env):
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(NOW + timedelta(hours=20))))
    result = await ex.execute(_sell("s1", "mcp-sell"), now=NOW)
    assert result.status == "refused"
    assert "Positions-Minimum" in result.message


@pytest.mark.asyncio
async def test_im_fenster_bleibt_mit_zwoelf_der_alte_weg_offen(session, env):
    """12er-Kader im Fenster: nach dem Verkauf 11 + Formation — autonom erlaubt."""
    env()
    squad = _squad_11()
    squad["d9"] = 2
    client = _Client()
    ex = ActionExecutor(
        client, session, preflight=_stub(_state(NOW + timedelta(hours=20), squad))
    )
    result = await ex.execute(_sell("d1", "intent-sell"), now=NOW)
    assert result.status == "executed", result.message


# --- Produktive Verdrahtung (Befund 02.10.2026) ------------------------------------------
#
# Alle Tests oben setzen den PreflightState von Hand (`_stub`) — sie prüfen die Regel, nicht,
# ob der Betrieb sie je zu sehen bekommt. Hier läuft der echte `default_preflight` (Executor
# ohne `preflight=`, wie im Worker) gegen einen Client, der nur Rohantworten im Kickbase-
# Format liefert; geparst wird mit den echten Methoden von KickbaseClient. Formate: Kader wie
# tests/fixtures/kickbase_squad_response.json (plus `lo`, live verifiziert, siehe
# SquadPlayer.lineup_order), Spielplan wie tests/kickbase/test_fixtures.py.

#: Erster Anpfiff Spieltag 5: Fr 09.10.2026 20:30 Berlin.
KICKOFF = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)
MATCHDAYS = endpoints.competition_matchdays(endpoints.BUNDESLIGA_COMPETITION_ID)


def _raw_player(pid: str, pos: int, lo: int | None = None) -> dict:
    raw = {"i": pid, "fn": "T", "n": pid, "pos": pos, "mv": 9_000_000, "st": 0, "tid": "2"}
    if lo is not None:
        raw["lo"] = lo
    return raw


def _raw_squad_11() -> list[dict]:
    """Elf Spieler, zehn aufgestellt (Teil-Elf, L-109) — m3 sitzt auf der Bank, der
    Starter-Schutz greift für ihn also nicht."""
    rows = [_raw_player("gk", 1, 0)]
    rows += [_raw_player(f"d{i}", 2, 1 + i) for i in range(4)]
    rows += [_raw_player(f"m{i}", 3, 5 + i) for i in range(3)] + [_raw_player("m3", 3)]
    rows += [_raw_player(f"s{i}", 4, 8 + i) for i in range(2)]
    return rows


class _RawClient:
    """Liefert nur Rohantworten; get_squad/get_matchdays sind die echten Client-Parser."""

    get_squad = KickbaseClient.get_squad
    get_matchdays = KickbaseClient.get_matchdays

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.responses: dict[str, dict] = {
            endpoints.squad("TEST"): {"it": _raw_squad_11()},
            MATCHDAYS: {"it": [{"day": 5, "it": [
                {"day": 5, "dt": "2026-10-09T18:30:00Z", "t1": "2", "t2": "9", "st": 0},
                {"day": 5, "dt": "2026-10-10T13:30:00Z", "t1": "77", "t2": "10", "st": 0},
            ]}]},
        }

    async def _get(self, path: str) -> dict:
        return self.responses[path]

    async def sell_to_kickbase(self, league_id, player_id):
        self.calls.append(("sell", league_id, player_id))
        return {"sold": True}


@pytest.mark.asyncio
async def test_produktiver_preflight_haelt_den_elfer_floor_eine_stunde_vor_anpfiff(session, env):
    """Befund 02.10.2026: Der Verkaufszweig von default_preflight lud die Spieltags-Frist nie
    (nur der Gebotszweig) — im Betrieb war das T-120-Fenster deshalb immer zu, und autonome
    Verkäufe hielten seit 02.09. nur die Positions-Minima. Elf Spieler, 60 min vor dem ersten
    Anpfiff, ein Bankspieler: Danach wären es zehn — verweigert."""
    env()
    client = _RawClient()
    ex = ActionExecutor(client, session)  # ohne preflight= → default_preflight wie im Betrieb
    result = await ex.execute(_sell("m3", "intent-sell"), now=KICKOFF - timedelta(minutes=60))
    assert result.status == "refused", result.message
    assert "Kader-Deckung" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_produktiver_preflight_drei_tage_vorher_gilt_das_positions_minimum(session, env):
    """Gegenprobe: Derselbe Verkauf drei Tage vor dem Anpfiff geht durch (Kader-Fenster 02.09.:
    vor T-120 ist der Kader Portfolio)."""
    env()
    client = _RawClient()
    ex = ActionExecutor(client, session)
    result = await ex.execute(_sell("m3", "intent-sell"), now=KICKOFF - timedelta(days=3))
    assert result.status == "executed", result.message
    assert client.calls == [("sell", "TEST", "m3")]


@pytest.mark.asyncio
@pytest.mark.parametrize("rule", ["kickoff-purge", "mcp-sell", "intent-sell-manual"])
async def test_notbremse_und_emils_wege_verkaufen_auch_ohne_lesbaren_spielplan(
    session, env, rule
):
    """Die Anpfiff-Notbremse (kickoff-*) und Emils manuelle Wege prüfen das Fenster nicht —
    ein gescheiterter Spielplan-Abruf darf ihren Verkauf nie blockieren (Notbremse: lieber
    zehn Spieler mit Punkten als elf mit null). Der Fix lädt die Frist nur für autonome
    Abgänge (Emils GO 02.10.2026)."""
    env()
    client = _RawClient()
    del client.responses[MATCHDAYS]  # Spielplan nicht lesbar
    ex = ActionExecutor(client, session)
    result = await ex.execute(_sell("m3", rule), now=KICKOFF - timedelta(minutes=15))
    assert result.status == "executed", result.message
    assert client.calls == [("sell", "TEST", "m3")]


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule", ["unlesbar", "leer"])
async def test_autonom_ohne_spielplan_kein_verkauf(session, env, schedule):
    """Fail-closed wie im Gebotszweig: Ohne bestimmbare Frist verkauft kein autonomer Pfad —
    auch nicht drei Tage vorher (ein leerer Spielplan ist ein Datenfehler, keine Pause)."""
    env()
    client = _RawClient()
    if schedule == "unlesbar":
        del client.responses[MATCHDAYS]
    else:
        client.responses[MATCHDAYS] = {"it": []}
    ex = ActionExecutor(client, session)
    result = await ex.execute(_sell("m3", "intent-sell"), now=KICKOFF - timedelta(days=3))
    assert result.status == "refused", result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_die_frist_gilt_zur_ausfuehrungszeit_nicht_zur_systemuhr(session, env, monkeypatch):
    """Wie im Gebotszweig (test_preflight_clock.py): Der Preflight bestimmt die Frist zum
    Zeitpunkt der Ausführung — eine abweichende Systemuhr darf das Fenster nicht verschieben."""
    import kickbase_agent.agent.executor as executor_module

    class SpaeteUhr(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2035, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(executor_module, "datetime", SpaeteUhr)
    env()
    client = _RawClient()
    ex = ActionExecutor(client, session)
    result = await ex.execute(_sell("m3", "intent-sell"), now=KICKOFF - timedelta(minutes=60))
    assert result.status == "refused", result.message
    assert ex._last_preflight.matchday_deadline == KICKOFF
