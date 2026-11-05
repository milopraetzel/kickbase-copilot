"""Konkrete nächste Schritte aus beobachteten Vorhaben; löst keine Trades aus."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from kickbase_agent.decision.lineup import _FORMATIONS

POSITIONS = {1: "Torwart", 2: "Abwehr", 3: "Mittelfeld", 4: "Sturm"}


def _money(value: int) -> str:
    return f"{value:,.0f}".replace(",", ".") + " €"


def _time(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=UTC)
    except ValueError:
        return None


def build_action_plan(
    *, strategy: dict, buys: list[dict], sells: list[dict], intents: list[dict],
    bids: list[dict] | None, squad_rows: list[dict], market_rows: list[dict],
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Gebot, Planung und Erwerb bleiben getrennt; keine erfundene Kaufzusage.

    Marktpreise der Prüfliste sind Kostenuntergrenzen, keine Höchstgebote oder
    gesicherten Zuschlagspreise. Ein KB-Startelfsignal ersetzt keine Rollenprüfung.
    """
    generated = observed_at or _time(strategy.get("generated_at"))
    portfolio = strategy.get("portfolio") or {}
    deadline = strategy.get("deadline") or {}
    cash = portfolio.get("cash_after_all_bids_eur")
    counts = Counter(int(r.get("position") or 0) for r in squad_rows)
    formations = [
        {1: max(0, 1-counts[1]), 2: max(0, d-counts[2]),
         3: max(0, m-counts[3]), 4: max(0, s-counts[4])}
        for d, m, s in _FORMATIONS
    ]
    deficits = min(formations, key=lambda r: (sum(r.values()), tuple(r.values())))
    missing = sum(deficits.values())
    needs = ", ".join(f"{n} × {POSITIONS[p]}" for p, n in deficits.items() if n)
    steps: list[dict] = []
    used: set[str] = set()

    def add(title: str, detail: str, status: str, **extra: Any) -> None:
        steps.append({"title": title, "detail": detail, "status": status,
                      "action_label": "Vorhaben ansehen", "href": "legacy.html#zentrale",
                      **extra})

    for bid in bids or []:
        pid = str(bid.get("player_id") or "")
        if not pid or pid in used:
            continue
        used.add(pid)
        add(f"{bid.get('name') or pid}: Zuschlag prüfen",
            "Gebot ist offen. Erst nach bestätigtem Kauf als Verstärkung einplanen.",
            "Geboten", player_id=pid, amount_eur=bid.get("amount"))
    for intent in intents:
        if intent.get("status") not in {"pending", "planned", "approved"}:
            continue
        if intent.get("kind") != "bid":
            continue
        intent_deadline = _time(intent.get("deadline"))
        if intent.get("deadline") and (
            intent_deadline is None or generated is None or intent_deadline <= generated
        ):
            continue
        pid = str(intent.get("player_id") or "")
        if not pid or pid in used:
            continue
        used.add(pid)
        add(f"{intent.get('player_name') or pid}: Gebot vorbereiten",
            "Vorhaben angelegt; aktuelle Rolle und Gesamtfinanzierung vor Ausführung prüfen.",
            "Geplant", player_id=pid, amount_eur=intent.get("amount"),
            execute_at=intent.get("execute_at"))
    for buy in buys:
        pid = str(buy.get("pid") or "")
        if not pid or pid in used:
            continue
        used.add(pid)
        add(f"{buy.get('name') or pid}: Kaufvorschlag prüfen",
            "Vorschlag des Planers. Kein bestätigter Kauf und noch kein Zuschlag.",
            "Vorschlag", player_id=pid, amount_eur=buy.get("amount"))
        if len(steps) >= 3:
            break

    if missing:
        amount_text = _money(cash) if cash is not None else "noch unbekanntes Budget"
        funding = (f"Nach offenen Geboten verfügbar: {amount_text}. "
                   "Dies ist eine mögliche Formation. Alle Zugänge gemeinsam finanzieren.")
        add(f"Starter als bezahlbares Paket ergänzen: {needs}", funding,
            "Finanzierung prüfen" if cash is None or cash < 0 else "Nächster Schritt",
            essential=True)
        if not used:
            # Nur aktuelle, nicht eigene Angebote mit positivem Verfügbarkeitsbeleg.
            # Eine Auswahl ist eine Rechercheaufgabe, kein vom Executor genehmigtes Gebot.
            owned = {str(r.get("pid")) for r in squad_rows}
            candidates = []
            lock = _time(deadline.get("next_lock_at"))
            for row in market_rows:
                pid = str(row.get("pid") or "")
                pos = int(row.get("position") or 0)
                expires = _time(row.get("expires_at"))
                if (pid in owned or row.get("ours") or not deficits.get(pos)
                        or row.get("status") in {"out", "bench"}
                        or row.get("fitness_status") != 0 or row.get("kb_prob") not in {1, 2}
                        or row.get("listing_type") != "kickbase"
                        or expires is None or generated is None or lock is None
                        or not generated < expires <= lock):
                    continue
                price = max(int(row.get("mv") or 0), int(row.get("price") or 0))
                if price > 0:
                    candidates.append((price, row))
            affordable = [(price, row) for price, row in candidates
                          if cash is not None and price <= max(0, cash)]
            for price, row in sorted(affordable, key=lambda item: item[0])[:2]:
                add(f"{row['name']}: Startrolle und Preis prüfen",
                    f"{row.get('pos') or POSITIONS.get(int(row.get('position') or 0), '')} · "
                    f"Marktpreis ab {_money(price)}; KB-Startsignal vorhanden. "
                    "Frische Vereinsmeldung prüfen und ein finanzierbares Limit bestimmen.",
                    "Prüfliste", player_id=str(row["pid"]), reference_price_eur=price)
            if not affordable:
                add("Günstigere Starter und eine Umschichtung prüfen",
                    "Aktuell kein passender Starter mit belegter Angebotsfrist im freien Budget. "
                    "Neue Angebote prüfen und einen Kaderumbau inklusive Ersatzkosten rechnen.",
                    "Nächster Schritt")
    if cash is not None and cash < 0:
        add(f"{_money(-cash)} Finanzierung absichern",
            "Bei allen offenen Zuschlägen. Erlöse und den verbleibenden Kader zusammen prüfen.",
            "Offen", essential=True)
    if not steps:
        fallers = [r for r in squad_rows if not r.get("protected")
                   and isinstance(r.get("mv24"), (int, float)) and r["mv24"] < 0]
        if fallers:
            names = ", ".join(r.get("name", "") for r in fallers[:2])
            add(f"{names}: Halten gegen Ersatz rechnen",
                "Kursverlust, Kaufaufschlag und Nutzen für die ganze Elf vergleichen.",
                "Nächster Schritt")
        else:
            add("Startrollen für den nächsten Spieltag prüfen",
                "Aktuelle Mannschaft mit Vereinsmeldungen und bestätigten Einsätzen abgleichen.",
                "Nächster Schritt")
    lock_text = deadline.get("next_lock_label") or "der nächsten bestätigten Kaderfrist"
    add(f"Elf und Konto vor {lock_text} bestätigen",
        "Nach Zuschlägen Aufstellung speichern und nichtnegativen Kontostand prüfen.",
        "Zum Abschluss", essential=True)
    title = "Die nächsten Verstärkungen finanzieren" if missing else "Die Elf gezielt verbessern"
    if cash is not None and cash < 0:
        title = "Finanzierung und Verstärkungen zusammen lösen"
    essential = [s for s in steps if s.get("essential")]
    optional = [s for s in steps if not s.get("essential")][:max(0, 6-len(essential))]
    visible = [{k: v for k, v in s.items() if k != "essential"}
               for s in steps if s in essential or s in optional]
    return {"title": title,
            "summary": "Bestehende Vorhaben und nächste Prüfschritte für den nächsten Spieltag.",
            "status": "offen" if missing or cash is None or cash < 0 else "in_pruefung",
            "deadline": deadline, "steps": visible, "available_cash_eur": cash,
            "missing_positions": {POSITIONS[p]: n for p, n in deficits.items() if n},
            "observed_at": generated.isoformat() if generated else None,
            "proposals_are_not_executions": True}
