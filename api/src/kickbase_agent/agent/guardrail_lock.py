"""Tests-als-Tor: die Sicherungs-Tests sind versiegelt, sonst gibt es keinen Deploy.

Sobald der Agent seinen eigenen Code ändern darf, entsteht eine neue Gefahr: Nicht
der schlechte Code ist das Problem — den fängt die Suite —, sondern der stille Griff
an die Suite selbst. Ein Agent, der eine Zeile ändert und den störenden Test
„anpasst", hat seine Bremsen ausgebaut und merkt es nicht einmal.

Deshalb liegt neben der Suite ein Manifest (``api/guardrails.lock``): SHA-256 über
genau die Tests, die die harten Grenzen bewachen — 33-%-Regel, Executor-Interlocks
und Preflight, Anpfiff-Notbremse/Konto ≥ 0, Vereinslimit, Selbst-Einfrieren,
Selbst-Deploy-Tor — und, seit dem 26.08.2026, die Siegelprüfung selbst.
``kba guardrails-check`` prüft zweierlei:

1. Jede Datei des Manifests ist unverändert — UND jede erwartete Datei steht auch
   wirklich drin (sonst wäre „Eintrag löschen" der bequeme Ausweg).
2. Die volle Suite ist grün.

Exit-Code ≠ 0 blockiert jeden Selbst-Deploy (agent/self_change.py). Neu versiegeln
ist ein bewusster, menschlicher Akt: ``kba guardrails-seal --reason "…"``. Der
Selbstumbau ruft ihn NIE auf — er darf prüfen, nicht siegeln.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: Dateiname des Manifests (liegt neben `pyproject.toml`, also in `api/`).
LOCK_FILENAME = "guardrails.lock"
LOCK_VERSION = 1

#: Die Sicherungs-Tests. Bewusst kurz: Was hier steht, ist eine harte Grenze des
#: Systems — nicht „wichtige Tests", sondern die, deren Wegfall Geld kostet.
GUARDED_TESTS: tuple[str, ...] = (
    # 33-%-Regel und Kaufkraft-Zyklus (Konto − offene Gebote + ⅓ Teamwert)
    "tests/trading/test_finance.py",
    "tests/trading/test_finance_cycle.py",
    # Die einzige Schreibstelle: Kill-Switch, Live-Interlocks, Preflight,
    # Tages-Caps, Overpay-Kappe, Vereinslimit, Selbst-Einfrieren-Interlock
    "tests/agent/test_executor.py",
    # Konto ≥ 0 beim Anpfiff: Notbremse und Schutzfenster
    "tests/worker/test_kickoff_guard.py",
    "tests/decision/test_kickoff_freeze.py",
    # Vereinslimit in der Engine (Liga-Regel lpc)
    "tests/decision/test_policies.py",
    # Selbst-Einfrieren (A2) — die Sicherung, die die Sicherungen scharf hält
    "tests/agent/test_freeze.py",
    # Umgebungs-Tor vor dem Selbst-Deploy (agent/deploy_env.py): Manifest lesbar,
    # Suite startbar, Git-Work-Tree da. Ohne dieses Tor läuft ein Umbau an einem
    # Ort los, an dem er weder prüfbar noch rückholbar ist.
    "tests/agent/test_deploy_env.py",
    # SELBSTBEZUG — GEWOLLT, NICHT AUFRÄUMEN:
    # Diese Zeile versiegelt die Prüfung, die das Siegel prüft. Sie sieht nach
    # einem Zirkelschluss aus und ist keiner: Die Prüfsumme steht im Manifest,
    # nicht in dieser Datei, also kann die Datei ihr eigenes Urteil nicht
    # fälschen — sie kann nur auffliegen.
    "tests/agent/test_guardrail_lock.py",
)

# Warum die beiden letzten Einträge zusammengehören (26.08.2026, auf Emils
# Zustimmung hin ergänzt):
#
# Bis hierher galt die Versiegelung nur für die Geld-Regeln. Tor und Test des
# Selbst-Deploys standen daneben — beide unversiegelt. Damit hätte ein
# Selbstumbau in EINER Änderung das Tor (`agent/deploy_env.py`) entschärfen und
# den Test, der das gemerkt hätte, gleich mit „anpassen" können: exakt der Weg,
# den die Versiegelung für `tests/trading/test_finance.py` & Co. von Anfang an
# verstellt. Ein Tor, dessen Prüfung frei editierbar ist, ist ein Türrahmen.
#
# Der Preis ist Absicht: Wer diese Datei künftig legitim ändert, muss danach
# `kba guardrails-seal --reason "…"` von Hand ausführen — sonst wird
# `guardrails-check` rot und jeder Selbst-Deploy bleibt stehen. Genau dieser
# menschliche Handgriff ist der Punkt; der Selbstumbau darf prüfen, nie siegeln.

#: Volle Suite. Genau der Befehl aus CLAUDE.md §7 — eine zweite Wahrheit über
#: „wie man testet" wäre der erste Schritt zum stillen Auseinanderlaufen.
SUITE_COMMAND: tuple[str, ...] = ("uv", "run", "--extra", "dev", "pytest", "-q")

#: (returncode, ausgabe) — injizierbar, damit Tests nie eine echte Suite starten.
Runner = Callable[[Sequence[str], Path], tuple[int, str]]


class LockError(RuntimeError):
    """Das Manifest fehlt, ist unlesbar oder hat nicht die erwartete Form."""


def project_root(start: Path | None = None) -> Path:
    """Wurzel des `api/`-Baums: der nächste Ordner mit `pyproject.toml`.

    Bewusst ohne Env-Variable: eine Umgebungsvariable wäre genau der Hebel, mit
    dem sich ein Selbst-Deploy auf ein harmloses Fake-Manifest umbiegen ließe.
    """
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise LockError("Projektwurzel (pyproject.toml) nicht gefunden")


def digest(path: Path) -> str:
    """SHA-256 über den Dateiinhalt (Bytes, keine Normalisierung)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(root: Path, files: Iterable[str] | None = None) -> dict:
    """Manifest aus dem aktuellen Baum bauen (sortiert = stabile Diffs).

    `files=None` heißt GUARDED_TESTS — bewusst zur Laufzeit aufgelöst und nicht
    als Default-Argument: ein an die Funktion gebundener Default wäre beim Testen
    nicht mehr austauschbar (klassische Falle).
    """
    entries: dict[str, str] = {}
    for rel in sorted(GUARDED_TESTS if files is None else files):
        path = root / rel
        if not path.is_file():
            raise LockError(f"Sicherungs-Test {rel} existiert nicht — nichts zu versiegeln")
        entries[rel] = digest(path)
    return {"version": LOCK_VERSION, "files": entries}


