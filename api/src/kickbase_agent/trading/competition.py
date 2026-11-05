"""Wettbewerbsmodell mit zensierten Zuschlägen und getrenntem Zukunftstest.

Ein eigener Gewinn bei B beobachtet nur Rivalengebot <= B. Ein belegter Verlust
beobachtet > B. Ein fremder Auktionsgewinner liefert dessen gezahlten Höchstpreis.
Managerangebote werden getrennt als Annahmeereignisse gelernt.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import numpy as np
from scipy.optimize import minimize
from scipy.special import log_ndtr, ndtr

DEFAULT_REGISTRY_DIR = Path("data/models/competition")


def _context(league_id: str, listing_type: str) -> str:
    if not league_id.isdigit() or listing_type not in {"kickbase", "manager"}:
        raise ValueError("Gültige Liga-ID und Listingart kickbase/manager erforderlich.")
    return f"{league_id}-{listing_type}"


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def _atomic(path: Path, value: dict) -> None:
    content = _canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _registry_lock(directory: Path, context: str):
    import fcntl

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f".{context}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def artifact_valid(artifact: dict, *, league_id: str, listing_type: str) -> bool:
    """Modell, Kontext, getrennte Zeitgruppen und Mindestprüfung unabhängig prüfen."""
    try:
        if (
            type(artifact["schema_version"]) is not int
            or artifact["schema_version"] != 1
            or not re.fullmatch(r"competition-[a-f0-9]{16}", artifact["version"])
            or artifact["algorithm_version"] != "censored-lognormal-v1-gate-v2"
            or artifact["status"] not in {"candidate", "promoted"}
            or artifact["league_id"] != league_id
            or artifact["listing_type"] != listing_type
            or artifact["eligible_for_promotion"] is not True
            or not _passed_evaluation(artifact)
        ):
            return False
        _context(league_id, listing_type)
        trained = datetime.fromisoformat(artifact["trained_through"])
        created = datetime.fromisoformat(artifact["created_at"])
        validation = date.fromisoformat(artifact["split_dates"]["validation_start"])
        test = date.fromisoformat(artifact["split_dates"]["test_start"])
        if (
            trained.tzinfo is None
            or created.tzinfo is None
            or not trained.date() < validation < test <= created.date()
        ):
            return False
        splits = artifact["splits"]
        if (
            any(
                type(splits[k]) is not int or splits[k] <= 0
                for k in ("train", "validation", "test")
            )
            or type(artifact["rows"]) is not int
            or artifact["rows"] != sum(splits[k] for k in ("train", "validation", "test"))
            or any(artifact[k]["evaluated_inputs"] > splits[k] for k in ("validation", "test"))
        ):
            return False
        weights, sigma = artifact["model"]["weights"], artifact["model"]["sigma"]
        if (
            not isinstance(weights, list)
            or len(weights) != 5
            or any(type(w) not in (int, float) or not math.isfinite(w) for w in weights)
            or type(sigma) not in (int, float)
            or not math.isfinite(sigma)
            or sigma <= 0
        ):
            return False
        low, high = artifact["market_value_range"]
        return type(low) is int and type(high) is int and 0 < low <= high
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def _resolve(pointer: dict, directory: Path, league_id: str, listing_type: str) -> dict:
    context = _context(league_id, listing_type)
    if not isinstance(pointer, dict):
        raise ValueError("Aktive Modellreferenz ist kein strukturiertes Objekt.")
    if (
        type(pointer.get("registry_schema_version")) is not int
        or pointer.get("registry_schema_version") != 1
        or pointer.get("league_id") != league_id
        or pointer.get("listing_type") != listing_type
        or not re.fullmatch(r"competition-[a-f0-9]{16}", str(pointer.get("version")))
    ):
        raise ValueError("Aktive Modellreferenz ungültig.")
    artifact = json.loads(
        (directory / "candidates" / context / f"{pointer['version']}.json").read_text()
    )
    if (
        not artifact_valid(artifact, league_id=league_id, listing_type=listing_type)
        or artifact["version"] != pointer["version"]
        or hashlib.sha256(_canonical(artifact)).hexdigest() != pointer.get("sha256")
    ):
        raise ValueError("Modellkandidat verändert oder Freigabeprüfung nicht bestanden.")
    return {**artifact, "status": "promoted"}


def promote_competition(
    candidate: str | Path,
    *,
    league_id: str,
    listing_type: str,
    registry_dir: str | Path = DEFAULT_REGISTRY_DIR,
) -> dict:
    """Geprüften unveränderten Kandidaten ausdrücklich und atomisch aktivieren."""
    context = _context(league_id, listing_type)
    artifact = json.loads(Path(candidate).read_text())
    if not artifact_valid(artifact, league_id=league_id, listing_type=listing_type):
        raise ValueError("Freigabe verweigert: Kontext, Modell oder Zukunftsprüfung ungültig.")
    if datetime.fromisoformat(artifact["created_at"]) > datetime.now(UTC):
        raise ValueError("Kandidat stammt aus der Zukunft.")
    digest = hashlib.sha256(_canonical(artifact)).hexdigest()
    pointer = {
        "registry_schema_version": 1,
        "league_id": league_id,
        "listing_type": listing_type,
        "version": artifact["version"],
        "sha256": digest,
    }
    directory = Path(registry_dir)
    with _registry_lock(directory, context):
        active = directory / f"{context}.json"
        previous = json.loads(active.read_text()) if active.exists() else None
        if previous is not None:
            _resolve(previous, directory, league_id, listing_type)
            if previous == pointer:
                return pointer
        stored = directory / "candidates" / context / f"{artifact['version']}.json"
        if (
            stored.exists()
            and hashlib.sha256(_canonical(json.loads(stored.read_text()))).hexdigest() != digest
        ):
            raise ValueError(
                "Version existiert bereits mit anderem Inhalt; neuer Kandidat erforderlich."
            )
        _atomic(stored, artifact)
        if previous is not None:
            _atomic(directory / f"{context}-previous.json", previous)
        _atomic(active, pointer)
    return pointer


def rollback_competition(
    *,
    league_id: str,
    listing_type: str,
    registry_dir: str | Path = DEFAULT_REGISTRY_DIR,
    to_baseline: bool = False,
) -> dict:
    """Vorherige geprüfte Referenz laden oder ausdrücklich zu unbekannter Chance zurück."""
    context = _context(league_id, listing_type)
    directory = Path(registry_dir)
    with _registry_lock(directory, context):
        active = directory / f"{context}.json"
        if to_baseline:
            active.unlink(missing_ok=True)
            return {"version": "unknown", "active_loaded": False}
        previous = json.loads((directory / f"{context}-previous.json").read_text())
        _resolve(previous, directory, league_id, listing_type)
        _atomic(active, previous)
    return previous


def _passed_evaluation(artifact: dict) -> bool:
    """Gespeicherte Freigabe anhand der tatsächlichen Zukunftsmetriken nachprüfen."""
    try:
        for name in ("validation", "test"):
            score = artifact[name]
            if not (
                type(score["n"]) is int
                and score["n"] >= 20
                and type(score["won"]) is int
                and 0 < score["won"] < score["n"]
                and score["n"] == score["evaluated_inputs"]
            ):
                return False
            for key in ("brier", "baseline_brier", "calibration_gap"):
                value = score[key]
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                ):
                    return False
            if not (score["brier"] < score["baseline_brier"] and score["calibration_gap"] <= 0.15):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def load_competition(league_id: str, listing_type: str, *, registry_dir=DEFAULT_REGISTRY_DIR):
    """Nur aktivierte, zum Kontext passende Artefakte; kaputte Dateien bleiben wirkungslos."""
    if not league_id.isdigit() or listing_type not in {"kickbase", "manager"}:
        return None
    try:
        directory = Path(registry_dir)
        pointer = json.loads((directory / f"{league_id}-{listing_type}.json").read_text())
        return _resolve(pointer, directory, league_id, listing_type)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


@dataclass(frozen=True)
class BidObservation:
    event_id: str
    league_id: str
    at: datetime
    market_value: int
    amount: int
    position: int
    kind: Literal["upper", "lower", "exact"]
    listing_type: Literal["kickbase", "manager"] = "kickbase"
    # Historische Tages-MW sind Kontextrekonstruktion, keine Tatzeitprognose.
    source: str = "confirmed_receipt"
    prospective: bool = False

    def valid(self) -> bool:
        return (
            bool(self.event_id and self.league_id)
            and self.at.tzinfo is not None
            and type(self.amount) is int
            and self.amount > 0
            and type(self.market_value) is int
            and self.market_value > 0
            and type(self.position) is int
            and self.position in {1, 2, 3, 4}
            and self.kind in {"upper", "lower", "exact"}
            and self.listing_type in {"kickbase", "manager"}
            and not (self.listing_type == "manager" and self.kind == "exact")
            and self.source in {"confirmed_receipt", "confirmed_rival_auction", "archived_quote"}
        )


def _features(market_value: int, position: int) -> list[float]:
    # Wenige gemeinsame Gewichte; kein unbelegtes eigenes Modell je Manager.
    return [
        1.0,
        math.log(market_value / 5_000_000),
        float(position == 1),
        float(position == 3),
        float(position == 4),
    ]


def _fit(rows: list[BidObservation]) -> dict:
    x = np.asarray([_features(r.market_value, r.position) for r in rows])
    y = np.log([r.amount / r.market_value for r in rows])
    upper = np.array([r.kind == "upper" for r in rows])
    lower = np.array([r.kind == "lower" for r in rows])
    exact = ~(upper | lower)

    def objective(weights):
        sigma = math.exp(weights[-1])
        z = (y - x @ weights[:-1]) / sigma
        ll = np.empty(len(rows))
        ll[upper] = log_ndtr(z[upper])
        ll[lower] = log_ndtr(-z[lower])
        ll[exact] = -0.5 * z[exact] ** 2 - weights[-1]
        return float(-ll.sum() + 2 * np.square(weights[1:-1]).sum())

    start = np.zeros(6)
    start[-1] = math.log(0.3)
    result = minimize(
        objective, start, method="L-BFGS-B", bounds=[(-3, 3)] * 5 + [(math.log(0.025), math.log(3))]
    )
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError("Wettbewerbsmodell hat keine stabile Lösung gefunden.")
    return {"weights": result.x[:-1].tolist(), "sigma": math.exp(result.x[-1])}


def predict_probability(
    artifact: dict,
    *,
    amount: int,
    market_value: int,
    position: int,
    league_id: str,
    listing_type: str,
    as_of: datetime,
    require_promoted: bool = True,
) -> float | None:
    """Nur geprüfte, zeitlich verfügbare und passende Modelle steuern Gebote."""
    try:
        if require_promoted:
            if (
                artifact.get("status") != "promoted"
                or artifact.get("eligible_for_promotion") is not True
                or not _passed_evaluation(artifact)
            ):
                return None
            created = datetime.fromisoformat(artifact["created_at"])
            if created.tzinfo is None or as_of.tzinfo is None or created > as_of:
                return None
        if artifact["league_id"] != league_id or artifact["listing_type"] != listing_type:
            return None
        trained = datetime.fromisoformat(artifact["trained_through"])
        if trained.tzinfo is None or as_of.tzinfo is None or trained >= as_of:
            return None
        if (
            type(amount) is not int
            or type(market_value) is not int
            or type(position) is not int
            or amount <= 0
            or market_value <= 0
            or position not in {1, 2, 3, 4}
        ):
            return None
        lower_mv, upper_mv = artifact["market_value_range"]
        if not lower_mv <= market_value <= upper_mv:
            return None
        weights = artifact["model"]["weights"]
        sigma = artifact["model"]["sigma"]
        if len(weights) != 5 or not all(math.isfinite(w) for w in weights):
            return None
        if not math.isfinite(sigma) or sigma <= 0:
            return None
        mu = sum(w * x for w, x in zip(weights, _features(market_value, position), strict=True))
        return float(ndtr((math.log(amount / market_value) - mu) / sigma))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def train_competition(
    observations: list[BidObservation],
    *,
    league_id: str,
    listing_type: str,
    as_of: datetime,
) -> dict:
    """Kandidat + ehrlicher zeitlicher Bericht. Kein automatisches Aktivieren.

    Preisbeobachtungen tragen zum Fit bei. Eine Freigabe braucht zusätzlich
    später aufgezeichnete echte Gewinn-/Verlustfälle; Gewinnerpreise allein
    validieren keine Zuschlagswahrscheinlichkeit unserer angebotenen Beträge.
    """
    if as_of.tzinfo is None:
        raise ValueError("Trainingszeitpunkt benötigt Zeitzone.")
    unique: dict[str, BidObservation] = {}
    for row in observations:
        if (
            row.valid()
            and row.at < as_of
            and row.league_id == league_id
            and row.listing_type == listing_type
        ):
            # Ein Ereignis bleibt ein Beispiel; widersprüchliche Duplikate ausschließen.
            if row.event_id in unique and unique[row.event_id] != row:
                raise ValueError(f"Widersprüchliches Auktionsereignis: {row.event_id}")
            unique[row.event_id] = row
    rows = sorted(unique.values(), key=lambda r: (r.at, r.event_id))
    serial = [{**asdict(r), "at": r.at.isoformat()} for r in rows]
    version = hashlib.sha256(json.dumps(serial, sort_keys=True).encode()).hexdigest()[:16]
    report = {
        "schema_version": 1,
        "version": f"competition-{version}",
        "status": "candidate",
        "league_id": league_id,
        "listing_type": listing_type,
        "created_at": as_of.isoformat(),
        "rows": len(rows),
        "rejected_or_duplicates": len(observations) - len(rows),
        "trained_through": rows[-1].at.isoformat() if rows else as_of.isoformat(),
        "eligible_for_promotion": False,
        "limitations": [
            "Gewinnerpreise sind beobachtete Höchstpreise, keine minimal nötigen Gebote.",
            "Ohne spätere echte eigene Ergebnisse keine bestätigte Zuschlagskalibrierung.",
            "Nur Preis und Position; historische Rollen- und Rivalenkonten fehlen.",
        ],
    }
    days = sorted({r.at.date() for r in rows})
    if len(rows) < 30 or len(days) < 6:
        return {**report, "reason": "Zu wenige getrennte Ereignisse oder Beobachtungstage."}
    validation_start = days[int(len(days) * 0.6)]
    test_start = days[int(len(days) * 0.8)]
    train = [r for r in rows if r.at.date() < validation_start]
    validation = [r for r in rows if validation_start <= r.at.date() < test_start]
    test = [r for r in rows if r.at.date() >= test_start]
    model = _fit(train)
    report.update(model=model, trained_through=train[-1].at.isoformat())
    report["algorithm_version"] = "censored-lognormal-v1-gate-v2"
    model_identity = {"data": version, "model": model, "algorithm": report["algorithm_version"]}
    report["version"] = (
        "competition-"
        + hashlib.sha256(json.dumps(model_identity, sort_keys=True).encode()).hexdigest()[:16]
    )
    report["market_value_range"] = [
        min(r.market_value for r in train),
        max(r.market_value for r in train),
    ]
    report["splits"] = {"train": len(train), "validation": len(validation), "test": len(test)}
    report["split_dates"] = {
        "validation_start": validation_start.isoformat(),
        "test_start": test_start.isoformat(),
    }
    binary_train = [r for r in train if r.kind != "exact"]
    base_rate = (sum(r.kind == "upper" for r in binary_train) + 1) / (len(binary_train) + 2)

    def score(part):
        checked = [r for r in part if r.kind != "exact" and r.prospective]
        predictions = [
            predict_probability(
                report,
                amount=r.amount,
                market_value=r.market_value,
                position=r.position,
                league_id=league_id,
                listing_type=listing_type,
                as_of=r.at,
                require_promoted=False,
            )
            for r in checked
        ]
        pairs = [(r, p) for r, p in zip(checked, predictions, strict=True) if p is not None]
        if not pairs:
            return {
                "n": 0,
                "evaluated_inputs": len(checked),
                "brier": None,
                "baseline_brier": None,
                "calibration_gap": None,
            }
        return {
            "n": len(pairs),
            "evaluated_inputs": len(checked),
            "brier": sum((p - (r.kind == "upper")) ** 2 for r, p in pairs) / len(pairs),
            "baseline_brier": sum((base_rate - (r.kind == "upper")) ** 2 for r, _ in pairs)
            / len(pairs),
            "calibration_gap": abs(sum(p - (r.kind == "upper") for r, p in pairs) / len(pairs)),
            "won": sum(r.kind == "upper" for r, _ in pairs),
        }

    report["validation"], report["test"] = score(validation), score(test)
    report["eligible_for_promotion"] = _passed_evaluation(report)
    report["reason"] = (
        "Zeitliche Mindestprüfung bestanden; Freigabe bleibt getrennt."
        if report["eligible_for_promotion"]
        else "Zukunftstest noch nicht ausreichend oder schlechter."
    )
    return report


def observations_from_actions(actions, *, as_of: datetime | None = None) -> list[BidObservation]:
    """Nur bestätigte neue Ergebnisse mit tatsächlich gespeichertem Tatzeit-Marktwert."""
    result = []
    cutoff = as_of or datetime.now(UTC)
    if cutoff.tzinfo is None:
        raise ValueError("Auswertungszeitpunkt benötigt Zeitzone.")
    for action in actions:
        data = action.payload or {}
        if not isinstance(data, dict):
            continue
        outcome, expectation = data.get("outcome") or {}, data.get("expectation") or {}
        if not isinstance(outcome, dict) or not isinstance(expectation, dict):
            continue
        if (
            action.status != "executed"
            or action.action_type != "bid"
            or action.dry_run
            or outcome.get("training_eligible") is not True
            or outcome.get("source") != "confirmed_receipt"
            or outcome.get("result") not in {"won", "lost"}
            or not isinstance(expectation.get("engine_version"), str)
            or not expectation["engine_version"].strip()
            or expectation.get("backfilled")
        ):
            continue
        try:
            observed = datetime.fromisoformat(expectation["observed_at"])
            settled = datetime.fromisoformat(outcome["settled_at"])
            created = action.created_at
            if created.tzinfo is None:  # SQLite-Datenbank speichert UTC ohne Zonenmarker.
                created = created.replace(tzinfo=UTC)
            event_id = outcome["event_id"]
            if (
                type(event_id) not in (str, int)
                or not str(event_id).strip()
                or observed.tzinfo is None
                or settled.tzinfo is None
                or not observed <= created <= settled <= cutoff
            ):
                continue
            observation = BidObservation(
                event_id=str(event_id),
                league_id=action.league_id,
                at=settled,
                market_value=expectation["market_value_eur"],
                amount=action.amount,
                position=expectation["position"],
                kind="upper" if outcome["result"] == "won" else "lower",
                listing_type=expectation["kind"],
                source="confirmed_receipt",
                prospective=True,
            )
            if observation.valid():
                result.append(observation)
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return result
