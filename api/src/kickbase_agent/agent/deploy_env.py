"""Umgebungs-Tor: Kann DIESE Umgebung überhaupt einen Selbst-Deploy tragen?

Das Sicherungs-Tor (`agent/guardrail_lock.py`, `kba guardrails-check`) prüft zwei
Dinge: Sind die versiegelten Tests unverändert, und ist die Suite grün. Beides
setzt still voraus, dass Manifest und Suite überhaupt existieren — und der Weg
danach (`agent/self_change.py`) setzt genauso still voraus, dass `git` da ist.

Im Worker-Image stimmt keine dieser Annahmen (geprüft 26.08.2026):

* `api/Dockerfile` kopiert `pyproject.toml`, `src/`, `alembic.ini` und den
  Entrypoint — **weder `guardrails.lock` noch `tests/`**.
* `uv sync --no-dev` lässt `pytest` weg; die Suite wäre auch mit Dateien nicht
  startbar.
* `python:3.12-slim` bringt **kein `git`, kein `rsync`, kein `ssh`** mit, und der
  Dockerfile installiert nichts nach. Auch die Box selbst hat kein Git-Repo
  (`docs/BETRIEB.md` §1: „Kein Git auf der Box — Deploy = rsync vom Mac").

Ein Selbst-Deploy läuft deshalb ausschließlich am **Arbeitsplatz mit
Git-Checkout**: Branch, Commit, `git revert`, danach rsync + `docker compose`
gegen die Box (MCP-Tool `deploy_verify`). Der Container ist das ZIEL des Deploys,
nie sein Ausgangspunkt.

Dieses Modul zieht daraus die harte Grenze. Es kopiert nichts ins Image nach —
das ergäbe ein zweites, halbes Tor (Prüfsummen prüfbar, Deploy trotzdem
unmöglich) — sondern beantwortet vor jedem Umbau eine Frage:

1. **Manifest lesbar?** (`guardrails.lock`, nicht leer)
2. **Testsuite vorhanden?** (jeder Sicherungs-Test im Baum, Testläufer auf PATH)
3. **Git-Repo verfügbar?** (`git` da UND der Baum ist ein Work-Tree)

Fehlt eines: Abbruch mit Begründung, laut im Log, Alarm nach Discord. Nie ein
stilles „ok", nie ein Traceback statt eines Urteils — auch eine Prüfung, die
selbst scheitert, ist ein Nein.

Bewusst NICHT dabei: Einfrieren. Der Einfrier-Zustand sperrt Geld-Writes; die
falsche Umgebung sagt über den Handel nichts aus, und es bleibt auch nichts
Halbfertiges zurück (das Tor greift vor dem ersten git-Aufruf). Wer hier
einfriert, macht aus einem verweigerten Umbau einen verlorenen Spieltag.
Eingefroren wird, wenn der Rückwärtsgang klemmt — das ist `self_deploy.rollback`.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from kickbase_agent.agent import guardrail_lock as gl

log = logging.getLogger(__name__)

#: Steht in jeder Absage — eine Fehlermeldung ohne Adresse kostet die nächste Stunde.
PLACE = (
    "Ein Selbst-Deploy läuft am Arbeitsplatz mit Git-Checkout (Repo auf dem Mac): "
    "Branch, Commit, Rückwärtsgang, dann rsync + `docker compose` gegen die Box. "
    "Der Container ist das Ziel des Deploys, nie sein Ausgangspunkt."
)

#: `shutil.which`-Signatur — injizierbar, damit Tests keinen echten PATH brauchen.
Which = Callable[[str], str | None]
#: (returncode, ausgabe)
Runner = Callable[..., tuple[int, str]]

#: Werkzeug + Frage, mit der ein Work-Tree bewiesen wird (keine Heuristik auf `.git`:
#: im Worktree ist `.git` eine Datei, im Submodul ein Verweis — git weiß es besser).
GIT_PROBE: tuple[str, ...] = ("git", "rev-parse", "--is-inside-work-tree")


def _which(name: str) -> str | None:
    """Eigene Indirektion statt `shutil.which` direkt: so ist sie patchbar."""
    return shutil.which(name)


def _probe_run(cmd: Sequence[str], cwd: Path) -> tuple[int, str]:
    """Sonde ausführen. Jeder Fehlschlag ist ein Rückgabewert, nie eine Ausnahme."""
    try:
        proc = subprocess.run(  # noqa: S603 — fester Befehl aus GIT_PROBE
            list(cmd), cwd=str(cwd), capture_output=True, text=True, check=False, timeout=20
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{cmd[0]!r} nicht ausführbar: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


@dataclass
class EnvironmentReport:
    """Urteil über die Umgebung — maschinen- und menschenlesbar."""

    ok: bool
    root: str = ""
    #: manifest | suite | git — je True/False, damit man sieht, WAS fehlt
    checks: dict[str, bool] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.ok:
            return f"Umgebung trägt einen Selbst-Deploy ({self.root})."
        missing = ", ".join(name for name, good in self.checks.items() if not good)
        return (
            f"Diese Umgebung trägt keinen Selbst-Deploy ({self.root or 'unbekannt'}): "
            f"{missing or 'unklar'} fehlt/fehlen."
        )

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "root": self.root,
            "checks": dict(self.checks),
            "problems": list(self.problems),
            "summary": self.summary,
        }


def format_environment_alarm(report: EnvironmentReport) -> str:
    """Discord: warum hier kein Umbau stattfindet — und wo er stattfinden müsste."""
    return "\n".join(
        [
            "🚧 **Selbstumbau abgebrochen — falsche Umgebung.**",
            f"Ort: `{report.root or 'unbekannt'}`",
            *(f"• {problem}" for problem in report.problems[:6]),
            PLACE,
        ]
    )


def check_environment(
    *,
    root: Path | None = None,
    start: Path | None = None,
    which: Which | None = None,
    run: Runner | None = None,
) -> EnvironmentReport:
    """Die drei Fragen beantworten. Fail-closed: Was nicht beweisbar da ist, fehlt."""
    look = which or _which
    probe = run or _probe_run

    try:
        base = root or gl.project_root(start)
    except gl.LockError as exc:
        # Ohne Projektwurzel gibt es keinen Baum, in dem man etwas suchen könnte.
        return EnvironmentReport(
            ok=False,
            root=str(start or ""),
            checks={"manifest": False, "suite": False, "git": False},
            problems=[str(exc)],
        )

    checks: dict[str, bool] = {}
    problems: list[str] = []

    # 1. Manifest — ohne es kann `guardrails-check` gar nichts prüfen.
    try:
        lock = gl.load_lock(base / gl.LOCK_FILENAME)
        if not lock.get("files"):
            raise gl.LockError(
                f"Manifest {gl.LOCK_FILENAME} ist leer — ein Tor über null Dateien ist keines"
            )
        checks["manifest"] = True
    except gl.LockError as exc:
        checks["manifest"] = False
        problems.append(str(exc))

    # 2. Testsuite — die Dateien UND etwas, das sie starten kann.
    suite: list[str] = [
        f"Sicherungs-Test {rel} fehlt im Baum"
        for rel in gl.GUARDED_TESTS
        if not (base / rel).is_file()
    ]
    launcher = gl.SUITE_COMMAND[0]
    if look(launcher) is None:
        suite.append(f"Testläufer {launcher!r} nicht auf PATH — die Suite ist nicht startbar")
    checks["suite"] = not suite
    problems.extend(suite)

    # 3. Git — Branch, Commit und vor allem der Rückwärtsgang hängen daran.
    git: list[str] = []
    if look(GIT_PROBE[0]) is None:
        git.append(
            f"{GIT_PROBE[0]!r} nicht auf PATH — ohne git gibt es weder Branch noch Commit "
            "noch Rückwärtsgang"
        )
    else:
        try:
            code, output = probe(list(GIT_PROBE), base)
        except Exception as exc:  # noqa: BLE001 — eine Sonde darf das Tor nie sprengen
            code, output = 127, f"{type(exc).__name__}: {exc}"
        if code != 0 or not output.strip().startswith("true"):
            git.append(f"{base} ist kein Git-Repo: {output.strip()[:160]}")
    checks["git"] = not git
    problems.extend(git)

    report = EnvironmentReport(
        ok=all(checks.values()), root=str(base), checks=checks, problems=problems
    )
    if not report.ok:
        log.error("%s Beanstandungen: %s", report.summary, "; ".join(problems))
    return report