def write_lock(
    root: Path,
    files: Iterable[str] | None = None,
    *,
    reason: str = "",
    sealed_at: str = "",
) -> Path:
    """Manifest schreiben. `reason`/`sealed_at` sind reine Nachvollziehbarkeit."""
    manifest = build_manifest(root, files)
    if reason:
        manifest["reason"] = reason
    if sealed_at:
        manifest["sealed_at"] = sealed_at
    path = root / LOCK_FILENAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_lock(path: Path) -> dict:
    """Manifest lesen; jede Unklarheit ist ein Fehler, nie ein stilles Weiter."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LockError(f"Manifest {path.name} nicht lesbar: {exc}") from exc
    except ValueError as exc:
        raise LockError(f"Manifest {path.name} ist kein gültiges JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise LockError(f"Manifest {path.name} hat nicht die erwartete Form")
    return data


def verify_manifest(
    lock: dict, root: Path, *, expected: Iterable[str] | None = None
) -> list[str]:
    """Alle Beanstandungen im Klartext; leere Liste = unverändert.

    Drei Wege, eine Sicherung loszuwerden — alle drei werden benannt: Datei
    ändern, Datei löschen, Eintrag aus dem Manifest streichen.
    """
    files: dict[str, str] = lock.get("files", {})
    problems: list[str] = []
    for rel in sorted(GUARDED_TESTS if expected is None else expected):
        if rel not in files:
            problems.append(f"{rel} ist ein Sicherungs-Test, steht aber nicht im Manifest")
    for rel, expected_digest in sorted(files.items()):
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel} fehlt im Baum (Manifest kennt ihn)")
            continue
        actual = digest(path)
        if actual != expected_digest:
            problems.append(
                f"{rel} wurde geaendert (Manifest {expected_digest[:12]}…, "
                f"jetzt {actual[:12]}…)"
            )
    return problems


def _subprocess_runner(cmd: Sequence[str], cwd: Path) -> tuple[int, str]:
    """Suite starten. Auch „lässt sich nicht starten" ist ein Ergebnis, keine Ausnahme.

    Fehlt der Testläufer — im Worker-Image gibt es weder dev-Extras noch `tests/` —,
    warf `subprocess.run` bisher `FileNotFoundError` mitten durch das Tor hindurch;
    der Aufrufer bekam gar kein Urteil. Kein Urteil ist schlechter als ein Nein:
    Das Tor soll blockieren, nicht abstürzen.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — fester Befehl aus SUITE_COMMAND
            list(cmd), cwd=str(cwd), capture_output=True, text=True, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"Suite nicht startbar ({cmd[0]!r}): {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


