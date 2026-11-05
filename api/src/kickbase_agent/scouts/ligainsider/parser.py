"""HTML → strukturierte Daten (rein, netzfrei — testbar gegen echte Fixtures).

Zwei Seitentypen:
- Startelf-Seite eines Teams: `.stadium_container_area .player_name` = die
  voraussichtliche Startelf (Platz-Grafik).
- Zentrale Ausfall-Seite: pro Team `.leg_table_title` (Name) + `.small_table_holder`
  mit `.small_table_row` (Spalte1=Name, Spalte2=Grund).

LigaInsider rendert Desktop + Mobile doppelt ins DOM → Dedup per (Team, Name).
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag

from kickbase_agent.scouts.ligainsider.matcher import normalize
from kickbase_agent.scouts.ligainsider.models import InjuryEntry, LineupEntry
from kickbase_agent.scouts.ligainsider.teams import TEAMS

_LI_NAME_TO_TID: dict[str, str] = {normalize(t.li_name): t.tid for t in TEAMS.values()}


def parse_lineup(html: str, team_tid: str) -> list[LineupEntry]:
    """Voraussichtliche Startelf eines Teams (nach Team-Seite-Fetch, tid bekannt)."""
    soup = BeautifulSoup(html, "html.parser")
    entries: list[LineupEntry] = []
    seen: set[str] = set()
    for el in soup.select(".stadium_container_area .player_name"):
        name = el.get_text(" ", strip=True)
        key = normalize(name)
        if name and key and key not in seen:
            seen.add(key)
            entries.append(LineupEntry(team_tid=team_tid, player_name=name))
    return entries


def _nearest_team_tid(holder: Tag) -> str | None:
    prev = holder.find_previous(class_="leg_table_title")
    if isinstance(prev, Tag):
        return _LI_NAME_TO_TID.get(normalize(prev.get_text(" ", strip=True)))
    return None


def parse_injuries(html: str) -> list[InjuryEntry]:
    """Alle Ausfälle der Liga von der zentralen Ausfall-Seite (1 Fetch)."""
    soup = BeautifulSoup(html, "html.parser")
    entries: list[InjuryEntry] = []
    seen: set[tuple[str, str]] = set()
    for holder in soup.select(".small_table_holder"):
        if not isinstance(holder, Tag):
            continue
        tid = _nearest_team_tid(holder)
        if tid is None:
            continue  # Sektionskopf o.Ä. — kein Team → überspringen
        for row in holder.select(".small_table_row"):
            if row.select_one(".small_table_column_noentry"):
                continue  # „Derzeit keine verletzten oder gesperrten"
            name_el = row.select_one(".small_table_column1")
            if not name_el:
                continue
            name = name_el.get_text(" ", strip=True)
            reason_el = row.select_one(".small_table_column2")
            reason = reason_el.get_text(" ", strip=True) if reason_el else ""
            key = (tid, normalize(name))
            if name and key[1] and key not in seen:
                seen.add(key)
                entries.append(
                    InjuryEntry(team_tid=tid, player_name=name, reason=reason or "Ausfall")
                )
    return entries
