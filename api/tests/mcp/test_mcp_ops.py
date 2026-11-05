"""Ops-Tools mit gemocktem Prozess-Runner (run_cmd): ssh-Argumente, Parsing, Sperrfenster."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import CommunityTip, LearningEvidence, Report
from kickbase_agent.mcp import _util, tools_ops
from kickbase_agent.mcp._util import BOX_HOST, CmdResult, deploy_window_blocked
from kickbase_agent.notify import report_store

BERLIN = ZoneInfo("Europe/Berlin")

BOX_PROBE_OUT = """@@status
Up 2 days
@@started
2026-08-21T20:31:05.123456789Z
@@flags
AGENT_ALLOW_LIVE=0
AGENT_AUTO_BONUS=1
AGENT_AUTO_LINEUP=1
AGENT_AUTO_PURGE=1
AGENT_DRY_RUN=0
AGENT_TEST_LEAGUE_ID=7205139
AGENT_TRADING_MAX_PER_PLAYER=1500000
@@jobs
13
@@errors
0
@@bonus
2026-08-23 08:03:12 INFO kickbase_agent.writes: API-WRITE ok type=bonus player=None took=812ms
"""


class Runner:
    """Ersetzt _util.run_cmd: protokolliert argv und liefert vorbereitete Antworten."""

    def __init__(self, *results: CmdResult) -> None:
        self.calls: list[list[str]] = []
        self.results = list(results)

    def __call__(self, argv: list[str], timeout: float) -> CmdResult:
        self.calls.append(list(argv))
        if not self.results:
            return CmdResult(rc=0, out="", err="", duration_s=0.1)
        return self.results.pop(0)

    def remote(self, idx: int) -> str:
        """Das Remote-Kommando des idx-ten ssh-Aufrufs."""
        argv = self.calls[idx]
        assert argv[0] == "ssh" and argv[-2] == BOX_HOST
        assert "BatchMode=yes" in argv  # nie interaktiv hängen
        return argv[-1]


def ok(out: str) -> CmdResult:
    return CmdResult(rc=0, out=out, err="", duration_s=0.3)


def _daytime() -> datetime:
    return datetime(2026, 8, 23, 10, 0, tzinfo=BERLIN)


@pytest.fixture(autouse=True)
def _legacy_delivery_policy(monkeypatch, _env):
    """Diese Versandtests aktivieren explizit Einzelmeldungen; Produktion bleibt still."""
    monkeypatch.setenv("DISCORD_DIGEST_ONLY", "0")


@pytest.fixture
def runner(monkeypatch):
    r = Runner()
    monkeypatch.setattr(_util, "run_cmd", r)
    return r


@pytest.fixture
def git_clean(monkeypatch):
    """Paket 1 (P0-9): Vorbedingung der Deploy-Tests ist ein sauberer main = origin/main.
    Den Riegel selbst prüft tests/mcp/test_deploy_git_gate.py am echten argv."""

    async def gate():
        return {"ok": True, "commit": "c" * 40, "branch": "main",
                "executor_sha256": "e" * 64, "ignored_untracked": []}

    monkeypatch.setattr(tools_ops, "git_deploy_gate", gate)


# ------------------------------------------------------------------ box_status


async def test_box_status_parses_probe_and_shows_flags_only_as_0_1(runner):
    runner.results = [ok(BOX_PROBE_OUT)]
    out = await tools_ops.box_status()
    assert out["status"] == "ok" and out["running"] is True
    assert out["container_status"] == "Up 2 days"
    assert out["jobs_added"] == 13 and out["error_lines"] == 0
    assert out["flags"]["AGENT_AUTO_BONUS"] == 1 and out["flags"]["AGENT_DRY_RUN"] == 0
    # Nicht-boolesche AGENT_-Variablen: nur Präsenz, NIE der Wert (Invariante 2)
    assert out["flags"]["AGENT_TEST_LEAGUE_ID"] == "set"
    assert "7205139" not in str(out) and "1500000" not in str(out)
    assert out["last_bonus_line"].endswith("took=812ms")
    assert out["summary"].startswith("läuft · Jobs 13 · Fehler 0")
    remote = runner.remote(0)
    assert "docker ps --filter name=^/kickbase-agent$" in remote
    assert "grep -c 'Added job'" in remote and "docker exec kickbase-agent env" in remote


async def test_box_status_unreachable_box_is_failed_not_traceback(runner):
    runner.results = [
        CmdResult(rc=255, out="", err="ssh: connect to host: timed out", duration_s=15)
    ]
    out = await tools_ops.box_status()
    assert out["status"] == "failed" and out["step"] == "ssh" and out["rc"] == 255
    assert "timed out" in out["stderr_tail"] and "hint" in out


# ------------------------------------------------------------------ deploy_verify


@pytest.mark.parametrize(
    ("hhmm", "blocked"),
    [((21, 24), False), ((21, 25), True), ((21, 30), True), ((22, 25), True), ((22, 26), False)],
)
def test_deploy_window_boundaries(hhmm, blocked):
    now = datetime(2026, 8, 23, *hhmm, tzinfo=BERLIN)
    assert deploy_window_blocked(now) is blocked


async def test_deploy_verify_refuses_in_block_window_without_any_command(runner, monkeypatch):
    monkeypatch.setattr(_util, "berlin_now", lambda: datetime(2026, 8, 23, 21, 30, tzinfo=BERLIN))
    monkeypatch.setattr(tools_ops, "berlin_now", _util.berlin_now)
    out = await tools_ops.deploy_verify(["neue_funktion"])
    assert out["status"] == "refused" and "21:25" in out["reason"] and "21:30" in out["reason"]
    assert runner.calls == []  # weder rsync noch ssh


async def test_deploy_verify_requires_markers(runner, monkeypatch):
    monkeypatch.setattr(tools_ops, "berlin_now", _daytime)
    out = await tools_ops.deploy_verify([" "])
    assert out["error"].startswith("ToolError: Mindestens ein Code-Marker")
    assert runner.calls == []


async def test_deploy_verify_rsyncs_without_secrets_builds_worker_and_greps_markers(
    runner, monkeypatch, git_clean
):
    monkeypatch.setattr(tools_ops, "berlin_now", _daytime)
    runner.results = [
        ok("sent 1 MB"),  # rsync
        ok("worker Started"),  # compose up
        ok(
            "@@marker0\n       3\n@@marker1\n0\n@@status\nUp 20 seconds\n"
            "@@jobs\n13\n@@errors\n0\n"
        ),
    ]
    out = await tools_ops.deploy_verify(["def league_state", "gibt_es_nicht"], settle_seconds=0)
    rsync = runner.calls[0]
    assert rsync[0] == "rsync" and rsync[-1] == f"{BOX_HOST}:/opt/kickbase-agent/"
    # Der Quellpfad bleibt genau EIN argv-Element; ob sein echter Checkout-Pfad
    # Leerzeichen enthält, ist eine Eigenschaft der Umgebung und kein Vertrag.
    assert rsync[-2].endswith("/api")
    for pat in (".env", ".git", ".venv", "*.db", "*.db-wal"):
        assert f"--exclude={pat}" in rsync
    # --env-file api/.env ist Pflicht (25.08.): speist die Traefik-Middleware
    # kba-writetoken aus derselben Quelle wie den Container — ohne den Flag
    # rendert Compose den Header leer und alle Schreib-Routen enden im 401.
    assert (
        "cd /opt/kickbase-agent && docker compose --env-file api/.env up -d --build worker api"
        == runner.remote(1)
    )
    verify = runner.remote(2)
    assert (
        "docker exec kickbase-agent grep -rlF --include=*.py -- 'def league_state' /app/src"
        in verify
    )
    assert "grep -c 'Added job'" in verify
    assert out["markers"] == {"def league_state": 3, "gibt_es_nicht": 0}
    assert out["all_markers_found"] is False and out["status"] == "check"
    assert out["jobs_added"] == 13 and out["running"] is True and out["rsync_rc"] == 0


async def test_deploy_verify_ok_when_all_markers_found(runner, monkeypatch, git_clean):
    monkeypatch.setattr(tools_ops, "berlin_now", _daytime)
    # Seit dem Review vom 21.09.2026 gehören zum „ok" auch der Marker im API-Container
    # und die Prüfsumme der Schreibstelle (hier die des git_clean-Riegels).
    runner.results = [
        ok(""), ok(""),
        ok(
            "@@marker0\n2\n@@apimarker0\n2\n"
            f"@@executor\n{'e' * 64}  /app/src/kickbase_agent/agent/executor.py\n"
            "@@status\nUp 30 seconds\n@@jobs\n13\n@@errors\n0\n"
        ),
    ]
    out = await tools_ops.deploy_verify(["kba_mcp_marker"], settle_seconds=0)
    assert out["status"] == "ok" and out["hint"] is None and out["all_markers_found"] is True


async def test_deploy_verify_stops_after_failed_rsync(runner, monkeypatch, git_clean):
    monkeypatch.setattr(tools_ops, "berlin_now", _daytime)
    runner.results = [
        CmdResult(rc=12, out="", err="rsync: connection unexpectedly closed", duration_s=2)
    ]
    out = await tools_ops.deploy_verify(["x"], settle_seconds=0)
    assert out["status"] == "failed" and out["step"] == "rsync"
    assert len(runner.calls) == 1  # kein compose, kein verify


# ------------------------------------------------------------------ cockpit_build


async def test_cockpit_build_parses_bytes_from_job_output(runner):
    runner.results = [ok("cockpit_build: 1,234,567 Bytes → /app/data/cockpit/index.html\n")]
    out = await tools_ops.cockpit_build()
    assert out["status"] == "ok" and out["bytes"] == 1_234_567
    assert out["url"] == "https://localhost:8080" and out["finished_at"]
    remote = runner.remote(0)
    assert remote.startswith("docker exec kickbase-agent uv run python -c ")
    assert "run_cockpit_build" in remote


async def test_cockpit_build_disabled_output_is_check(runner):
    runner.results = [ok("cockpit_build: deaktiviert (cockpit_html_path leer)")]
    out = await tools_ops.cockpit_build()
    assert out["status"] == "check" and out["bytes"] is None and "cockpit_html_path" in out["hint"]


# ------------------------------------------------------------------ discord_post


async def test_discord_post_rejects_long_or_empty_text_without_http(monkeypatch, httpx_mock):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")
    import kickbase_agent.config as cfg

    cfg._settings = None
    long = await tools_ops.discord_post("x" * 1901)
    assert long["error"].startswith("ToolError: Nachricht zu lang (1901")
    empty = await tools_ops.discord_post("   ")
    assert empty["error"].startswith("ToolError: Leere Nachricht")
    assert httpx_mock.get_requests() == []


@pytest.fixture
def reports_db(monkeypatch, world):
    """`reports` in der Test-DB hinter report_store.SessionLocal (die Ablage öffnet
    ihre eigene Session, damit ein DB-Fehler den Post nie stört)."""
    monkeypatch.setattr(report_store, "SessionLocal", world.sessions)

    def rows() -> list[Report]:
        with Session(world.engine, future=True) as s:
            return list(s.execute(select(Report).order_by(Report.id)).scalars())

    return rows


def _discord_env(monkeypatch, httpx_mock, message_id="987"):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")
    import kickbase_agent.config as cfg

    cfg._settings = None
    httpx_mock.add_response(
        method="POST",
        url="https://discord.com/api/v10/channels/123/messages",
        json={"id": message_id},
    )


BOX_SAVED = "Bericht #12 (recherche, mcp:discord_post) gespeichert.\n"


async def test_discord_post_sends_rest_message_stores_report_and_mirrors_to_box(
    monkeypatch, httpx_mock, reports_db, runner
):
    _discord_env(monkeypatch, httpx_mock)
    runner.results = [ok(BOX_SAVED)]
    text = "🧭 Lage & Gebote\n-# Zeile mit 'Apostroph' und \"Anführung\""
    out = await tools_ops.discord_post(text)
    assert out == {
        "posted": True, "chars": len(text), "message_id": "987",
        "report_id": 1, "kind": "recherche",  # Default "info" → aus der ersten Zeile geraten
        "box": {"ok": True, "report_id": 12, "output": BOX_SAVED.strip()},
        "hint": None,
    }
    req = httpx_mock.get_requests()[0]
    assert req.headers["Authorization"] == "Bot tok"
    assert b"Lage" in req.content
    assert "tok" not in str(out)  # keine Secrets in der Antwort
    # lokal gespeichert …
    (r,) = reports_db()
    assert (r.kind, r.title, r.body, r.source) == (
        "recherche", "Lage & Gebote", text, "mcp:discord_post"
    )
    assert r.meta == {"delivered": True, "chars": len(text), "message_id": "987"}
    # … und per ssh in die Box-DB gespiegelt (die Web-App liest die Box): kba report-add
    remote = runner.remote(0)
    assert remote.startswith(
        "docker exec kickbase-agent uv run kba report-add --kind recherche "
        "--source mcp:discord_post --meta "
    )
    assert '"delivered": true' in remote and '"local_report_id": 1' in remote
    assert remote.endswith(" -- " + _util.q(text))  # `--`: Body darf mit „-" beginnen
    assert "'\"'\"'" in remote  # Apostroph shlex-sicher gequotet


async def test_discord_post_explicit_kind_is_stored_as_given(
    monkeypatch, httpx_mock, reports_db, runner
):
    _discord_env(monkeypatch, httpx_mock)
    runner.results = [ok("Bericht #3 (retro, mcp:discord_post) gespeichert.")]
    out = await tools_ops.discord_post("📝 SP1-Retro: was lief", kind="retro")
    assert out["kind"] == "retro" and out["report_id"] == 1 and out["box"]["report_id"] == 3
    (r,) = reports_db()
    assert r.kind == "retro" and r.title == "SP1-Retro: was lief"
    assert "--kind retro" in runner.remote(0)


async def test_discord_post_box_mirror_failure_is_reported_not_fatal(
    monkeypatch, httpx_mock, reports_db, runner
):
    _discord_env(monkeypatch, httpx_mock)
    runner.results = [CmdResult(rc=255, out="", err="ssh: connect timed out", duration_s=15)]
    out = await tools_ops.discord_post("🧭 Lage & Gebote")
    assert out["posted"] is True and out["report_id"] == 1  # Post + lokale Ablage zählen
    assert out["box"] == {"ok": False, "report_id": None, "output": "ssh: connect timed out"}
    assert "nachholen" in out["hint"] and "kba report-add" in out["hint"]


async def test_discord_post_rejects_unknown_kind_before_posting(monkeypatch, httpx_mock):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")
    import kickbase_agent.config as cfg

    cfg._settings = None
    out = await tools_ops.discord_post("hallo", kind="quatsch")
    assert out["error"].startswith("ToolError: Unbekannte Berichtsart 'quatsch'")
    assert "recherche" in out["hint"]
    assert httpx_mock.get_requests() == []  # Validierung VOR dem Post


async def test_discord_post_failed_http_stores_nothing(
    monkeypatch, httpx_mock, reports_db, runner
):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")
    import kickbase_agent.config as cfg

    cfg._settings = None
    httpx_mock.add_response(
        method="POST", url="https://discord.com/api/v10/channels/123/messages", status_code=500
    )
    out = await tools_ops.discord_post("🧭 Lage & Gebote")
    assert "error" in out and reports_db() == []
    assert runner.calls == []  # auch keine Box-Spiegelung


async def test_discord_post_survives_missing_reports_table(monkeypatch, httpx_mock, runner):
    """Ohne Alembic-Upgrade: Post ist trotzdem ok, report_id None (nur Log), Box-Spiegel läuft."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    bare = sessionmaker(bind=create_engine("sqlite:///:memory:", future=True), future=True)
    monkeypatch.setattr(report_store, "SessionLocal", bare)
    _discord_env(monkeypatch, httpx_mock)
    runner.results = [ok(BOX_SAVED)]
    out = await tools_ops.discord_post("🧭 Lage & Gebote")
    assert out["posted"] is True and out["report_id"] is None and out["kind"] == "recherche"
    assert out["box"]["ok"] is True and '"local_report_id": null' in runner.remote(0)