@dataclass
class GuardrailResult:
    """Ergebnis von `kba guardrails-check` — maschinen- und menschenlesbar."""

    ok: bool
    checked: int = 0
    problems: list[str] = field(default_factory=list)
    #: None = Suite bewusst nicht gelaufen (--no-suite oder Manifest schon rot)
    suite_ok: bool | None = None
    suite_output: str = ""

    @property
    def summary(self) -> str:
        if self.ok:
            suite = "Suite grün" if self.suite_ok else "Suite nicht geprüft"
            return f"Sicherungen unversehrt ({self.checked} Dateien), {suite}."
        if self.problems:
            return (
                f"Sicherungen verletzt: {len(self.problems)} Beanstandung(en) "
                "— kein Selbst-Deploy."
            )
        return "Suite rot — kein Selbst-Deploy."

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "problems": list(self.problems),
            "suite_ok": self.suite_ok,
            "summary": self.summary,
        }


def check(
    *,
    root: Path | None = None,
    expected: Iterable[str] | None = None,
    runner: Runner | None = None,
    run_suite: bool = True,
    lock_path: Path | None = None,
) -> GuardrailResult:
    """Das Tor. Fail-closed: Was nicht beweisbar in Ordnung ist, ist nicht ok.

    Reihenfolge mit Absicht: Erst das Manifest, dann die Suite. Ein manipuliertes
    Manifest ist ein Abbruchgrund — eine grüne Suite darüber wäre wertlos, weil
    unklar wäre, WELCHE Tests da grün geworden sind.
    """
    base = root or project_root()
    path = lock_path or (base / LOCK_FILENAME)
    try:
        lock = load_lock(path)
    except LockError as exc:
        return GuardrailResult(ok=False, problems=[str(exc)])

    problems = verify_manifest(lock, base, expected=expected)
    checked = len(lock.get("files", {}))
    # Der einzige Weg zu einem stillen „ok": ein leeres Manifest, über das niemand
    # etwas erwartet (`expected=()`). „Sicherungen unversehrt (0 Dateien)" ist
    # formal wahr und inhaltlich das Gegenteil eines Tores.
    if not checked:
        problems.insert(
            0, f"Manifest {path.name} ist leer — ein Tor über null Dateien ist keines"
        )
    if problems:
        return GuardrailResult(ok=False, checked=checked, problems=problems)
    if not run_suite:
        return GuardrailResult(ok=True, checked=checked)

    try:
        code, output = (runner or _subprocess_runner)(SUITE_COMMAND, base)
    except Exception as exc:  # noqa: BLE001 — ein Läufer darf das Tor nie sprengen
        code, output = 127, f"Suite nicht startbar ({SUITE_COMMAND[0]!r}): {exc}"
    if code != 0:
        return GuardrailResult(
            ok=False, checked=checked, suite_ok=False, suite_output=_tail(output)
        )
    return GuardrailResult(ok=True, checked=checked, suite_ok=True, suite_output=_tail(output))


def _tail(text: str, lines: int = 25) -> str:
    """Letzte Zeilen der Testausgabe — genug für die Diagnose, wenig fürs Log."""
    rows = (text or "").strip().splitlines()
    return "\n".join(rows[-lines:])
