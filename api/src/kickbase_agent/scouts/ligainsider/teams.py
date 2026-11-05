"""Statische Team-Tabelle Kickbase ↔ LigaInsider (18 Clubs, einmal fest verdrahtet).

Team-Namen werden NICHT gefuzzt — es sind nur 18, sie sind stabil. Der Kickbase-
`tid` (Ganzzahl) mappt direkt auf den LigaInsider-Slug; kann nie an
„Bayern vs FC Bayern München" scheitern. Fuzzy-Matching bleibt Spielernamen
INNERHALB eines Teams vorbehalten (siehe matcher.py).

Beide Seiten gegen die Live-Quellen verifiziert (Kickbase `get_team_names` +
LigaInsider-Startseite/Ausfall-Seite, 19.08.2026).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiTeam:
    tid: str            # Kickbase team-id
    slug: str           # LigaInsider URL-Slug
    li_id: int          # LigaInsider numerische Team-id (Teil der URL)
    li_name: str        # LigaInsider Anzeigename (für Ausfall-Seiten-Titel)
    kb_name: str        # Kickbase Anzeigename (Referenz)

    @property
    def lineup_path(self) -> str:
        return f"/{self.slug}/{self.li_id}/"


# tid → LiTeam
TEAMS: dict[str, LiTeam] = {
    t.tid: t
    for t in (
        LiTeam("2", "fc-bayern-muenchen", 1, "FC Bayern München", "Bayern"),
        LiTeam("3", "borussia-dortmund", 14, "Borussia Dortmund", "Dortmund"),
        LiTeam("4", "eintracht-frankfurt", 3, "Eintracht Frankfurt", "Frankfurt"),
        LiTeam("5", "sc-freiburg", 18, "SC Freiburg", "Freiburg"),
        LiTeam("6", "hamburger-sv", 9, "Hamburger SV", "Hamburg"),
        LiTeam("7", "bayer-04-leverkusen", 4, "Bayer 04 Leverkusen", "Leverkusen"),
        LiTeam("8", "fc-schalke-04", 13, "FC Schalke 04", "Schalke"),
        LiTeam("9", "vfb-stuttgart", 12, "VfB Stuttgart", "Stuttgart"),
        LiTeam("10", "sv-werder-bremen", 2, "SV Werder Bremen", "Bremen"),
        LiTeam("13", "fc-augsburg", 21, "FC Augsburg", "Augsburg"),
        LiTeam("14", "tsg-hoffenheim", 10, "TSG Hoffenheim", "Hoffenheim"),
        LiTeam("15", "borussia-moenchengladbach", 5, "Borussia Mönchengladbach", "M'gladbach"),
        LiTeam("18", "1-fsv-mainz-05", 17, "1. FSV Mainz 05", "Mainz"),
        LiTeam("28", "1-fc-koeln", 15, "1. FC Köln", "Köln"),
        LiTeam("29", "sc-paderborn-07", 1249, "SC Paderborn 07", "Paderborn"),
        LiTeam("40", "1-fc-union-berlin", 1246, "1. FC Union Berlin", "Union Berlin"),
        LiTeam("43", "rb-leipzig", 1311, "RB Leipzig", "Leipzig"),
        LiTeam("77", "sv-07-elversberg", 1331, "SV 07 Elversberg", "Elversberg"),
    )
}
