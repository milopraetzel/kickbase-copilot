"""Pydantic response schemas for the REST API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class LeagueOut(BaseModel):
    id: str
    name: str
    budget: int


class MeOut(BaseModel):
    user_id: str
    leagues: list[LeagueOut]


class PlayerOut(BaseModel):
    id: str
    first_name: str
    last_name: str
    position: int  # 1-4 (goal, def, mid, str)
    position_label: str
    team_id: str
    market_value: int
    market_value_trend: int
    points: int


class SquadPlayerOut(BaseModel):
    player: PlayerOut
    is_starter: bool


class SquadOut(BaseModel):
    league_id: str
    players: list[SquadPlayerOut]


class MarketPlayerOut(BaseModel):
    player: PlayerOut
    price: int
    expires_at: datetime


class MarketOut(BaseModel):
    league_id: str
    players: list[MarketPlayerOut]


class NewsArticleOut(BaseModel):
    source: str
    url: str
    title: str
    body: str
    published_at: datetime


class SignalOut(BaseModel):
    name: str
    score: float
    weight: float
    reason: str


class ExplosionCandidateOut(BaseModel):
    player_id: str
    name: str
    team_id: str
    position: int
    position_label: str
    current_market_value: int
    total_score: float
    signals: list[SignalOut]


class PlayerStoryOut(BaseModel):
    player_id: str
    headline: str
    narrative: str
    confidence: str


# --- Steuerzentrale: Request-Bodies der Intent-Endpunkte ----------------------


class IntentCreateIn(BaseModel):
    """Manuelles Vorhaben (MCP/Mandat/Emil) — Zwei-DB-Fix 24.08.: der lokale MCP führt
    Writes nicht mehr selbst aus, sondern legt sie hier als Vorhaben an; der EINZIGE
    Kickbase-Schreiber bleibt der Box-Worker (Intent-Executor, Tick */5)."""

    kind: str  # bid | list | sell | cancel_bid | delist
    player_id: str
    # bid/list: Pflicht > 0; sell: 0 = MW zur Ausführungszeit; cancel_bid/delist: 0
    amount: int = 0
    rationale: str
    player_name: str = ""
    team_id: str = ""
    position: int = 0
    listing_type: str = "kickbase"  # kickbase | manager
    delay_s: int = 0  # Veto-/Wartefenster in Sekunden ab jetzt (0 = nächster Tick)
    emil_go: str = ""
    # Kader-Bedarf (26.08.): markiert ein Gebot als „ohne ihn steht die Formation
    # nicht" — dann gilt im Executor-Preflight die Overpay-Kappe 135 % MW statt
    # 110 %. Landet in `sources.squad_need` und von dort in die PlannedAction.
    squad_need: bool = False
    via: str = "mcp"
    # Tausch-Bedingung (02.09., Emils Auftrag): ein Sofortverkauf wartet, bis der
    # Kader diese Größe erreicht hat („erst Ersatz, dann Abgang"). Nur für sell;
    # braucht eine Deadline (deadline_s > 0), sonst ist das Warten unbefristet.
    after_squad_size: int | None = None
    # Deadline in Sekunden ab jetzt (0 = keine) — für Vorhaben ohne Listing-Bezug,
    # deren Warten befristet sein muss (Tausch-Bedingung bis zum Anpfiff).
    deadline_s: int = 0
    # Gebots-Rückzug (02.09.): kind="cancel_bid" braucht die Offer-ID unseres
    # eigenen Gebots (own_bids.offer_id — Kickbase keyed Offers per User). Ein
    # Rückzug baut Risiko ab und braucht deshalb kein emil_go; er verbrennt aber
    # den Slot dieses Listings (Regel 4: nie zum Preisändern).
    offer_id: str = ""


class IntentVetoIn(BaseModel):
    """POST /api/intents/{id}/veto — optionale Notiz von Emil."""

    note: str | None = Field(default=None, max_length=2000)


class IntentAdjustIn(BaseModel):
    """POST /api/intents/{id}/adjust — genau EINS von `amount` (absolut, > 0) oder
    `pct` (relativ, −50 … +100; 10 = +10 %)."""

    amount: int | None = Field(default=None, gt=0)
    pct: float | None = Field(default=None, ge=-50, le=100)

    @model_validator(mode="after")
    def _exactly_one(self) -> IntentAdjustIn:
        if (self.amount is None) == (self.pct is None):
            raise ValueError("genau eines von 'amount' oder 'pct' angeben")
        return self


class LearningEvidenceIn(BaseModel):
    """POST /api/learning/evidence — ein Beleg, den der Agent beurteilt hat.

    `source` fehlt hier mit Absicht: Über diese Route entsteht IMMER ein
    Agenten-Beleg (`source=agent`). Emil soll im UI unterscheiden können, was
    gemessen und was beurteilt wurde — wäre die Quelle wählbar, wäre die
    Unterscheidung wertlos. Auto-Belege schreibt allein der Sammler-Job.

    `ref` ist Pflicht: Ohne eindeutige Ereignis-Referenz zählt dasselbe Ereignis
    bei jedem Lauf erneut. `note` ist Pflicht, weil ein Beleg ohne Beobachtung
    später nichts mehr belegt.
    """

    learning_id: str = Field(..., min_length=3, max_length=16)
    verdict: str  # confirm | refute
    note: str = Field(..., min_length=1, max_length=2000)
    ref: str = Field(..., min_length=1, max_length=200)
    observed_at: datetime | None = None
