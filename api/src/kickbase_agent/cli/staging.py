"""Staging-Environment Seeder (`kba staging-seed`).

Erzeugt ohne echten Kickbase-Login ein vollstaendiges, interaktives Staging-Cockpit
inkl. Demo-Kader, Transfermarkt, 4-Saeulen-Strategie, Jev-Analysen und Regelwerk
fuer Entwickler, Pull-Request-Tests und CI-Pipelines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from kickbase_agent.agent.rules import save_agent_rules
from kickbase_agent.cli.dashboard import write_dashboard


def build_staging_payload() -> dict[str, Any]:
    """Synthetischer, realistischer Bundesliga-Datensatz fuer die Staging-Umgebung."""
    squad = [
        {
            "pid": "st-1",
            "name": "Serhou Guirassy",
            "team": "Dortmund",
            "pos": "STU",
            "position": 4,
            "mv": 44_200_000,
            "mv24": 310_000,
            "ap": 146,
            "punkte": 1610,
            "status": "starter",
            "elf": True,
            "score": 0.91,
        },
        {
            "pid": "st-2",
            "name": "Jonathan Burkardt",
            "team": "Frankfurt",
            "pos": "STU",
            "position": 4,
            "mv": 31_400_000,
            "mv24": 245_000,
            "ap": 128,
            "punkte": 1410,
            "status": "starter",
            "elf": True,
            "score": 0.87,
        },
        {
            "pid": "mf-1",
            "name": "Aleix Garcia",
            "team": "Leverkusen",
            "pos": "MIT",
            "position": 3,
            "mv": 28_900_000,
            "mv24": 190_000,
            "ap": 124,
            "punkte": 1360,
            "status": "starter",
            "elf": True,
            "score": 0.85,
        },
        {
            "pid": "mf-2",
            "name": "Felix Nmecha",
            "team": "Dortmund",
            "pos": "MIT",
            "position": 3,
            "mv": 19_500_000,
            "mv24": 160_000,
            "ap": 108,
            "punkte": 1190,
            "status": "starter",
            "elf": True,
            "score": 0.80,
        },
        {
            "pid": "mf-3",
            "name": "Can Uzun",
            "team": "Frankfurt",
            "pos": "MIT",
            "position": 3,
            "mv": 14_800_000,
            "mv24": 220_000,
            "ap": 99,
            "punkte": 1090,
            "status": "starter",
            "elf": True,
            "score": 0.78,
        },
        {
            "pid": "mf-4",
            "name": "Maximilian Eggestein",
            "team": "Freiburg",
            "pos": "MIT",
            "position": 3,
            "mv": 13_200_000,
            "mv24": 85_000,
            "ap": 94,
            "punkte": 1030,
            "status": "starter",
            "elf": True,
            "score": 0.75,
        },
        {
            "pid": "ab-1",
            "name": "Nico Schlotterbeck",
            "team": "Dortmund",
            "pos": "ABW",
            "position": 2,
            "mv": 34_600_000,
            "mv24": 210_000,
            "ap": 131,
            "punkte": 1440,
            "status": "starter",
            "elf": True,
            "score": 0.88,
        },
        {
            "pid": "ab-2",
            "name": "Jonathan Tah",
            "team": "Bayern",
            "pos": "ABW",
            "position": 2,
            "mv": 32_100_000,
            "mv24": 175_000,
            "ap": 122,
            "punkte": 1340,
            "status": "starter",
            "elf": True,
            "score": 0.84,
        },
        {
            "pid": "ab-3",
            "name": "Alphonso Davies",
            "team": "Bayern",
            "pos": "ABW",
            "position": 2,
            "mv": 27_400_000,
            "mv24": 150_000,
            "ap": 119,
            "punkte": 1310,
            "status": "starter",
            "elf": True,
            "score": 0.83,
        },
        {
            "pid": "ab-4",
            "name": "Ramy Bensebaini",
            "team": "Dortmund",
            "pos": "ABW",
            "position": 2,
            "mv": 16_900_000,
            "mv24": 95_000,
            "ap": 102,
            "punkte": 1120,
            "status": "starter",
            "elf": True,
            "score": 0.77,
        },
        {
            "pid": "tw-1",
            "name": "Noah Atubolu",
            "team": "Freiburg",
            "pos": "TW",
            "position": 1,
            "mv": 15_300_000,
            "mv24": 110_000,
            "ap": 104,
            "punkte": 1145,
            "status": "starter",
            "elf": True,
            "score": 0.79,
        },
        {
            "pid": "bk-1",
            "name": "Rocco Reitz",
            "team": "Gladbach",
            "pos": "MIT",
            "position": 3,
            "mv": 8_900_000,
            "mv24": 265_000,
            "ap": 78,
            "punkte": 860,
            "status": "bench",
            "elf": False,
            "score": 0.68,
        },
    ]
    market = [
        {
            "pid": "mk-1",
            "name": "Daniel Svensson",
            "team": "Dortmund",
            "pos": "ABW",
            "position": 2,
            "mv": 14_950_000,
            "mv24": 340_000,
            "ap": 112,
            "punkte": 1230,
            "hours_left": 5.5,
            "score": 0.86,
        },
        {
            "pid": "mk-2",
            "name": "Konrad Laimer",
            "team": "Bayern",
            "pos": "MIT",
            "position": 3,
            "mv": 21_300_000,
            "mv24": 280_000,
            "ap": 116,
            "punkte": 1275,
            "hours_left": 9.2,
            "score": 0.84,
        },
    ]
    return {
        "stand": "Staging-Demo · Live",
        "kickoff": "Fr 20:30",
        "budget": 6_450_000,
        "teamwert": sum(int(p["mv"]) for p in squad),
        "formation": "4-4-2",
        "kader": squad,
        "market": market,
        "bids": [{"name": "Daniel Svensson", "player_id": "mk-1", "amount": 15_450_000}],
        "ranking": [
            {
                "user_id": "u1",
                "name": "FC Taktik (Staging)",
                "rank": 1,
                "points": 14820,
                "me": True,
            },
            {"user_id": "u2", "name": "Pressing United", "rank": 2, "points": 14410, "me": False},
            {"user_id": "u3", "name": "Gegenpressing 09", "rank": 3, "points": 13990, "me": False},
        ],
        "fair": {"curve": {"a": 120000, "b": 1.15, "n": 240}, "cloud": squad + market},
        "jev": {
            "zahlen": {"urteile_7d": 24, "folgen_7d": 4},
            "bilanz": {"treffer_quote_pct": 89.2},
            "feed": [
                {
                    "player_id": "st-1",
                    "player": "Serhou Guirassy",
                    "kind": "einsatz",
                    "answer": "startet",
                    "p": 0.95,
                    "subject": "Abschlusstraining voll absolviert — sicher in der Startelf",
                    "source": "LigaInsider",
                }
            ],
        },
        "strategy": {"manager_profiles": []},
    }


def staging_seed_command(
    out: str = typer.Option(
        "/app/data/cockpit/index.html",
        "--out",
        help="Zielpfad fuer die gerenderte Staging-Cockpit-HTML.",
    ),
) -> None:
    """Erzeugt ein komplettes Staging-Cockpit + Sicherheits-Regelwerk ohne Live-Account."""
    target = Path(out)
    save_agent_rules({"preset": "veto_copilot"}, updated_by="staging_seed")
    payload = build_staging_payload()
    size = write_dashboard(payload, target)
    typer.echo(f"Staging-Cockpit erfolgreich erzeugt: {target} ({size:,} Bytes)")
