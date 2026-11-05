"""Domain errors for the Kickbase client."""

from __future__ import annotations


class KickbaseAuthError(Exception):
    """Kickbase hat Login oder Token abgelehnt (401)."""


class KickbaseDataError(Exception):
    """Kickbase-Antwort ohne erwartetes Pflichtfeld — Zustand UNBEKANNT.

    Wird statt eines stillen Defaults geworfen, wenn ein sicherheitskritisches
    Feld fehlt (z. B. der Kontostand `b` aus /me/budget): ein erfundenes „0 €"
    würde die T-15-Notbremse grundlos den halben Kader verkaufen lassen.
    """
