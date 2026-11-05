"""Git-Commits als „Systemänderungen" im Protokoll der Zentrale (Emil, 23.08. abends).

Der Agent ändert sich selbst (Commits, Deploys) — das Protokoll soll das zeigen,
ohne dass der Container ein Git-Repo braucht. Der DEPLOY erzeugt deshalb lokal
eine ``commits.json`` (JSON-Array, neueste zuerst) und legt sie ins Daten-Volume.

Exaktes Erzeugungs-Kommando (Feld-/Record-Trenner ␟/␞, damit Anführungszeichen
und Umbrüche in Subjects/Bodies das JSON nie zerbrechen)::

    cd "/Users/emil/Mein Claude /Kickbase-Agent" && \
      git log -60 --pretty=format:'%H%x1f%cI%x1f%s%x1f%b%x1e' | python3 -c '
    import json, sys
    rows = [r.strip("\\n").split("\\x1f") for r in sys.stdin.read().split("\\x1e") if r.strip()]
    print(json.dumps(
        [{"sha": f[0], "date": f[1], "subject": f[2],
          "body": f[3].strip() if len(f) > 3 else ""} for f in rows if len(f) >= 3],
        ensure_ascii=False, indent=1))' > api/commits.json

Auf die Box (beide Container teilen das Volume ``kba-data`` unter ``/app/data``)::

    scp "api/commits.json" root@203.0.113.10:/opt/kickbase-agent/api/commits.json
    ssh root@203.0.113.10 'docker cp /opt/kickbase-agent/api/commits.json \
      kickbase-api:/app/data/commits.json'

Pfadkette von ``load_commits``: Argument → Env ``KBA_COMMITS_PATH`` →
``/app/data/commits.json`` (Box) → ``<repo>/api/commits.json`` (lokal).
Fehlende/kaputte Datei ⇒ leere Liste — Commits sind Zusatzinfo, nie ein Blocker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

#: Env-Variable, die den Speicherort der commits.json überschreibt.
COMMITS_PATH_ENV = "KBA_COMMITS_PATH"

#: Standard-Kandidaten (Reihenfolge = Priorität): Box-Volume, dann lokales api/.
_API_DIR = Path(__file__).resolve().parents[3]
DEFAULT_CANDIDATES: tuple[Path, ...] = (
    Path("/app/data/commits.json"),
    _API_DIR / "commits.json",
)

#: Länge, auf die der Commit-Body im Protokoll gekürzt wird.
WHY_MAX_CHARS = 400
#: Kurz-SHA-Länge für den Quellen-Chip.
SHORT_SHA = 7
#: Git-Trailer, die im „Warum" nur Rauschen wären (Kleinschreibung, Präfix-Match).
_TRAILER_PREFIXES = ("co-authored-by:", "signed-off-by:", "reviewed-by:", "acked-by:")


def _why_from_body(body: str) -> str:
    """Commit-Body ohne Git-Trailer, auf WHY_MAX_CHARS gekürzt."""
    lines = [
        line
        for line in body.strip().splitlines()
        if not line.strip().lower().startswith(_TRAILER_PREFIXES)
    ]
    why = "\n".join(lines).strip()
    if len(why) > WHY_MAX_CHARS:
        why = why[: WHY_MAX_CHARS - 1].rstrip() + "…"
    return why


def _resolve_path(path: str | Path | None) -> Path | None:
    """Pfadkette auflösen: Argument → Env → erster existierender Default-Kandidat."""
    if path is not None:
        return Path(path)
    env = os.environ.get(COMMITS_PATH_ENV, "").strip()
    if env:
        return Path(env)
    for candidate in DEFAULT_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def load_commits(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Einträge aus ``commits.json``, neueste zuerst (sortiert nach ``date``).

    Je Eintrag: ``{"sha", "date" (ISO), "subject", "body"}``. Ungültige Einträge
    (kein dict, Pflichtfelder sha/date/subject fehlen) werden übersprungen;
    fehlende oder kaputte Datei ⇒ ``[]``.
    """
    file = _resolve_path(path)
    if file is None:
        return []
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    commits: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not all(item.get(k) for k in ("sha", "date", "subject")):
            continue
        commits.append(
            {
                "sha": str(item["sha"]),
                "date": str(item["date"]),
                "subject": str(item["subject"]),
                "body": str(item.get("body") or ""),
            }
        )
    # git log liefert neueste zuerst — sortieren macht es auch bei fremd
    # erzeugten/zusammengeführten Dateien deterministisch (stabil bei Gleichstand).
    commits.sort(key=lambda c: c["date"], reverse=True)
    return commits


def system_entries(limit: int = 30) -> list[dict[str, Any]]:
    """Commits als Protokoll-Einträge (kind ``system``) im Basisformat
    ``{kind, ts, title, why, sources, status, emil}`` — Vertrag mit
    ``GET /api/protocol`` (server/routes/intents.py) und dem Cockpit.

    - ``title``: „System: <subject>"
    - ``why``: Commit-Body (ohne Git-Trailer wie Co-Authored-By), gekürzt auf 400 Zeichen
    - ``sources``: ``{"commit": <sha[:7]>}``
    - ``status``: ``"deployt"``

    Einträge ohne parsebares ISO-Datum fliegen raus (der Protokoll-Mischer
    sortiert nach ``ts`` und darf daran nie scheitern).
    """
    from datetime import datetime

    entries: list[dict[str, Any]] = []
    for commit in load_commits()[: max(0, limit)]:
        try:
            ts = datetime.fromisoformat(commit["date"])
        except ValueError:
            continue
        if ts.tzinfo is None:
            continue  # %cI liefert immer einen Offset — naive Werte sind Fremddaten
        entries.append(
            {
                "kind": "system",
                "ts": ts.isoformat(),
                "title": f"System: {commit['subject']}",
                "why": _why_from_body(commit["body"]),
                "sources": {"commit": commit["sha"][:SHORT_SHA]},
                "status": "deployt",
                "emil": "",
            }
        )
    return entries
