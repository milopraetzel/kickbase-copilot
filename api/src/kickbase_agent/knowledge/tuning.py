"""Stellschrauben der Engine — versioniert in einer Datei, Defaults im Code.

Bis heute standen die Zahlen, an denen der Agent lernen soll, als Literale mitten
im Code (`trading/expectation.py`: 0.02, 0.15, 0.55, 0.30, 0.40, 0.45, 0.60, 0.80
und ihre Schwellen). Eine Zahl im Code kann man ändern — aber nicht *belegen*: Wer
sie später liest, sieht nicht, warum sie so ist und was seither passiert ist.

Deshalb liegen sie zusätzlich in ``tuning.json`` (im Paket, also in Git, also in
jedem Deploy nachvollziehbar). Regeln:

* **Defaults gewinnen bei Zweifel.** Fehlt die Datei, ist sie kaputt, enthält sie
  einen unbekannten Namen oder etwas, das keine Zahl ist — dann gilt der Code-Wert.
  Ein Tuning-Fehler darf nie ein Absturz und nie ein stilles 0 sein.
* **Schreiben nur mit Beleg.** ``set_tuning`` verlangt Grund UND Beleg (worauf sich
  der Grund stützt). Ohne beides gibt es keine Änderung — genau das trennt Lernen
  von Herumdrehen.
* **Jede Änderung erzeugt einen Eintrag im Entscheidungs-Protokoll**
  (``knowledge/parameters.record_change`` → ``parameter_history.json`` → Zentrale).

Wer einen Parameter NEU einführt, trägt ihn hier in ``DEFAULTS`` ein — dann kennt
ihn auch ``current_parameters()`` und das Protokoll kann Historie und Ist-Wert
verknüpfen.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

TUNING_FILE = "tuning.json"


class TuningError(ValueError):
    """Unzulässige Tuning-Änderung (unbekannter Name, keine Zahl, kein Beleg)."""


#: Alle stellbaren Zahlen mit ihrem Code-Default. Der Schlüssel ist zugleich der
#: Name im Entscheidungs-Protokoll — deshalb sprechend und stabil halten.
DEFAULTS: dict[str, float] = {
    # --- Gebots-Erwartung, Manager-Listings (trading/expectation.py) ---
    # Der Verkäufer vergleicht immer mit dem Marktwert, den Kickbase ihm garantiert.
    "expectation.manager_underpay_p": 0.02,
    "expectation.manager_thin_p": 0.15,
    "expectation.manager_premium_p": 0.55,
    "expectation.manager_thin_ratio": 1.15,
    # --- Gebots-Erwartung, Kickbase-Listings (Auto-Zuschlag beim Ablauf) ---
    "expectation.kickbase_underpay_p": 0.30,
    "expectation.kickbase_underpay_ratio": 0.90,
    "expectation.kickbase_thin_p": 0.45,
    "expectation.kickbase_over_p": 0.60,
    "expectation.kickbase_over_ratio": 1.20,
    "expectation.kickbase_far_over_p": 0.80,
    "expectation.kickbase_far_over_ratio": 1.35,
    # --- Kalibrierung der Erwartung ---
    "expectation.min_sample": 5,
    "expectation.calibration_tolerance": 0.15,
    # --- Überraschungs-Auslöser (worker/surprise_job.py) ---
    # „Ich war mir sicher und habe verloren" bzw. „Ich hielt es für aussichtslos
    # und habe gewonnen" — nur diese beiden Fälle sind erklärungsbedürftig.
    "surprise.high_p": 0.60,
    "surprise.low_p": 0.20,
    # --- Selbst-Einfrieren (agent/freeze.py) --- 10 M seit 02.09. (Doktrin-Tag passt
    # durch die Schwelle; Decision-Record 37be3e3)
    "freeze.daily_loss_limit": 10_000_000,
    "freeze.unclear_write_limit": 2,
}

_cache: dict[str, float] | None = None


def _package_path() -> Path:
    return Path(str(resources.files(__package__) / TUNING_FILE))


def reset_cache() -> None:
    """Zwischenspeicher leeren (nach einem Schreibvorgang, in Tests)."""
    global _cache
    _cache = None


def load_tuning(path: Path | None = None, *, use_cache: bool = True) -> dict[str, float]:
    """Gültige Werte: Defaults, überschrieben von dem, was die Datei belegbar sagt.

    Fail-soft mit Absicht: Jede Unklarheit fällt auf den Code-Default zurück.
    """
    global _cache
    if path is None and use_cache and _cache is not None:
        return dict(_cache)

    values = dict(DEFAULTS)
    source = path or _package_path()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raw = None
    entries = raw.get("values") if isinstance(raw, dict) else None
    if isinstance(entries, dict):
        for name, value in entries.items():
            if name not in DEFAULTS:
                continue  # unbekannter Name: nicht raten, Default behalten
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue  # keine Zahl: Default behalten
            values[name] = value

    if path is None and use_cache:
        _cache = dict(values)
    return values


def tuning_value(name: str, default: float | None = None) -> float:
    """Ein einzelner Wert. Unbekannter Name ⇒ `default` (oder KeyError, wenn keiner)."""
    values = load_tuning()
    if name in values:
        return values[name]
    if default is not None:
        return default
    raise TuningError(f"Unbekannter Tuning-Parameter {name!r}")


def _write(values: dict[str, float], path: Path, note: str = "") -> None:
    payload: dict[str, Any] = {
        "note": note or (
            "Stellschrauben der Engine. Aenderungen nur mit Grund UND Beleg "
            "(knowledge/tuning.set_tuning) — jede erzeugt einen Eintrag in "
            "parameter_history.json."
        ),
        "values": {k: values[k] for k in sorted(values)},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def set_tuning(
    name: str,
    value: float,
    *,
    reason: str,
    evidence: str,
    path: Path | None = None,
) -> dict[str, Any]:
    """Einen Wert ändern — nur mit Grund UND Beleg.

    Der Beleg ist die eigentliche Hürde: „fühlt sich besser an" ist kein Beleg,
    „6 von 7 Geboten über 120 % gewonnen (Nachschau 26.08.)" ist einer. Zurück
    kommt {parameter, old, new} — der Aufrufer schreibt daraus den Decision-Record.
    """
    if name not in DEFAULTS:
        raise TuningError(
            f"Unbekannter Tuning-Parameter {name!r} — erst in DEFAULTS eintragen"
        )
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TuningError(f"Wert für {name} muss eine Zahl sein, nicht {type(value).__name__}")
    if not (reason or "").strip():
        raise TuningError("Ohne Grund keine Aenderung")
    if not (evidence or "").strip():
        raise TuningError("Ohne Beleg keine Aenderung (worauf stuetzt sich der Grund?)")

    target = path or _package_path()
    values = load_tuning(path=target, use_cache=False)
    old = values.get(name, DEFAULTS[name])
    values[name] = value
    _write(values, target)
    if path is None:
        reset_cache()
    return {"parameter": name, "old": old, "new": value,
            "reason": reason.strip(), "evidence": evidence.strip()}


def as_parameters() -> dict[str, float]:
    """Ist-Werte für `current_parameters()` (Vertrag mit UI/API/Protokoll)."""
    return load_tuning()
