"""Kontext-Serialisierung und Spieler-Anonymisierung fuer das Hugging-Face-Entscheidungsmodell.

Verhindert Markenname-Overfitting aus Vortrainingsgewichten (`Player_01`, `Club_A`) und liefert
vorab berechnete Euro-Leitplanken (-33-%-Dispo, Freitagsdeckung, Kapitalbindungs-Alter), damit
das Sprachmodell keine Kopfrechnungen mit Millionenbetraegen ausfuehren muss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

FRIDAY_MIN_BUFFER_EUR = 10_000
STALE_MANAGER_BID_HOURS = 18.0


@dataclass(frozen=True)
class PlayerSnapshot:
    """Kompakter Zustand eines Kader- oder Marktspielers fuer den Modell-Kontext."""

    player_id: str
    name: str
    club_id: str
    position: int
    market_value_eur: int
    change_24h_eur: int = 0
    change_3d_pct: float = 0.0
    avg_points: float = 0.0
    recent_minutes: tuple[int, ...] = ()
    lineup_prob: int | None = None
    listing_price_eur: int | None = None
    is_manager_listing: bool = False
    expires_in_hours: float | None = None
    is_trade_holding: bool = False
    news_summary: str = ""


@dataclass(frozen=True)
class OpenBidContext:
    """Laufendes Gebot inklusive Kapitalbindungs-Alter und optionaler Tausch-Deckung."""

    player_id: str
    amount_eur: int
    is_manager_listing: bool = False
    bid_age_hours: float = 0.0
    is_trade_bid: bool = False
    coupled_swap_out_id: str | None = None
    coupled_swap_proceeds_eur: int = 0
    target_market_value_eur: int = 0


@dataclass(frozen=True)
class ManagerContext:
    """Vollstaendiger Entscheidungs-Kontext vor dem Aufruf des Hugging-Face-Modells."""

    budget_eur: int
    team_value_eur: int
    overdraft_floor_eur: int
    trade_cap_eur: int
    friday_hours_left: float
    squad: tuple[PlayerSnapshot, ...]
    market: tuple[PlayerSnapshot, ...]
    open_bids: tuple[OpenBidContext, ...] = ()
    protected_ids: frozenset[str] = field(default_factory=frozenset)

    def after_bids_eur(self) -> int:
        """Kontostand nach Zuschlag aller aktuell offenen Gebote."""
        return self.budget_eur - sum(b.amount_eur for b in self.open_bids)

    def free_dispo_eur(self) -> int:
        """Verbleibende zinsfreie Kaufkraft bis zur -33-%-Kickbase-Dispo-Grenze."""
        return max(0, self.after_bids_eur() - self.overdraft_floor_eur)

    def trade_exposure_eur(self) -> int:
        """Aktuell im Kurzfrist-Handel gebundenes Kapital (Positionen + Handelsgebote)."""
        held = sum(p.market_value_eur for p in self.squad if p.is_trade_holding)
        bids = sum(b.amount_eur for b in self.open_bids if b.is_trade_bid)
        return held + bids

    def friday_coverage_eur(self) -> int:
        """Echte Freitagsdeckung bei Verkauf aller Handelsspieler und gekoppelten Swap-Abgaengen."""
        held_sale = sum(p.market_value_eur for p in self.squad if p.is_trade_holding)
        trade_bid_resale = sum(
            b.target_market_value_eur for b in self.open_bids if b.is_trade_bid
        )
        swap_sale = sum(b.coupled_swap_proceeds_eur for b in self.open_bids)
        return self.after_bids_eur() + held_sale + trade_bid_resale + swap_sale


@dataclass(frozen=True)
class AnonymizedBundle:
    """Anonymisiertes Prompt-Paket samt Rueckuebersetzung fuer die Nutzer-Kommunikation."""

    prompt_payload: dict[str, Any]
    alias_to_id: dict[str, str]
    id_to_alias: dict[str, str]
    id_to_name: dict[str, str]
    club_to_alias: dict[str, str]


def _club_alias(index: int) -> str:
    letters = []
    n = index
    while True:
        n, rem = divmod(n, 26)
        letters.append(chr(ord("A") + rem))
        if n == 0:
            break
        n -= 1
    return "Club_" + "".join(reversed(letters))


def _sanitize_news(text: str, id_to_name: dict[str, str], id_to_alias: dict[str, str]) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    for pid, full_name in id_to_name.items():
        alias = id_to_alias[pid]
        if full_name:
            cleaned = re.sub(re.escape(full_name), alias, cleaned, flags=re.IGNORECASE)
        parts = [p for p in full_name.split() if len(p) >= 4]
        for part in parts:
            cleaned = re.sub(rf"\b{re.escape(part)}\b", alias, cleaned, flags=re.IGNORECASE)
    return cleaned[:240]


def anonymize_context(ctx: ManagerContext) -> AnonymizedBundle:
    """Uebersetzt Kader, Markt und Gebote deterministisch in anonymisierte Modell-Symbole."""
    all_players = list(ctx.squad) + [
        p for p in ctx.market if p.player_id not in {s.player_id for s in ctx.squad}
    ]
    alias_to_id: dict[str, str] = {}
    id_to_alias: dict[str, str] = {}
    id_to_name: dict[str, str] = {}
    club_to_alias: dict[str, str] = {}

    for idx, p in enumerate(all_players, start=1):
        alias = f"Player_{idx:02d}"
        alias_to_id[alias] = p.player_id
        id_to_alias[p.player_id] = alias
        id_to_name[p.player_id] = p.name
        if p.club_id not in club_to_alias:
            club_to_alias[p.club_id] = _club_alias(len(club_to_alias))

    def _ser_player(p: PlayerSnapshot) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "alias": id_to_alias[p.player_id],
            "club": club_to_alias[p.club_id],
            "pos": p.position,
            "mv_eur": p.market_value_eur,
            "mv_24h_eur": p.change_24h_eur,
            "rise_3d_pct": round(p.change_3d_pct * 100.0, 2),
            "avg_pts": round(p.avg_points, 1),
            "recent_min": list(p.recent_minutes),
            "kb_prob": p.lineup_prob,
            "protected": p.player_id in ctx.protected_ids,
            "trade_holding": p.is_trade_holding,
        }
        if p.listing_price_eur is not None:
            entry["price_eur"] = p.listing_price_eur
            entry["listing_kind"] = "manager" if p.is_manager_listing else "kickbase"
            entry["expires_h"] = (
                round(p.expires_in_hours, 2) if p.expires_in_hours is not None else None
            )
        if p.news_summary:
            entry["news"] = _sanitize_news(p.news_summary, id_to_name, id_to_alias)
        return entry

    serialized_bids = []
    for b in ctx.open_bids:
        p_alias = id_to_alias.get(b.player_id, f"Bid_{b.player_id}")
        stale = b.is_manager_listing and b.bid_age_hours >= STALE_MANAGER_BID_HOURS
        serialized_bids.append(
            {
                "alias": p_alias,
                "amount_eur": b.amount_eur,
                "listing_kind": "manager" if b.is_manager_listing else "kickbase",
                "bid_age_hours": round(b.bid_age_hours, 1),
                "capital_lockup_warning": stale,
                "coupled_swap_proceeds_eur": b.coupled_swap_proceeds_eur,
            }
        )

    payload: dict[str, Any] = {
        "constraints": {
            "budget_eur": ctx.budget_eur,
            "team_value_eur": ctx.team_value_eur,
            "overdraft_floor_eur": ctx.overdraft_floor_eur,
            "after_bids_eur": ctx.after_bids_eur(),
            "free_interest_free_dispo_eur": ctx.free_dispo_eur(),
            "trade_cap_eur": ctx.trade_cap_eur,
            "trade_room_left_eur": max(0, ctx.trade_cap_eur - ctx.trade_exposure_eur()),
            "friday_coverage_eur": ctx.friday_coverage_eur(),
            "friday_min_buffer_eur": FRIDAY_MIN_BUFFER_EUR,
            "friday_hours_left": round(ctx.friday_hours_left, 1),
        },
        "squad": [_ser_player(p) for p in ctx.squad],
        "market": [_ser_player(p) for p in ctx.market],
        "open_bids": serialized_bids,
    }
    return AnonymizedBundle(
        prompt_payload=payload,
        alias_to_id=alias_to_id,
        id_to_alias=id_to_alias,
        id_to_name=id_to_name,
        club_to_alias=club_to_alias,
    )


def deanonymize_text(text: str, bundle: AnonymizedBundle) -> str:
    """Ersetzt `Player_XX`-Platzhalter im generierten Co-Manager-Text durch echte Spielernamen."""
    result = text
    for alias in sorted(bundle.alias_to_id, key=len, reverse=True):
        pid = bundle.alias_to_id[alias]
        real_name = bundle.id_to_name.get(pid, pid)
        result = result.replace(alias, real_name)
    return result
