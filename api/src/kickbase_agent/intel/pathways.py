"""„Wege vor uns": bedingte Pfade je offenem Gebot (Emil, 23.08. abends).

Reine, testbare Funktion — kein I/O. Die Route ``GET /api/pathways``
(server/routes/live.py) füttert sie aus dem /api/bids-Cache und der DB.

Je offenem Gebot entstehen ZWEI Pfade:

- Zuschlag:      Konto danach, Spielraum nach der 33-%-Regel (Konto − übrige
                 Gebote + ⅓ Teamwert; Teamwert wächst um den MARKTWERT des
                 Spielers — als Näherung dient der Listing-Preis, s. trading/
                 finance.py: der Kaufpreis zählt NIE), bei Konto < 0 der
                 Purge-Hinweis (Konto ≥ 0 beim Anpfiff, eiserne Regel 3).
- kein Zuschlag: Betrag wird frei + Planner-Kandidaten (geplante Kauf-Vorhaben
                 zuerst, dann die jüngsten Kauf-Kandidaten der Intents-Tabelle).

Dazu die Extremfälle „Alle Gebote gewinnen" / „Alle Gebote verlieren" mit
Konto-Endstand. Ohne offene Gebote gibt es genau einen Ruhe-Pfad.

Pfad-Format (Vertrag mit der Web-App)::

    {"kind": "win" | "lose" | "all_win" | "all_lose" | "idle",
     "player_id": str | None, "player_name": str | None,
     "if": str, "then": [str, ...],
     "konto_eur": int, "konto_fmt": str}
"""

from __future__ import annotations

from typing import Any

from kickbase_agent.mcp._util import fmt_money, parse_int

#: Höchstzahl an Kandidaten-Namen in der „kein Zuschlag"-Zeile.
MAX_CANDIDATE_NAMES = 5


def _amount(bid: dict[str, Any]) -> int:
    return parse_int(bid.get("amount_eur", bid.get("amount")))


def _listing_price(bid: dict[str, Any]) -> int:
    return parse_int(bid.get("listing_price_eur", bid.get("listing_price")))


def _name(bid: dict[str, Any]) -> str:
    return str(bid.get("name") or bid.get("player_name") or "?").strip() or "?"


def _mv_proxy(bid: dict[str, Any]) -> int:
    """Teamwert-Zuwachs beim Zuschlag: Marktwert-Näherung = Listing-Preis
    (Kickbase listet zum MW), zur Not der Gebotsbetrag. Nie der Kaufpreis-Overpay."""
    return _listing_price(bid) or _amount(bid)


def _candidate_line(
    planned_intents: list[dict[str, Any]] | None, candidates: list[str] | None
) -> str:
    """Geplante Kauf-Vorhaben zuerst, dann jüngste Kandidaten — dedupliziert, max 5."""
    names: list[str] = []
    seen: set[str] = set()
    pools: list[str] = [
        str(p.get("player_name") or "").strip()
        for p in planned_intents or []
        if p.get("kind") == "bid"
    ]
    pools.extend(str(c or "").strip() for c in candidates or [])
    for name in pools:
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= MAX_CANDIDATE_NAMES:
            break
    if not names:
        return "Planner-Kandidaten: derzeit keine"
    return "Planner-Kandidaten: " + ", ".join(names)


def _purge_step(konto: int) -> str:
    return (
        f"Purge-Pfad: Verkäufe nötig bis ≥ 0 vor Anpfiff (Fehlbetrag {fmt_money(-konto)})"
    )


def _path(
    kind: str,
    condition: str,
    steps: list[str],
    konto: int,
    player_id: str | None = None,
    player_name: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "player_id": player_id,
        "player_name": player_name,
        "if": condition,
        "then": steps,
        "konto_eur": konto,
        "konto_fmt": fmt_money(konto),
    }


def build_pathways(
    bids: list[dict[str, Any]],
    budget_eur: int,
    team_value_eur: int,
    planned_intents: list[dict[str, Any]] | None = None,
    candidates: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Bedingte Pfade aus offenen Geboten + Kontolage (Format s. Modul-Docstring).

    ``bids``: Zeilen im /api/bids-Format (``amount_eur``/``listing_price_eur``;
    tolerant auch ``amount``/``listing_price``). ``planned_intents``: geplante
    Vorhaben als dicts mit ``kind``/``player_name``. ``candidates``: Namen der
    jüngsten Kauf-Kandidaten (Intents-Historie).
    """
    budget = int(budget_eur)
    team_value = int(team_value_eur)
    kandidaten = _candidate_line(planned_intents, candidates)
    open_bids = [b for b in bids or [] if isinstance(b, dict)]

    if not open_bids:
        kaufkraft = budget + team_value // 3
        return [
            _path(
                "idle",
                "Keine offenen Gebote",
                [
                    f"Konto {fmt_money(budget)}",
                    f"Kaufkraft {fmt_money(kaufkraft)} (33-%-Regel)",
                    kandidaten,
                ],
                budget,
            )
        ]

    exposure_total = sum(_amount(b) for b in open_bids)
    pathways: list[dict[str, Any]] = []

    for bid in open_bids:
        amount = _amount(bid)
        name = _name(bid)
        player_id = str(bid.get("player_id") or "") or None

        # Pfad 1: Zuschlag — Konto sinkt um den Kaufpreis, Teamwert steigt nur
        # um den Marktwert (Proxy: Listing-Preis), übrige Gebote binden weiter.
        konto_win = budget - amount
        exposure_other = exposure_total - amount
        team_after = team_value + _mv_proxy(bid)
        spielraum = konto_win - exposure_other + team_after // 3
        steps_win = [
            f"Konto → {fmt_money(konto_win)}",
            f"Spielraum → {fmt_money(spielraum)} (33-%-Regel, übrige Gebote gebunden)",
        ]
        if konto_win < 0:
            steps_win.append(_purge_step(konto_win))
        pathways.append(
            _path(
                "win",
                f"Zuschlag {name} ({fmt_money(amount)})",
                steps_win,
                konto_win,
                player_id,
                name,
            )
        )

        # Pfad 2: kein Zuschlag — der Betrag wird frei, der Planner rückt nach.
        pathways.append(
            _path(
                "lose",
                f"Kein Zuschlag {name}",
                [f"{fmt_money(amount)} frei", kandidaten],
                budget,
                player_id,
                name,
            )
        )

    # Extremfälle über alle Gebote (Konto-Endstand).
    konto_all_win = budget - exposure_total
    team_all_win = team_value + sum(_mv_proxy(b) for b in open_bids)
    spielraum_all_win = konto_all_win + team_all_win // 3
    steps_all_win = [
        f"Konto → {fmt_money(konto_all_win)}",
        f"Spielraum → {fmt_money(spielraum_all_win)} (33-%-Regel)",
    ]
    if konto_all_win < 0:
        steps_all_win.append(_purge_step(konto_all_win))
    pathways.append(
        _path(
            "all_win",
            f"Alle Gebote gewinnen ({len(open_bids)})",
            steps_all_win,
            konto_all_win,
        )
    )
    pathways.append(
        _path(
            "all_lose",
            "Alle Gebote verlieren",
            [
                f"Konto bleibt {fmt_money(budget)}",
                f"{fmt_money(exposure_total)} Kaufkraft wieder frei",
                kandidaten,
            ],
            budget,
        )
    )
    return pathways
