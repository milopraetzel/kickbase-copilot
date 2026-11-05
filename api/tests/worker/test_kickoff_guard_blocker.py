"""Notbremsen-Alarme müssen den Discord-Digest durchbrechen (Review-Fund 17.09.2026).

Eigene, unversiegelte Datei: `tests/worker/test_kickoff_guard.py` ist über
`guardrails.lock` versiegelt und fälscht den Kanal nur mit `send_info` — deshalb
fällt `_blocker` dort auf `send_info` zurück und die versiegelten Texte bleiben
identisch. Hier wird der echte Pfad geprüft: hat der Kanal `send_blocker`, wird
er benutzt.
"""

import pytest

from kickbase_agent.worker import kickoff_guard as kg


class _DigestChannel:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.blockers: list[str] = []

    async def send_info(self, text: str, **_: object) -> None:
        self.infos.append(text)

    async def send_blocker(self, text: str) -> None:
        self.blockers.append(text)


class _LegacyChannel:
    def __init__(self) -> None:
        self.infos: list[str] = []

    async def send_info(self, text: str, **_: object) -> None:
        self.infos.append(text)


@pytest.mark.asyncio
async def test_blocker_prefers_send_blocker_when_available():
    ch = _DigestChannel()
    await kg._blocker(ch, "NOTBREMSE FEHLGESCHLAGEN — sofort handeln")
    assert ch.blockers == ["NOTBREMSE FEHLGESCHLAGEN — sofort handeln"]
    assert ch.infos == []


@pytest.mark.asyncio
async def test_blocker_falls_back_to_send_info_with_unchanged_text():
    ch = _LegacyChannel()
    await kg._blocker(ch, "T-60: Kontostand UNBEKANNT")
    assert ch.infos == ["T-60: Kontostand UNBEKANNT"]


def test_every_alarm_site_uses_the_blocker_helper():
    """Jede Alarmzeile (🚨 / ⚠️ T-120) läuft über _blocker, keine über send_info."""
    import inspect

    src = inspect.getsource(kg)
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "await channel.send_info(" in line:
            following = " ".join(lines[i + 1:i + 3])
            assert "🚨" not in following and "⚠️ T-120" not in following, (
                f"Alarm ohne _blocker in Zeile {i + 1}: {following.strip()[:80]}"
            )
    assert src.count("await _blocker(channel,") >= 10
