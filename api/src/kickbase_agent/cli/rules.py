"""CLI-Befehle fuer das KI-Regelwerk und das interaktive 5-Schritte-Onboarding."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from kickbase_agent.agent.rules import load_agent_rules, save_agent_rules

console = Console()
app = typer.Typer(help="KI-Regelwerk anzeigen, per Preset setzen oder einzeln anpassen.")


@app.command("show")
def show_rules(
    as_json: bool = typer.Option(False, "--json", help="Als JSON ausgeben."),
) -> None:
    """Zeigt das aktuelle KI-Regelwerk an."""
    rules = load_agent_rules()
    if as_json:
        typer.echo(json.dumps(rules.to_dict(), ensure_ascii=False, indent=2))
        return

    table = Table(title="Aktuelles KI-Regelwerk (Web-App & KI-Chat synchron)")
    table.add_column("Regel", style="bold cyan")
    table.add_column("Wert", style="bold green")
    table.add_column("Erklaerung", style="dim")

    table.add_row("mode", rules.mode, "dry_run | veto_copilot | full_autopilot")
    table.add_row("allow_bids", str(rules.allow_bids), "Darf die KI Gebote abgeben?")
    table.add_row(
        "max_bid_million",
        f"{rules.max_bid_million:.1f} Mio. EUR",
        "Max. Einzelgebot ohne Rueckfrage",
    )
    table.add_row(
        "max_overpay_pct",
        f"+{rules.max_overpay_pct:.1f} %",
        "Max. Aufschlag ueber Marktwert",
    )
    table.add_row(
        "allow_sells",
        rules.allow_sells,
        "none | bench_only | swap_allowed",
    )
    table.add_row(
        "allow_mw_trading",
        str(rules.allow_mw_trading),
        "Unter-der-Woche Marktwert-Trading mit Steigern",
    )
    table.add_row(
        "trade_budget_cap_million",
        f"{rules.trade_budget_cap_million:.1f} Mio. EUR",
        "Max. Dispo-Rahmen fuer Trading-Spieler",
    )
    table.add_row(
        "friday_buffer_haircut_pct",
        f"{rules.friday_buffer_haircut_pct:.1f} % / Update",
        "Sicherheits-Puffer bis Freitag pro 22:00-Update",
    )
    table.add_row(
        "allow_lineup",
        str(rules.allow_lineup),
        "Startelf vor Anpfiff automatisch aufstellen",
    )
    table.add_row(
        "allow_friday_rescue",
        str(rules.allow_friday_rescue),
        "Freitagabend-Waechter: Konto vor Anpfiff >= 0 EUR",
    )
    table.add_row(
        "veto_minutes",
        f"{rules.veto_minutes} Min.",
        "Veto-Countdown im Cockpit vor Ausfuehrung",
    )
    table.add_row(
        "protected_players",
        ", ".join(rules.protected_players) or "(keine)",
        "Unantastbare Lieblingsspieler (nie verkaufen)",
    )
    console.print(table)


@app.command("set")
def set_rules(
    preset: str | None = typer.Option(
        None,
        "--preset",
        help="Schnell-Profil: dry_run | veto_copilot | full_autopilot",
    ),
    mode: str | None = typer.Option(None, "--mode", help="Autonomie-Modus"),
    allow_bids: bool | None = typer.Option(None, "--allow-bids/--no-bids"),
    max_bid_million: float | None = typer.Option(None, "--max-bid-million"),
    max_overpay_pct: float | None = typer.Option(None, "--max-overpay-pct"),
    allow_sells: str | None = typer.Option(None, "--allow-sells"),
    allow_mw_trading: bool | None = typer.Option(None, "--mw-trading/--no-mw-trading"),
    trade_budget_cap_million: float | None = typer.Option(
        None, "--trade-budget-cap"
    ),
    friday_buffer_haircut_pct: float | None = typer.Option(
        None, "--friday-haircut-pct"
    ),
    allow_lineup: bool | None = typer.Option(None, "--lineup/--no-lineup"),
    allow_friday_rescue: bool | None = typer.Option(
        None, "--friday-rescue/--no-friday-rescue"
    ),
    veto_minutes: int | None = typer.Option(None, "--veto-minutes"),
    protected_players: str | None = typer.Option(
        None, "--protected", help="Kommagetrennte Spielernamen oder IDs"
    ),
) -> None:
    """Aktualisiert das KI-Regelwerk (z. B. durch den KI-Chat oder Terminal)."""
    updates: dict[str, object] = {}
    if preset is not None:
        updates["preset"] = preset
    if mode is not None:
        updates["mode"] = mode
    if allow_bids is not None:
        updates["allow_bids"] = allow_bids
    if max_bid_million is not None:
        updates["max_bid_million"] = max_bid_million
    if max_overpay_pct is not None:
        updates["max_overpay_pct"] = max_overpay_pct
    if allow_sells is not None:
        updates["allow_sells"] = allow_sells
    if allow_mw_trading is not None:
        updates["allow_mw_trading"] = allow_mw_trading
    if trade_budget_cap_million is not None:
        updates["trade_budget_cap_million"] = trade_budget_cap_million
    if friday_buffer_haircut_pct is not None:
        updates["friday_buffer_haircut_pct"] = friday_buffer_haircut_pct
    if allow_lineup is not None:
        updates["allow_lineup"] = allow_lineup
    if allow_friday_rescue is not None:
        updates["allow_friday_rescue"] = allow_friday_rescue
    if veto_minutes is not None:
        updates["veto_minutes"] = veto_minutes
    if protected_players is not None:
        updates["protected_players"] = [
            p.strip() for p in protected_players.split(",") if p.strip()
        ]

    rules = save_agent_rules(updates, updated_by="cli_or_chat")
    console.print("[bold green]KI-Regelwerk gespeichert![/bold green]")
    typer.echo(json.dumps(rules.to_dict(), ensure_ascii=False, indent=2))


def onboarding_command() -> None:
    """Interaktives 5-Schritte-Onboarding: Fragt im Dialog ab, was die KI darf."""
    console.print(
        Panel.fit(
            "[bold cyan]Kickbase KI-Copilot — Interaktives Onboarding & Regelwerk[/bold cyan]\n"
            "Hier legst du in 5 kurzen Schritten fest, was dein Agent automatisch darf.\n"
            "Alle Antworten gelten sofort synchron im Web-Cockpit (Tab 'Regelwerk') und KI-Chat.",
            border_style="cyan",
        )
    )
    current = load_agent_rules()

    console.print(
        "\n[bold]Schritt 1/5 — Grund-Rolle des Agenten[/bold]\n"
        "  [cyan]1[/cyan] = Nur Berater (Dry-Run: KI rechnet alles, klickt nie in Kickbase)\n"
        "  [cyan]2[/cyan] = Co-Pilot mit Veto-Countdown (Vorhaben mit Countdown im Cockpit)\n"
        "  [cyan]3[/cyan] = Voll-Autopilot (Transfers, Trading, Elf & Dispo automatisch)"
    )
    choice = typer.prompt("Deine Wahl (1/2/3)", default="2").strip()
    preset = {
        "1": "dry_run",
        "2": "veto_copilot",
        "3": "full_autopilot",
    }.get(choice, "veto_copilot")

    updates: dict[str, object] = {"preset": preset}
    if preset != "dry_run":
        console.print("\n[bold]Schritt 2/5 — Gebote & Startelf-Schutz[/bold]")
        updates["max_bid_million"] = float(
            typer.prompt(
                "Bis zu wie viel Mio. EUR darf die KI maximal auf einen Spieler bieten?",
                default=18.0 if preset == "veto_copilot" else 50.0,
            )
        )
        sell_choice = typer.prompt(
            "Verkaufs-Regel? (none = nie | bench_only = nur Bank | swap_allowed = Upgrade-Tausch)",
            default="bench_only" if preset == "veto_copilot" else "swap_allowed",
        ).strip()
        updates["allow_sells"] = sell_choice

        console.print("\n[bold]Schritt 3/5 — Unter-der-Woche Marktwert-Trading (Dispo)[/bold]")
        mw_ok = typer.confirm(
            "Darf die KI von Mo-Do Marktwert-Steiger auf Dispo kaufen und beim Kippen verkaufen?",
            default=True,
        )
        updates["allow_mw_trading"] = mw_ok
        if mw_ok:
            updates["trade_budget_cap_million"] = float(
                typer.prompt(
                    "Wie viel Mio. EUR darf maximal gleichzeitig in Trading-Spielern stecken?",
                    default=20.0 if preset == "veto_copilot" else 50.0,
                )
            )

        console.print("\n[bold]Schritt 4/5 — Spieltags-Vorbereitung (Freitagabend)[/bold]")
        updates["allow_lineup"] = typer.confirm(
            "Darf die KI vor Spieltagsbeginn automatisch die beste Startelf aufstellen?",
            default=True,
        )
        updates["allow_friday_rescue"] = typer.confirm(
            "Soll der Freitagabend-Waechter das Konto vor Anpfiff garantiert >= 0 EUR halten?",
            default=True,
        )

    console.print("\n[bold]Schritt 5/5 — Unantastbare Lieblingsspieler (Schutzliste)[/bold]")
    protected_input = typer.prompt(
        "Welche Spieler duerfen NIEMALS verkauft werden? (kommagetrennte Namen oder leer)",
        default=", ".join(current.protected_players),
    )
    updates["protected_players"] = [
        p.strip() for p in protected_input.split(",") if p.strip()
    ]

    save_agent_rules(updates, updated_by="interactive_onboarding")
    console.print("\n[bold green]Onboarding abgeschlossen! Dein Regelwerk ist aktiv:[/bold green]")
    show_rules(as_json=False)