async def test_discord_post_without_config_is_error(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "")
    import kickbase_agent.config as cfg

    cfg._settings = None
    out = await tools_ops.discord_post("hallo")
    assert out["error"] == "ToolError: Discord nicht konfiguriert."


# ------------------------------------------------------------------ tip_add


async def test_tip_add_writes_locally_and_on_box_with_safe_quoting(world, runner):
    runner.results = [
        ok("Tipp #7: O'Brien (+0.6, youtube) gespeichert — wirkt 4 Tage auf die Kauf-Scores.")
    ]
    out = await tools_ops.tip_add("O'Brien", 0.6, "youtube", 'Stamm "Sechser"; Hype')
    assert out["local"]["player_name"] == "O'Brien" and out["local"]["weight"] == 0.6
    assert out["box"]["ok"] is True and out["box"]["output"].startswith("Tipp #7")
    with Session(world.engine, future=True) as s:
        tips = list(s.execute(select(CommunityTip)).scalars())
    assert [(t.player_name, t.source, t.note) for t in tips] == [
        ("O'Brien", "youtube", 'Stamm "Sechser"; Hype')
    ]
    remote = runner.remote(0)
    assert remote.startswith("docker exec kickbase-agent uv run kba tip ")
    assert "'O'\"'\"'Brien'" in remote  # shlex-sicher gequotet
    assert "-w 0.60 -s youtube -n 'Stamm \"Sechser\"; Hype'" in remote


