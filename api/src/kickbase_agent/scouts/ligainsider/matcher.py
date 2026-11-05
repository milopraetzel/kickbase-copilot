"""Name-Matching Kickbase ↔ LigaInsider — team-verankert, akzent-robust, fuzzy.

Regel 3 aus Emils Spec: Namen werden NIE global abgeglichen. Der Aufrufer filtert
zuerst nach Team (nur ~25 Kandidaten statt ~500) und ruft dann `best_match` mit den
Namen genau dieses Teams. Kein externer Fuzzy-Dep — `difflib` (stdlib) reicht.

Beispiel-Härtefall (live verifiziert): Kickbase „Minjae" ↔ LigaInsider „Minjae Kim"
→ Token-Teilmenge → Confidence ≥ 0.92 → über Schwelle → scharf.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def normalize(name: str) -> str:
    """Kleinschreibung, Akzente strippen, Satzzeichen/Bindestriche zu Leerzeichen.

    „Min-jae Kim" → „min jae kim"; „Ouédraogo" → „ouedraogo"; „Schwäbe" → „schwabe".
    """
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = stripped.lower().replace("ß", "ss")
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def similarity(a: str, b: str) -> float:
    """Confidence 0..1 zwischen zwei Namen (bereits egal welcher Schreibweise).

    Exakt-normalisiert = 1.0. Teilmenge der Tokens (Kickbase-Nachname ⊂ LI-Vollname)
    = hoch. Sonst Zeichen-Ähnlichkeit (difflib). Ohne geteiltes Token bleibt der Wert
    niedrig, damit „Kim" nicht fälschlich auf „Kimmich" trifft.
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    ta, tb = set(na.split()), set(nb.split())
    ratio = SequenceMatcher(None, na, nb).ratio()

    if ta & tb:  # mindestens ein voll geteiltes Token (typischer Nachname)
        if ta <= tb or tb <= ta:      # ein Name ist Teilmenge des anderen
            return max(0.92, ratio)
        return max(0.85, ratio)
    return ratio


def best_match(kb_name: str, li_names: list[str]) -> tuple[int | None, float]:
    """Bester LI-Name für einen Kickbase-Namen INNERHALB eines Teams.

    Gibt (Index in li_names, Confidence) zurück; (None, 0.0) bei leerer Liste.
    """
    best_i: int | None = None
    best_score = 0.0
    for i, li_name in enumerate(li_names):
        s = similarity(kb_name, li_name)
        if s > best_score:
            best_i, best_score = i, s
    return best_i, best_score
