"""Selbstumbau — der Weg, den eine Änderung des Agenten an sich selbst gehen muss.

Das ist die Stufe, für die alles davor gebaut wurde. Sie fügt keine neue Fähigkeit
hinzu, sondern eine Reihenfolge, aus der man nicht ausbrechen kann:

    Flag ➜ nicht eingefroren ➜ Umgebung ➜ Beleg ➜ Branch ➜ Änderung
        ➜ Tests + Sicherungen ➜ ein Commit mit `[selbst]` ➜ Deploy
        ➜ Gesundheitsprüfung ➜ (rot) Rollback + Alarm ➜ (Rollback rot) Einfrieren

Vier Dinge sind Absicht und keine Umständlichkeit:

* **Die Umgebung wird VOR dem ersten git-Aufruf geprüft** (`agent/deploy_env.py`).
  Der Weg braucht ein Git-Repo, ein lesbares Sicherungs-Manifest und eine
  startbare Suite; im Worker-Image gibt es nichts davon. Ohne dieses Tor liefe
  ein scharfgeschalteter Umbau dort in ein rohes `FileNotFoundError: 'git'` —
  noch bevor `guardrails-check` überhaupt gefragt wurde.
* **Tests und Sicherungen laufen VOR dem Commit.** Ein Commit, den man gleich
  wieder zurücknehmen muss, ist teurer als einer, der nie entstand.
* **Genau EIN Commit trägt den Marker.** Der Decision-Record kommt als eigener,
  unmarkierter Commit hinterher — ein Rollback nimmt die Änderung zurück, nicht
  die Tatsache, dass sie stattgefunden hat.
* **Das Deploy-Sperrfenster gilt auch hier.** Fällt ein Urteil zwischen 21:25 und
  22:25 Berlin, wird committet und der Deploy vertagt. Die Änderung geht nicht
  verloren; sie wartet.

Standardmäßig AUS (`AGENT_SELF_CHANGE=0`). Der Agent darf dieses Flag nie selbst
setzen — Scharfschalten ist Emils Handgriff in der Box-`.env` (eiserne Regel 2).
Die Kommandos (git, Deploy, Gesundheitsmessung) kommen als Callables herein, damit
kein Test je ein echtes Repository anfasst.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from kickbase_agent.agent import deploy_env as de
from kickbase_agent.agent import freeze as fz
from kickbase_agent.agent import guardrail_lock as gl
from kickbase_agent.agent import self_deploy as sd

log = logging.getLogger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")

#: Deploy-Sperrfenster (eiserne Regel 8): Der Scheduler lebt im Speicher und holt
#: nichts nach — ein Neustart um 22:10 verschluckt Snapshot und Briefing.
DEPLOY_BLOCK_START = (21, 25)
DEPLOY_BLOCK_END = (22, 25)

#: Branch-Präfix jeder Selbstmodifikation — auf einen Blick von Emils Arbeit trennbar.
BRANCH_PREFIX = "selbst"

_SLUG_RE = re.compile(r"[^a-z0-9]+")

#: (returncode, ausgabe)
Runner = Callable[..., tuple[int, str]]


@dataclass
class ChangeRequest:
    """Was geändert werden soll — mit allem, was der Beleg-Pflicht genügt."""

    art: str            # parameter | code
    bereich: str        # z. B. "erwartung" (steht im Commit-Betreff)
    summary: str        # die Aussage des Commits
    surprise_ref: str   # welcher Fall das ausgelöst hat
    reason: str = ""
    beleg: str = ""     # L-ID oder surprise:<ref>
    recherche: str = ""  # Quelle + Datum + Aussage (oder: warum nichts zu finden war)
    expect: str = ""
    review: str = ""
    einzelfall_ausnahme: bool = False
    # nur art == "parameter":
    stellschraube: str | None = None
    neuer_wert: float | None = None
    tuning_path: Path | None = None
    history_path: Path | None = None

    @property
    def slug(self) -> str:
        text = _SLUG_RE.sub("-", f"{self.bereich} {self.summary}".lower()).strip("-")
        return text[:48] or "umbau"


@dataclass
class ChangeResult:
    ok: bool
    #: Wo der Weg endete: flag | eingefroren | umgebung | beleg | aenderung |
    #: sicherungen | commit | vertagt | deploy | gesundheit | fertig
    schritt: str
    sha: str = ""
    branch: str = ""
    deployed: bool = False
    rolled_back: bool = False
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "schritt": self.schritt, "sha": self.sha,
            "branch": self.branch, "deployed": self.deployed,
            "rolled_back": self.rolled_back, "problems": list(self.problems),
        }


def in_deploy_block(now: datetime) -> bool:
    """Liegt `now` im Sperrfenster 21:25–22:25 Berlin?"""
    local = now.astimezone(BERLIN)
    minutes = local.hour * 60 + local.minute
    start = DEPLOY_BLOCK_START[0] * 60 + DEPLOY_BLOCK_START[1]
    end = DEPLOY_BLOCK_END[0] * 60 + DEPLOY_BLOCK_END[1]
    return start <= minutes <= end


async def run_self_change(
    request: ChangeRequest,
    *,
    run: Runner | None = None,
    cwd: Any = None,
    guardrails: Callable[..., gl.GuardrailResult] | None = None,
    environment: Callable[..., de.EnvironmentReport] | None = None,
    apply: Callable[[], Any] | None = None,
    deploy: Callable[[], Awaitable[bool]] | None = None,
    health: Callable[[], sd.HealthSnapshot] | None = None,
    post: Callable[..., Awaitable[Any]] | None = None,
    session: Session | None = None,
    now: datetime | None = None,
) -> ChangeResult:
    """Eine Selbstmodifikation durch alle Tore führen. Jeder Abbruch ist begründet."""
    from kickbase_agent.config import get_settings

    stamp = now or datetime.now(UTC)
    runner = run or sd._default_run
    check = guardrails or gl.check

    # 1. Flag — der Selbstumbau ist standardmäßig aus.
    if not get_settings().agent_self_change:
        return ChangeResult(
            False, "flag",
            problems=["AGENT_SELF_CHANGE=0 — Selbstumbau nicht geärmt (Emils Schalter)"],
        )

    # 2. Eingefroren heißt eingefroren — auch für Umbauten.
    if session is not None and fz.is_frozen(session):
        active = fz.active_freeze(session)
        return ChangeResult(
            False, "eingefroren",
            problems=[f"Selbst-Einfrieren aktiv: {active.reason if active else '?'}"],
        )

    # 3. Die Umgebung muss einen Deploy überhaupt tragen können — VOR dem ersten
    #    git-Aufruf. Ein Sicherungs-Tor, das in der Zielumgebung gar nicht laufen
    #    kann, ist kein Tor; hier ist der einzige Ort, an dem das auffällt, bevor
    #    irgendetwas Halbfertiges entsteht. Bewusst OHNE Einfrieren (siehe
    #    agent/deploy_env.py): Es liegt nichts halb herum, und der Handel ist gesund.
    env = (environment or de.check_environment)()
    if not env.ok:
        log.error("Selbstumbau abgebrochen — %s", env.summary)
        if post is not None:
            await post(de.format_environment_alarm(env))
        return ChangeResult(False, "umgebung", problems=[env.summary, *env.problems])

    # 4. Beleg-Pflicht: ohne Grund, Beleg und Recherche keine Änderung.
    missing = [
        name for name, value in (
            ("Grund", request.reason), ("Beleg", request.beleg),
            ("Recherche", request.recherche),
        ) if not (value or "").strip()
    ]
    if missing:
        return ChangeResult(
            False, "beleg", problems=[f"Es fehlt: {', '.join(missing)}"]
        )

    branch = f"{BRANCH_PREFIX}/{stamp:%Y%m%d}-{request.slug}"
    code, output = runner(["git", "checkout", "-b", branch], cwd)
    if code != 0:
        return ChangeResult(False, "aenderung", branch=branch,
                            problems=[f"Branch {branch} fehlgeschlagen: {output[:200]}"])

    # 5. Die eigentliche Änderung.
    change: dict[str, Any] | None = None
    if request.art == "parameter":
        change = _apply_parameter(request)
        if change is None:
            return ChangeResult(False, "aenderung", branch=branch,
                                problems=["Parameter-Aenderung nicht ausfuehrbar "
                                          "(Name, Wert oder Beleg fehlt)"])
    else:
        if apply is None:
            return ChangeResult(False, "aenderung", branch=branch,
                                problems=["Code-Weg ohne Aenderung — nichts zu committen"])
        apply()

    # 6. Tests + Sicherungen VOR dem Commit.
    guard = check()
    if not guard.ok:
        return ChangeResult(False, "sicherungen", branch=branch,
                            problems=guard.problems or [guard.summary])

    # 7. Genau EIN Commit mit dem Marker.
    subject = sd.commit_subject(request.bereich, request.summary)
    for cmd in (["git", "add", "-A"], ["git", "commit", "-m", subject]):
        code, output = runner(cmd, cwd)
        if code != 0:
            return ChangeResult(False, "commit", branch=branch,
                                problems=[f"{' '.join(cmd[:2])} fehlgeschlagen: "
                                          f"{output[:200]}"])
    code, sha_out = runner(["git", "rev-parse", "HEAD"], cwd)
    sha = sha_out.strip().splitlines()[0] if code == 0 and sha_out.strip() else ""

    # 8. Decision-Record — eigener, unmarkierter Commit, mit dem ECHTEN Sha.
    if change is not None and sha:
        _record(request, change, sha, runner, cwd)

    # 9. Deploy — im Sperrfenster wird vertagt, nicht erzwungen.
    if in_deploy_block(stamp):
        log.info("Selbstumbau %s committet, Deploy vertagt (Sperrfenster)", sha[:8])
        if post is not None:
            await post(
                f"⏸️ **Selbstumbau {sha[:8]} wartet.** Commit ist drin, der Deploy "
                "fällt ins Sperrfenster 21:25–22:25 — er läuft danach."
            )
        return ChangeResult(True, "vertagt", sha=sha, branch=branch)

    if deploy is not None:
        try:
            deployed = bool(await deploy())
        except Exception as exc:  # noqa: BLE001 — der Rückwärtsgang ist wichtiger
            deployed = False
            log.warning("Deploy des Selbstumbaus fehlgeschlagen: %s", exc)
        if not deployed:
            rolled = await sd.rollback(
                sha, reason="Deploy fehlgeschlagen", run=runner, cwd=cwd,
                post=post, session=session, now=stamp,
            )
            return ChangeResult(False, "deploy", sha=sha, branch=branch,
                                rolled_back=rolled.reverted,
                                problems=["Deploy fehlgeschlagen", *rolled.problems])
    else:
        deployed = False

    # 10. Gesundheitsprüfung — messen, nicht hoffen.
    if health is not None:
        result = sd.evaluate_health(health())
        if not result.ok:
            if post is not None:
                await post(sd.format_health_alarm(sha, result))
            # Redeploy des zurückgerollten Standes: Ein Revert im Repo hilft nichts,
            # solange auf der Box weiter der kaputte Container läuft.
            rolled = await sd.rollback(
                sha, reason="; ".join(result.problems[:3]), run=runner, cwd=cwd,
                redeploy=deploy, post=post, session=session, now=stamp,
            )
            return ChangeResult(False, "gesundheit", sha=sha, branch=branch,
                                deployed=deployed, rolled_back=rolled.reverted,
                                problems=[*result.problems, *rolled.problems])

    return ChangeResult(True, "fertig", sha=sha, branch=branch, deployed=deployed)


def _apply_parameter(request: ChangeRequest) -> dict[str, Any] | None:
    """Die Zahl in der versionierten Datei ändern (mit Beleg). None = geht nicht."""
    from kickbase_agent.knowledge.tuning import TuningError, set_tuning

    if not request.stellschraube or request.neuer_wert is None:
        return None
    try:
        return set_tuning(
            request.stellschraube, request.neuer_wert,
            reason=request.reason, evidence=request.beleg,
            path=request.tuning_path,
        )
    except TuningError as exc:
        log.warning("Parameter-Aenderung abgelehnt: %s", exc)
        return None


def _record(
    request: ChangeRequest, change: dict[str, Any], sha: str, runner: Runner, cwd: Any
) -> None:
    """Decision-Record schreiben und als EIGENEN Commit ablegen.

    Bewusst getrennt vom Marker-Commit: Ein `git revert` nimmt die Änderung zurück,
    das Protokoll bleibt — sonst verschwände mit der Änderung auch ihre Spur.
    """
    from kickbase_agent.knowledge.parameters import RecordError, record_change
    from kickbase_agent.knowledge.self_improvement import SOURCE_SELF

    try:
        record_change(
            parameter=change["parameter"], old=change["old"], new=change["new"],
            reason=request.reason, commit=sha, expect=request.expect,
            review=request.review, beleg=request.beleg,
            einzelfall_ausnahme=request.einzelfall_ausnahme,
            # Der Selbstumbau zeichnet sich als solcher — in der Zentrale steht
            # sonst nicht, WER geaendert hat (Emils Tabelle, 26.08.).
            quelle=SOURCE_SELF,
            path=request.history_path,
        )
    except RecordError as exc:
        log.warning("Decision-Record nicht geschrieben: %s", exc)
        return
    runner(["git", "add", "-A"], cwd)
    runner(
        ["git", "commit", "-m",
         f"docs(entscheidung): {change['parameter']} {change['old']} -> "
         f"{change['new']} ({sha[:8]})"],
        cwd,
    )