async def test_tip_add_reports_box_failure_but_keeps_local(world, runner):
    runner.results = [CmdResult(rc=1, out="", err="docker: No such container", duration_s=0.2)]
    out = await tools_ops.tip_add("Hoffmeier", -0.5, "reddit", "Bank")
    assert out["box"]["ok"] is False and "nachholen" in out["hint"]
    assert out["local"]["weight"] == -0.5


async def test_tip_add_validates_weight_and_name(world, runner):
    assert (await tools_ops.tip_add("X", 1.5))["error"].startswith("ToolError: weight")
    assert (await tools_ops.tip_add(" ", 0.5))["error"].startswith("ToolError: Spielername")
    assert runner.calls == []


# ------------------------------------------------------------------ learning_evidence


async def test_learning_evidence_writes_an_agent_row(world):
    out = await tools_ops.learning_evidence(
        "L-065", "confirm", "Ulrich-Gebot mit 100 % MW abgelehnt", "bid:34"
    )
    assert out["created"] is True and out["source"] == "agent" and out["remote"] is False
    with Session(world.engine, future=True) as s:
        rows = list(s.execute(select(LearningEvidence)).scalars())
    assert [(r.learning_id, r.verdict, r.source, r.rule_key) for r in rows] == [
        ("L-065", "confirm", "agent", "")
    ]


async def test_learning_evidence_is_idempotent_per_ref(world):
    await tools_ops.learning_evidence("L-065", "confirm", "a", "bid:34")
    again = await tools_ops.learning_evidence("L-065", "refute", "b", "bid:34")
    assert again["created"] is False and "schon belegt" in again["message"]
    with Session(world.engine, future=True) as s:
        assert len(list(s.execute(select(LearningEvidence)).scalars())) == 1


async def test_learning_evidence_refuses_a_bad_id_or_verdict(world):
    assert (await tools_ops.learning_evidence("065", "confirm", "x", "r:1"))[
        "error"].startswith("ToolError: learning_id")
    assert (await tools_ops.learning_evidence("L-065", "vielleicht", "x", "r:1"))[
        "error"].startswith("ToolError: verdict")
    with Session(world.engine, future=True) as s:
        assert list(s.execute(select(LearningEvidence)).scalars()) == []


def test_rsync_excludes_cover_sqlite_side_files():
    """Die Ausschluss-Muster muessen SQLite-Journale erfassen, nicht nur die DB.

    Gefunden 27.08.26: `--exclude='*.db'` matcht `kba_local.db-shm` und
    `kba_local.db-wal` NICHT — beide wurden jahrelang mit auf die Box gesynct
    und lagen dort ohne zugehoerige Datenbank daneben. Der Test prueft die
    ABSICHT (welche Dateinamen fallen raus), nicht die Schreibweise des
    Musters, damit eine spaetere Umformulierung der Liste ihn nicht aushebelt.
    """
    from fnmatch import fnmatch

    from kickbase_agent.mcp.tools_ops import RSYNC_EXCLUDES

    def ausgeschlossen(name: str) -> bool:
        return any(fnmatch(name, pat) for pat in RSYNC_EXCLUDES)

    for name in ("kba_local.db", "kba_local.db-shm", "kba_local.db-wal",
                 "kba_local.db-journal", ".env", ".env.local"):
        assert ausgeschlossen(name), f"{name} muesste ausgeschlossen sein"
    # Gegenprobe: der Code selbst darf nicht mitgerissen werden — inklusive
    # der Beinahe-Treffer, an denen ein `*.db*` gescheitert waere (Codex 27.08.)
    for name in ("models.py", "docker-compose.yml", "alembic.ini", "db",
                 "schema.dbml", "seed.db.py", "config.db-backup",
                 "database.db.template"):
        assert not ausgeschlossen(name), f"{name} darf NICHT ausgeschlossen werden"
